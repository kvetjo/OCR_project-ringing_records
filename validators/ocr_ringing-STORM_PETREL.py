"""
OCR Ringing Validator
=====================
Validates and enriches OCR results for bird ringing data (ocr_ringing).

Validace (zapínatelné/vypínatelné v config.yaml pod klíčem validation.ocr_ringing.rules):
  - species_match   : rapidfuzz napárování druhu na lexikon
  - wing_range      : kontrola délky křídla dle lexikonu ± tolerance
  - weight_range    : kontrola hmotnosti dle lexikonu ± tolerance

Obohacení záznamu (pokud species_match uspěje):
  - OfficialName    : oficiální název druhu z lexikonu
  - EuringCode      : EURING kód druhu z lexikonu

Výstupní soubory (vráceny jako List[Tuple[str, Any]]):
  - results.jsonl   : úspěšně zpracované záznamy
  - review.jsonl    : záznamy vyžadující ruční kontrolu
  - problems.jsonl  : záznamy s chybou parsování JSON
"""
from collections import Counter, defaultdict
import csv
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import unicodedata
from rapidfuzz import fuzz
from rapidfuzz import process as fuzz_process
import string



# Vzor se odvozuje jen z ring_no zapsaných na kartě v plném znění.
# Doplněné z prefixu nehlasují — jejich tvar je pouze kopií zdrojového řádku.
RING_NO_VOTE_SOURCE = "written"   # "written" | "all"

# Kolik zapsaných hodnot musí karta mít, aby hlasování něco znamenalo.
# Pod tímto počtem se uznají všechny nalezené tvary.
RING_NO_MIN_WRITTEN = 3

# Kolik hlasů musí tvar dostat, aby byl uznán za platnou sérii.
RING_NO_MIN_SUPPORT = 2

# ============================================================================
# STORM PETREL OVERRIDE - ring_no is kept as raw OCR text
# ============================================================================
# Storm Petrel cards use a different ring numbering convention, so ring_no must
# survive this validator exactly as the OCR read it: no prefix completion, no
# carry-over, no pattern voting, no validation, no character stripping.
#
# This flag overrides config.yaml for the listed fields ONLY. Every other field
# keeps its configured behaviour, so config.yaml needs no edit for this script.
# Set to False to fall back to the standard config-driven behaviour.
RING_NO_RAW_PASSTHROUGH = True
RING_NO_RAW_FIELDS = ("ring_no",)

# Ring continuity safety check in apply_backfill_species (_check_continuity).
# It compares the numeric tail of the last ring of the previous scan with the
# first ring of the current one, which only works when ring_no is prefix-completed.
# With raw pass-through above, suffix-only rows would raise false
# backfill_ring_discontinuity reviews, so the check is off here.
# Set to True to re-enable it. Species backfill itself is unaffected either way.
RING_NO_CONTINUITY_CHECK = False

# ============================================================================
# INTERNAL BOB FUNCTION: Backfill Species
# ============================================================================


def merge_review_records(
    reviews: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Sloučí review záznamy se stejným (custom_id, id_row) do jednoho řádku
    s kombinovaným seznamem reasons.
    Záznamy bez id_row (např. wrong_row_count) jsou ponechány beze změny.
    """
    merged: Dict[tuple, Dict[str, Any]] = {}
    standalone: List[Dict[str, Any]] = []

    for review in reviews:
        custom_id = review.get("custom_id", "")
        species_entry = review.get("species_entry")
        id_row = species_entry.get("id_row") if isinstance(species_entry, dict) else None

        if id_row is None:
            standalone.append(review)
            continue

        key = (custom_id, id_row)
        if key in merged:
            existing = merged[key]["reasons"]
            for reason in review["reasons"]:
                if reason not in existing:
                    existing.append(reason)
        else:
            merged[key] = review

    return list(merged.values()) + standalone

def _parse_custom_id(cid: str) -> Tuple[Optional[str], Optional[int]]:
    """'img_1_2' → ('img_1', 2)  |  neznámý formát → (None, None)"""
    m = re.match(r'^(.+?)_(\d+)$', cid)
    return (m.group(1), int(m.group(2))) if m else (None, None)


def _numeric_tail(ring_no: str) -> Optional[int]:
    """'GML 012345' → 12345  |  'A-00345' → 345"""
    m = re.search(r'(\d+)\s*$', ring_no.strip())
    return int(m.group(1)) if m else None


def _collect_rings(record: Dict, ring_no_field: str) -> List[str]:
    """
    Sbírá čísla kroužků ze záznamu.
    Podporuje:  ring_no_field → str
                ring_no_field → List[str]
                ring_no_field → List[Dict]  (bere první str hodnotu z každého dict)
    """
    value = record.get(ring_no_field)
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                for v in item.values():
                    if isinstance(v, str) and v.strip():
                        out.append(v.strip())
                        break
        return out
    return []


def _check_continuity(
    prev_rings: List[str],
    curr_rings: List[str],
) -> Tuple[bool, str]:
    """
    Ověří: numeric_tail(fyzicky posledního kroužku prev) + 1 == numeric_tail(fyzicky prvního kroužku curr)
    Vrací: (ok, důvod_selhání)
    """
    if not prev_rings:
        return False, "předchozí sken neobsahuje čísla kroužků"
    if not curr_rings:
        return False, "aktuální sken neobsahuje čísla kroužků"

    last_ring_str = prev_rings[-1]
    first_ring_str = curr_rings[0]

    last = _numeric_tail(last_ring_str)
    first = _numeric_tail(first_ring_str)

    if last is None:
        return False, f"nelze číst číslo kroužku '{last_ring_str}'"
    if first is None:
        return False, f"nelze číst číslo kroužku '{first_ring_str}'"
        
    if last + 1 == first:
        return True, ""
        
    reason_why = f"expected …{last + 1} (prev last: …{last} from '{last_ring_str}'), but found …{first} from '{first_ring_str}'"
    return False, reason_why


# def _set_on_group(
#     records: List[Dict],
#     cid: str,
#     sp_field: str,
#     species: str,
#     source: str,
#     config: Dict[str, Any],
#     lexicon: List[Dict[str, Any]]
# ) -> None:
#     """Nastaví druh na VŠECHNY chybějící záznamy skupiny."""
#     verbose: Any = config.get("verbose", False)

#     # Match against lexicon
#     threshold = config.get("species_threshold", DEFAULT_SPECIES_THRESHOLD)
#     matched_entry, score = best_species_match(species, lexicon, threshold=threshold)

#     for r in records:
#         if r.get("custom_id") == cid:
#             items = r.get("data", [])
#             if not isinstance(items, list):
#                 items = [items]
                
#             for item in items:
#                 if isinstance(item, dict):
#                     current_sp = str(item.get(sp_field) or "").strip()
#                     if not current_sp or current_sp.lower() in ("null", "none"):
#                         item[sp_field] = species

#                         # Add lexicon enrichment if matched
#                         if matched_entry:
#                             item["OfficialName"] = matched_entry["official_name"]
#                             item["EuringCode"] = matched_entry["euring_code"]
#                             item["MatchScore"] = round(score, 2)


def _set_on_group(
    records: List[Dict],
    cid: str,
    sp_field: str,
    species: str,
    source: str,
    config: Dict[str, Any],
    lexicon: List[Dict[str, Any]],
    serial_lexicon: Optional[Dict[str, str]] = None
) -> List[Dict[str, Any]]:
    """
    Nastaví druh na VŠECHNY chybějící záznamy skupiny.

    Zaroven na kazdy takto obohaceny zaznam aplikuje pravidla wing_range /
    weight_range (pokud jsou v configu zapnuta) - stejnou logikou jako
    enrich_record (viz validate_wing_weight_rule), protoze pred backfillem
    nebyl druh znamy a tato kontrola se tedy jeste nemohla provest.

    Vraci seznam review zaznamu vzniklych z neplatnych mereni (wing/weight).
    """
    verbose: Any = config.get("verbose", False)
    rule_config: Dict[str, bool] = config.get("rules", {})
    measurement_tolerance: float = float(
        config.get("measurement_tolerance", DEFAULT_MEASUREMENT_TOLERANCE)
    )

    # Match against lexicon
    threshold = config.get("species_threshold", DEFAULT_SPECIES_THRESHOLD)
    matched_entry, score = best_species_match(species, lexicon, threshold=threshold)

    now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    measurement_reviews: List[Dict[str, Any]] = []

    for r in records:
        if r.get("custom_id") == cid:
            items = r.get("data", [])
            if not isinstance(items, list):
                items = [items]
                
            for item in items:
                if isinstance(item, dict):
                    current_sp = str(item.get(sp_field) or "").strip()
                    if not current_sp or current_sp.lower() in ("null", "none"):
                        item[sp_field] = species

                        # Add lexicon enrichment if matched
                        if matched_entry:
                            item["OfficialName"] = matched_entry["official_name"]
                            item["EuringCode"] = matched_entry["euring_code"]
                            item["MatchScore"] = round(score, 2)
                            item["allowed_wing_range"] = format_allowed_range(
                                matched_entry.get("wing_range"), measurement_tolerance, "mm"
                            )
                            item["allowed_weight_range"] = format_allowed_range(
                                matched_entry.get("weight_range"), measurement_tolerance, "g"
                            )

                        # --- Wing/weight rule (sdileno s enrich_record) ---
                        raw_ringing = item.get("ringing", [])
                        if isinstance(raw_ringing, dict):
                            ringing_entries = [raw_ringing]
                        elif isinstance(raw_ringing, list):
                            ringing_entries = raw_ringing
                        else:
                            ringing_entries = []

                        item_reasons: List[str] = []
                        for ring_entry in ringing_entries:
                            if isinstance(ring_entry, dict):
                                item_reasons.extend(
                                    validate_wing_weight_rule(
                                        ring_entry, matched_entry, rule_config, measurement_tolerance
                                    )
                                )
                        

                        # --- Lexicon SERIAL validation ---
                        item_reasons.extend(
                            validate_serial_with_dict_rule(item, rule_config, serial_lexicon or {})
                        )

                        

                        if item_reasons:
                            measurement_reviews.append({
                                "custom_id": cid,
                                "batch_id": r.get("batch_id", ""),
                                "species_entry": item,
                                "reasons": item_reasons,
                                "timestamp": now_str,
                            })

                            if str(verbose) in ("True", "true"):
                                print(f"[BOB/backfill_species] ⚠  {cid}: {item_reasons} (species z {source})")

    return measurement_reviews


def _tag_group(records: List[Dict], cid: str, field: str, value: Any) -> None:
    for r in records:
        if r.get("custom_id") == cid:
            r[field] = value


def apply_backfill_species(
    all_results: List[Dict[str, Any]],
    config: Dict[str, Any],
    lexicon: List[Dict[str, Any]],
    serial_lexicon: Optional[Dict[str, str]] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Apply backfill_species logic to all results.
    Returns (modified_results, review_records).
    """
    ringing_cfg = config.get("validation", {}).get("ocr_ringing", {})
    verbose: Any = ringing_cfg.get("verbose", False)
    mode: Any = ringing_cfg.get("backfill_species", False)
    if not mode:
        return all_results, []

    sp_field: str = "species"
    ring_field: str = "ring_no"

    records = all_results.copy()
    
    # Build species_by_scan and rings_by_scan
    species_by_scan: Dict[str, str] = {}
    rings_by_scan: Dict[str, List[str]] = defaultdict(list)
    
    for r in records:
        cid = r.get("custom_id", "")
        if not cid:
            continue
            
        items = r.get("data", [])
        if not isinstance(items, list):
            items = [items]
        
        # Species
        valid_sp = ""
        for item in items:
            if isinstance(item, dict):
                val = str(item.get(sp_field) or "").strip()
                if val and val.lower() not in ("null", "none", "nan"):
                    valid_sp = val
                    break
        if valid_sp and cid not in species_by_scan:
            species_by_scan[cid] = valid_sp
        
        # Rings
        for item in items:
            if isinstance(item, dict):
                rings_by_scan[cid].extend(_collect_rings(item, ring_field))

    # Get unique custom_ids in order
    seen_cids: List[str] = list(dict.fromkeys(
        r.get("custom_id", "") for r in records if r.get("custom_id")
    ))

    review: List[Dict] = []
    modified: set = set()

    for cid in seen_cids:
        if species_by_scan.get(cid, "").strip():
            continue

        # Get batch_id and first_item for review
        batch_id = ""
        first_item = {}
        for r in records:
            if r.get("custom_id") == cid:
                batch_id = r.get("batch_id", "")
                items = r.get("data", [])
                if not isinstance(items, list):
                    items = [items]
                if items and isinstance(items[0], dict):
                    first_item = items[0]
                break
                
        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Fixed mode
        if mode != "AUTO":
            measurement_reviews = _set_on_group(records, cid, sp_field, mode, "config_fixed", config=ringing_cfg, lexicon=lexicon, serial_lexicon=serial_lexicon)
            review.extend(measurement_reviews)
            modified.add(cid)
            continue

        # AUTO mode
        prefix, page_num = _parse_custom_id(cid)
        if prefix is None:
            review.append({
                "custom_id": cid,
                "batch_id": batch_id,
                "species_entry": first_item,
                "reasons": ["backfill_unparseable_id"],
                "timestamp": now_str
            })
            continue

        prev_id = f"{prefix}_{page_num - 1}"
        source_species = species_by_scan.get(prev_id, "").strip()

        if not source_species:
            issue = (
                "backfill_no_prev_scan" if prev_id not in rings_by_scan
                else "backfill_prev_also_missing"
            )
            review.append({
                "custom_id": cid,
                "batch_id": batch_id,
                "species_entry": first_item,
                "reasons": [issue],
                "timestamp": now_str
            })
            continue

        # Safety check for ring continuity
        # STORM PETREL OVERRIDE: skipped when RING_NO_CONTINUITY_CHECK is False.
        if RING_NO_CONTINUITY_CHECK:
            ok, reason = _check_continuity(
                rings_by_scan.get(prev_id, []),
                rings_by_scan.get(cid, []),
            )
        else:
            ok, reason = True, ""

        measurement_reviews = _set_on_group(records, cid, sp_field, source_species, prev_id, config=ringing_cfg, lexicon=lexicon, serial_lexicon=serial_lexicon)
        review.extend(measurement_reviews)
        modified.add(cid)

        if not ok:
            _tag_group(records, cid, "_backfill_ring_warning", reason)
            
            if verbose in (True, "True", "true"):
                reason_details = [f"backfill_ring_discontinuity: {reason}"]
            else:
                reason_details = ["backfill_ring_discontinuity"]

            review.append({
                "custom_id": cid,
                "batch_id": batch_id,
                "species_entry": first_item,
                "reasons": reason_details,
                "timestamp": now_str
            })

            if str(verbose) == "True" or str(verbose) == "true":
                print(f"[BOB/backfill_species] ⚠  {cid} ← {prev_id}: {reason}")

    return records, review

# =============================================================================
# KONSTANTY
# =============================================================================

DEFAULT_SPECIES_THRESHOLD = 80.0      # minimální skóre shody druhu (0–100)
DEFAULT_MEASUREMENT_TOLERANCE = 0.10  # ± 10 % od hranic lexikonu
DEFAULT_LOCATION_THRESHOLD = 85.0

# =============================================================================
# LLM OUTPUT NORMALIZATION
# =============================================================================

def normalize_llm_output(parsed: Any) -> Dict[str, Any]:
    """Normalize LLM output to standard dict format with 'data' key."""
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {"data": parsed}
    raise ValueError(f"Unsupported LLM output type: {type(parsed)}")


# =============================================================================
# LEXIKON
# =============================================================================

def _parse_measurement_range(raw: str) -> Optional[Tuple[float, float]]:
    """
    Convert string from lexicon to tuple (min, max).

    Examples:
      '107-116 mm'  -> (107.0, 116.0)
      ' 450-680 g'  -> (450.0, 680.0)
      'unknown'     -> None   <- validace se preskoci
      ''            -> None
    """
    if not raw or raw.strip().lower() == "unknown":
        return None

    # Odstraníme jednotky (mm, g, cm…) a bílé znaky
    cleaned = re.sub(r"[a-zA-Z\s]", "", raw.strip())

    # Rozdělíme na min–max (en dash, em dash, nebo obyčejná pomlčka)
    parts = re.split(r"[–—\-]", cleaned)
    try:
        if len(parts) == 2 and parts[0] and parts[1]:
            return float(parts[0]), float(parts[1])
        if len(parts) == 1 and parts[0]:
            v = float(parts[0])
            return v, v
    except ValueError:
        pass

    return None

#----------locations------------
def normalize_text_loc(text: str) -> str:
    """Normalizuje text: odstraní diakritiku, převede na malá písmena a odstraní interpunkci."""
    if not text:
        return ""
    # Odstranění diakritiky
    text = unicodedata.normalize('NFKD', str(text)).encode('ASCII', 'ignore').decode('utf-8')
    # Převod na malá písmena
    text = text.lower()
    # Odstranění interpunkce
    text = text.translate(str.maketrans('', '', string.punctuation))
    # Odstranění přebytečných mezer
    return " ".join(text.split())





def best_location_match(
    place: str,
    lexicon: List[Dict[str, Any]],
    threshold: float = DEFAULT_LOCATION_THRESHOLD
) -> Optional[Dict[str, Any]]:
    """
    Napáruje název lokace ve třech krocích:
    1. Exact match na normalizovaný text.
    2. RapidFuzz s vysokou shodou.
    3. Pokud není shoda, vrátí None.
    """
    if not place or str(place).strip().lower() in ("null", "none", "", "nan"):
        return None

    norm_place = normalize_text_loc(str(place))
    if not norm_place:
        return None

    # Krok 1: Exact match na normalizovaný text
    for entry in lexicon:
        if norm_place in entry["normalized_search_names"]:
            return {"place": entry["location_name"], "id": entry["id"]}

    # Krok 2 & 3: RapidFuzz fallback
    flat_names = []
    flat_idx = []
    for idx, entry in enumerate(lexicon):
        for norm_name in entry["normalized_search_names"]:
            flat_names.append(norm_name)
            flat_idx.append(idx)

    if not flat_names:
        return None

    result = fuzz_process.extractOne(
        norm_place,
        flat_names,
        scorer=fuzz.WRatio,
        score_cutoff=threshold
    )

    if result:
        _matched_name, score, flat_pos = result
        matched_entry = lexicon[flat_idx[flat_pos]]
        return {"place": matched_entry["location_name"], "id": matched_entry["id"]}

    return None

#------- load lexicons-----------

def load_serial_lexicon(filepath: str) -> Dict[str, str]:
    """
    Loads serial options from a separate CSV file.
    Maps both euring code and species name to the options string.
    """
    serial_map: Dict[str, str] = {}
    try:
        with open(filepath, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                bird_name = row.get("bird_name", "").strip().lower()
                euring_code = row.get("spc_euringCode", "").strip()
                options = row.get("series_options", "").strip()
                
                if euring_code:
                    serial_map[euring_code] = options
                if bird_name:
                    serial_map[bird_name] = options
    except Exception as e:
        print(f"Error loading serial lexicon from {filepath}: {e}")
    return serial_map

def load_locations_lexicon(path: str) -> List[Dict[str, Any]]:
    lexicon: List[Dict[str, Any]] = []

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            id_key = next((k for k in row.keys() if k and k.strip() == 'ID'), None)
            name_key = next((k for k in row.keys() if k and k.strip() == 'Location_name'), None)
            alt_key = next((k for k in row.keys() if k and k.strip() == 'Alternative_names'), None)

            if not id_key or not name_key:
                continue

            val_id = row.get(id_key)
            val_name = row.get(name_key)
            val_alt = row.get(alt_key) if alt_key else None

            # Ošetření None a bezpečný strip()
            loc_id_raw = str(val_id).strip() if val_id is not None else ""
            loc_name = str(val_name).strip() if val_name is not None else ""
            alternates_raw = str(val_alt).strip() if val_alt is not None else ""

            if not loc_name:
                continue
            
            try:
                loc_id = int(loc_id_raw) if loc_id_raw else None
            except ValueError:
                loc_id = loc_id_raw

            sep = ";" if ";" in alternates_raw else ","
            alternates = [n.strip() for n in alternates_raw.split(sep) if n.strip()]

            search_names = [loc_name] + alternates
            normalized_search_names = [normalize_text_loc(n) for n in search_names if n]

            entry: Dict[str, Any] = {
                "id": loc_id,
                "location_name": loc_name,
                "search_names": search_names,
                "normalized_search_names": normalized_search_names
            }
            lexicon.append(entry)

    return lexicon


def load_species_lexicon(path: str) -> List[Dict[str, Any]]:
    """
    Nacte lexikon druhu z CSV souboru.

    Ocekavane sloupce (alespon):
        Euring_Code, Full_Official_Name, Alternate_Names,
        weight_range, wing_range
    """
    lexicon: List[Dict[str, Any]] = []

    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            official = row.get("Full_Official_Name", "").strip()
            alternates_raw = row.get("Alternate_Names", "").strip()

            # Alternate_Names může být odděleno čárkou nebo středníkem
            sep = ";" if ";" in alternates_raw else ","
            alternates = [n.strip() for n in alternates_raw.split(sep) if n.strip()]

            entry: Dict[str, Any] = {
                "euring_code":    row.get("Euring_Code", "").strip(),
                "official_name":  official,
                "alternate_names": alternates,
                "wing_range":     _parse_measurement_range(row.get("wing_range", "")),
                "weight_range":   _parse_measurement_range(row.get("weight_range", "")),
                # Vsechna jmena, pres ktera se vyhledava
                "search_names":   [official] + alternates,
            }
            lexicon.append(entry)

    return lexicon


# =============================================================================
# NAPAROVANI DRUHU
# =============================================================================

def best_species_match(
    species: str,
    lexicon: List[Dict[str, Any]],
    threshold: float = DEFAULT_SPECIES_THRESHOLD,
) -> Tuple[Optional[Dict[str, Any]], float]:
    """
    Napáruje název druhu na záznam v lexikonu pomocí rapidfuzz.

    Vraci (lex_entry, score) nebo (None, 0.0) pokud nic nevyhovuje prahu.
    Prochazi jak officialni, tak alternativni nazvy.
    """
    if not species:
        return None, 0.0

    # Flatten: paralelní seznamy jmen a indexů do lexikonu
    flat_names: List[str] = []
    flat_idx: List[int] = []
    for lex_idx, entry in enumerate(lexicon):
        for name in entry["search_names"]:
            flat_names.append(name.lower())
            flat_idx.append(lex_idx)

    result = fuzz_process.extractOne(
        species.lower(),
        flat_names,
        scorer=fuzz.WRatio,
        score_cutoff=threshold,
    )

    if result is None:
        return None, 0.0

    _matched_name, score, flat_pos = result
    return lexicon[flat_idx[flat_pos]], score


# =============================================================================
# VALIDACE MERENI
# =============================================================================

def validate_measurement(
    value: Any,
    lex_range: Optional[Tuple[float, float]],
    tolerance: float = DEFAULT_MEASUREMENT_TOLERANCE,
) -> Tuple[bool, Optional[str]]:
    """
    Overi, zda namerena hodnota spada do tolerovaneho rozsahu lexikonu.

    - lex_range is None -> vzdy OK (unknown v lexikonu)
    - value is None     -> vzdy OK (chybejici hodnota, preskocime)
    - jinak overi [min*(1-tol), max*(1+tol)]

    Vraci (is_valid, detail_chyby_nebo_None).
    """
    if lex_range is None:
        return True, None

    if value is None:
        return True, None

    try:
        val = float(value)
    except (ValueError, TypeError):
        return False, f"Neparsovatelna hodnota: '{value}'"

    lo, hi = lex_range
    adj_lo = lo * (1.0 - tolerance)
    adj_hi = hi * (1.0 + tolerance)

    if adj_lo <= val <= adj_hi:
        return True, None

    pct = int(tolerance * 100)
    return (
        False,
        (
            f"Hodnota {val} je mimo rozsah "
            f"[{adj_lo:.1f}-{adj_hi:.1f}] "
            f"(lexikon: {lo}-{hi}, +/-{pct} %)"
        ),
    )


def format_allowed_range(
    lex_range: Optional[Tuple[float, float]],
    tolerance: float,
    unit: str,
) -> Optional[str]:
    """
    Zformatuje povoleny rozsah (s toleranci) do citelneho retezce.

    Priklad: lex_range=(70, 80), tolerance=0.10, unit="mm"
             -> "63.0-88.0 mm"

    Vraci None pokud lex_range is None (unknown v lexikonu).
    """
    if lex_range is None:
        return None
    lo, hi = lex_range
    adj_lo = lo * (1.0 - tolerance)
    adj_hi = hi * (1.0 + tolerance)
    return f"{adj_lo:.1f}-{adj_hi:.1f} {unit}"

def validate_simple_serial_rule(
    item: Dict[str, Any],
    rule_config: Dict[str, bool]
) -> List[str]:
    """
    Validates the serial field against simple formatting rules.
    Checks for length > 3, presence of spaces, and ending with a digit.
    Returns a list of reason strings (empty = OK).
    """
    reasons: List[str] = []
    
    # Safeguard if called without the config flag enabled
    if not rule_config.get("serial_validation-simple", False):
        return reasons

    serial_value = item.get("serial")
    if serial_value is not None:
        serial_str = str(serial_value).strip()
        
        # Check all conditions: length > 3, contains whitespace, dash,dot or ends with a digit
        if (
            len(serial_str) > 3 
            or " " in serial_str 
            or "-" in serial_str
            or "." in serial_str
            or (len(serial_str) > 0 and serial_str[-1].isdigit())
        ):
            reasons.append("strange_serial")

    return reasons


def validate_serial_with_dict_rule(
    item: Dict[str, Any],
    rule_config: Dict[str, bool],
    serial_lexicon: Dict[str, str],
) -> List[str]:
    """
    Validates the serial field against the serial lexicon.
    Callable from both enrich_record and _set_on_group (backfill path).
    Returns list of reason strings (empty = OK).
    """
    reasons: List[str] = []
    if not rule_config.get("serial_validation", False) or not serial_lexicon:
        return reasons

    serial_val = item.get("serial")
    serial_val_clean = str(serial_val).strip() if serial_val is not None else ""
    empty_values = {"", "none", "null", "-", ".", "_", "nan"}
    AUTO_REPLACE_SINGLE_SERIAL = False # BETTER NOT TO USE

    euring_key = item.get("EuringCode")
    name_key = str(item.get("OfficialName", "")).strip().lower()


    options_raw = None
    if euring_key and euring_key in serial_lexicon:
        options_raw = serial_lexicon[euring_key]
    elif name_key and name_key in serial_lexicon:
        options_raw = serial_lexicon[name_key]

    if options_raw is None:
        return reasons

    options = [opt.strip() for opt in str(options_raw).split(";") if opt.strip()]
    if not options:
        return reasons

    is_match = False
    for opt in options:
        if opt.upper() == "NULL":
            if serial_val_clean.lower() in empty_values:
                is_match = True
                break
        else:
            if serial_val_clean.upper() == opt.upper():
                is_match = True
                break

    if not is_match and len(options) == 1 and AUTO_REPLACE_SINGLE_SERIAL:
        single_option = options[0]
        item["serial"] = None if single_option.upper() == "NULL" else single_option
        is_match = True

    if not is_match:
        reasons.append("not_allowed_serial")

    return reasons


def validate_wing_weight_rule(
    ring_entry: Dict[str, Any],
    matched_entry: Optional[Dict[str, Any]],
    rule_config: Dict[str, bool],
    measurement_tolerance: float = DEFAULT_MEASUREMENT_TOLERANCE,
) -> List[str]:
    """
    Obecna funkce pro pravidla wing_range / weight_range.

    Zkontroluje hodnoty 'wing' a 'weight' jednoho ringing-zaznamu (ring_entry)
    proti rozsahu z lexikonu (matched_entry), pokud je prislusne pravidlo
    zapnute v rule_config. Pouziva se jak v enrich_record (běžné zaznamy),
    tak v _set_on_group (zaznamy doplnene pres backfill_species) — takze
    se nikdy nerozejdou.

    - matched_entry is None -> zadna kontrola se neprovede (neni s cim
      srovnavat), vraci se prazdny seznam.
    - rule_config.get("wing_range")/("weight_range") == False -> dane
      pravidlo se preskoci.

    Vraci seznam duvodu, napr. ["wing_out_of_range", "weight_out_of_range"].
    Prazdny seznam = vse OK (nebo pravidlo neaktivni / nelze provest).
    """
    reasons: List[str] = []

    if not matched_entry:
        return reasons

    # ---- Wing ----------------------------------------------------------
    if rule_config.get("wing_range", False):
        lex_range = matched_entry.get("wing_range")
        valid, _detail = validate_measurement(
            ring_entry.get("wing"), lex_range, measurement_tolerance
        )
        if not valid:
            reasons.append("wing_out_of_range")

    # ---- Weight ----------------------------------------------------------
    if rule_config.get("weight_range", False):
        lex_range = matched_entry.get("weight_range")
        valid, _detail = validate_measurement(
            ring_entry.get("weight"), lex_range, measurement_tolerance
        )
        if not valid:
            reasons.append("weight_out_of_range")

    return reasons

# =============================================================================
# RING NO VALIDATION HELPERS
# =============================================================================

def build_pattern(ring_no: Any) -> str:
    """Derive a canonical, anchored regex from a ring_no value's structure."""
    s = str(ring_no)
    parts: List[str] = ["^"]
    i = 0
    while i < len(s):
        ch = s[i]
        if ch.isdigit():
            j = i + 1
            while j < len(s) and s[j].isdigit():
                j += 1
            parts.append(r"\d+")
            i = j
        elif ch.isalpha():
            j = i + 1
            while j < len(s) and s[j].isalpha():
                j += 1
            parts.append(r"[A-Za-z]+")
            i = j
        else:
            parts.append(re.escape(ch))
            i += 1
    parts.append("$")
    return "".join(parts)


def dominant_pattern(ring_nos: List[Any]) -> Tuple[Optional[str], Counter, set]:
    """Choose the most frequent canonical pattern from a list of ring_nos."""
    valid = [str(rn) for rn in ring_nos if rn is not None and str(rn).strip().lower() not in ("null", "none", "", "nan")]
    
    counter: Counter = Counter()
    suspicious_patterns: set[str] = set()

    if not valid:
        return None, counter, suspicious_patterns

    

    for rn in valid:
        pat = build_pattern(rn)

        #print(f"[Debug AUTO] Hodnota: '{rn:<10}' -> Vygenerován regex: {pat}")
        counter[pat] += 1
        
        
        # Detekce 2 a více po sobě jdoucích znaků, které NEJSOU písmena ani čísla
        if re.search(r'[^A-Za-z0-9]{2,}', rn):
            suspicious_patterns.add(pat)

    clean_candidates = {p: c for p, c in counter.items() if p not in suspicious_patterns}

    if clean_candidates:
        chosen = max(clean_candidates, key=lambda p: (clean_candidates[p], len(p)))
    else:
        # Fallback: Pokud jsou VŠECHNY záznamy podezřelé
        chosen = max(counter, key=lambda p: (counter[p], len(p)))

    return chosen, counter, suspicious_patterns

def correct_age_ocr(val):
    """
    Corrects common OCR errors for a single age value.
    Returns the corrected string or the original value if no match.
    """
    if val is None:
        return None
        
    val_str = str(val).strip().upper().replace(".", "")
    
    # THE ONLY PLACE YOU NEED TO EDIT:
    # Key = what OCR text might look like (error), Value = what it should be
    raw_ocr_fixes = {
        "15": "1J",
        "35": "3J",
        "25": "2J",
        "Pull": "1",
        "Pullus": "1",
        "Pul": "1",
        "H": "4" 
    }
    ocr_fixes = {k.upper(): v for k, v in raw_ocr_fixes.items()}

    # 1. If it's a known error, return the fixed version (e.g., "15" -> "1J")
    if val_str in ocr_fixes:
        return ocr_fixes[val_str]
        
    # 2. If it's already a correct target value (e.g., "1J"), return it as is
    if val_str in ocr_fixes.values():
        return val_str
        
    # 3. Otherwise, return the original value
    return val


# =============================================================================
# OBOHACENI A VALIDACE JEDNOHO ZAZNAMU
# =============================================================================

def enrich_record(
    record: Dict[str, Any],
    rule_config: Dict[str, bool],
    lexicon: List[Dict[str, Any]],
    species_threshold: float,
    measurement_tolerance: float,
    ring_no_pattern: Optional[str] = None,
    is_verbose = False,
    locations_lexicon: Optional[List[Dict[str, Any]]] = None,
    serial_lexicon: Optional[Dict[str, str]] = None,
    location_threshold: float = DEFAULT_LOCATION_THRESHOLD

) -> Tuple[Dict[str, Any], List[str]]:
    """
    Obohati a zvaliduje jeden zaznam z pole `data[]`.

    Matching druhu probiha VZDY (neda se vypnout v configu).
    Jednotliva pravidla v rule_config rizuji jen to, zda se problem
    zapise do review.

    Vraci (enriched_record, reasons).
    reasons je plochý seznam retezcu (napr. ["low_match_score", "wing_out_of_range"]).
    Prazdny seznam = vse OK, zadny review zaznam se nevygeneruje.
    """
    #enriched = dict(record)
    reasons: List[str] = []
    matched_entry: Optional[Dict[str, Any]] = None

    raw_species = record.get("species")
    
    if is_empty_value(raw_species):
        species = None
        record["species"] = None  # Rovnou přepíšeme i ve výsledném slovníku na pravý None (v JSONu null)
    else:
        species = str(raw_species).strip()
        record["species"] = species # Pokud je platný, uložíme ho čistý bez mezer




    #---------------------------------------------------------------
    #try to solve the problem

    raw_ringing = record.get("ringing", [])

    # 1. Normalizace dat do nové proměnné
    if isinstance(raw_ringing, dict):
        ringing_entries = [raw_ringing] # Zabalíme slovník do seznamu
    elif raw_ringing is None:
        ringing_entries = []
    elif isinstance(raw_ringing, list):
        ringing_entries = raw_ringing
    else:
        # Pojistka, kdyby tam přišel nějaký úplný nesmysl (např. string)
        ringing_entries = [] 

    # 2. KLÍČOVÝ KROK: Přepsání původní hodnoty v record!
    record["ringing"] = ringing_entries


    # raw_ringing = record.get("ringing", [])
    # if isinstance(raw_ringing, dict):
    #     ringing_entries: List[Dict[str, Any]] = [raw_ringing] # Zabalíme slovník do seznamu
    # elif raw_ringing is None:
    #     ringing_entries: List[Dict[str, Any]] = []
    # else:
    #     ringing_entries: List[Dict[str, Any]] = raw_ringing


#    ringing_entries: List[Dict[str, Any]] = record.get("ringing", [])
    # ------------------------------------------------------------------
    # 1) Naparovani druhu — VZDY se provede bez ohledu na rule_config
    # ------------------------------------------------------------------
    matched_entry, score = best_species_match(
        species, lexicon, threshold=species_threshold
    )

    if matched_entry:
        # Obohaceni — pridavame nova pole, nepреpisujeme puvodni
        record["OfficialName"] = matched_entry["official_name"]
        record["EuringCode"] = matched_entry["euring_code"]
        record["MatchScore"] = round(score, 2)

        

        # Povolene rozsahy z lexikonu (s toleranci) — pro zobrazeni v datech i review.
        wing_lex   = matched_entry.get("wing_range")
        weight_lex = matched_entry.get("weight_range")

        record.setdefault(
            "allowed_wing_range",
            format_allowed_range(wing_lex, measurement_tolerance, "mm"),
        )
        record.setdefault(
            "allowed_weight_range",
            format_allowed_range(weight_lex, measurement_tolerance, "g"),
        )

        # low_match_score: skore je pod prahem — volitelny review
        if rule_config.get("low_match_score", False):
            if score < species_threshold:
                reasons.append("sp_low_match_score")
    else:
        # Zadna shoda v lexikonu — vzdy zapiseme do review
        record.setdefault("OfficialName", None)
        record.setdefault("EuringCode",   None)
        record.setdefault("MatchScore",   0.0)

        record.setdefault("allowed_wing_range", None)
        record.setdefault("allowed_weight_range", None)
        
        
        # Převedeme na string, odstraníme mezery a převedeme na malá písmena
        species_str = str(species).strip().lower()
        # Pokud výsledek není prázdný a není to žádná z variant "nic"
        if species_str not in {"", "none", "null", "nan", "None"}:
            reasons.append("sp_low_match_score")


    # --- H) Ring number validation (it is not inside ringing (in json structure) - thats why it must be outside for cycle) ---
    # STORM PETREL OVERRIDE: ring_no is raw text, so no pattern check is run
    # even when ring_no_validation is enabled in config.yaml.
    if (not RING_NO_RAW_PASSTHROUGH) and rule_config.get("ring_no_validation", False) and ring_no_pattern:
        rn_val = record.get("ring_no")
        if rn_val is not None:
            ok = bool(re.match(ring_no_pattern, str(rn_val)))
            if not ok:

                if is_verbose: # turn on or turn off printing
                    inferred = build_pattern(str(rn_val))
                    print(f"  -> ERROR: '{rn_val}' ({inferred}) does not match pattern {ring_no_pattern}")
                reasons.append(f"invalid_ring_no")

            else:

                if is_verbose: # turn on or turn off printing
                    inferred = build_pattern(str(rn_val))
                    print(f"  -> OK: '{rn_val}' ({inferred}) matches pattern {ring_no_pattern}")
            # elif ring_no_mode == "AUTO":
            #     inferred = build_pattern(str(rn_val))
            #     if inferred != ring_no_pattern:
            #         reasons.append(f"ring_no_schema_mismatch ({inferred})")

    # --- F)  SERIAL validation ---
    if rule_config.get("serial_validation", False) and serial_lexicon:
        reasons.extend(validate_serial_with_dict_rule(record, rule_config, serial_lexicon))


    # --- G) simple SERIAL validation ---
    if rule_config.get("serial_validation-simple", False):
        reasons.extend(validate_simple_serial_rule(record, rule_config))
   

    # ------------------------------------------------------------------
    # 2) Measurement validation and adjustments (age, wing, weight)
    # ------------------------------------------------------------------
    for ring_entry in ringing_entries:
        
        # --- A) Automatic OCR error correction for 'age' (runs ALWAYS) ---
        raw_age = ring_entry.get("age")

        if isinstance(raw_age, dict):
            # Option 1: 'age' is a dict
            for key in ["written", "assumed"]:
                if key in raw_age:
                    raw_age[key] = correct_age_ocr(raw_age[key])
        elif raw_age is not None:
            # Option 2: 'age' is a string / int
            ring_entry["age"] = correct_age_ocr(raw_age)

        # --- B) Validation of allowed values for 'age' (runs based on config) ---
        if rule_config.get("age_validation", False):
            current_age = ring_entry.get("age")
            allowed_ages = {"1", "2", "3", "4", "5", "6", "7", "1J", "3J"}
            
            
            
            # Pomocná funkce pro validaci jedné hodnoty
            def is_invalid_age_val(val: Any) -> bool:
                if val is None:
                    return False
                val_str = str(val).strip().upper()
                if val_str in ("NULL", "NONE", ""):
                    return False
                return val_str not in allowed_ages

            # Logika pro kontrolu
            invalid_found = False

            if isinstance(current_age, dict):
                # Zkontrolujeme obě části nové struktury
                written_val = current_age.get("written")
                assumed_val = current_age.get("assumed")
                
                if is_invalid_age_val(written_val) or is_invalid_age_val(assumed_val):
                    invalid_found = True
            else:
                # Fallback pro jistotu, pokud by age byl stále jen obyčejný string
                if is_invalid_age_val(current_age):
                    invalid_found = True
                    
            if invalid_found:
                reasons.append("invalid_age")

        # --- C) Wing and weight validation (runs only if species matching is successful) ---

        reasons.extend(
            validate_wing_weight_rule(ring_entry, matched_entry, rule_config, measurement_tolerance)
            )
        


        # --- D) Date format validation and standardization (runs based on config) ---
        if rule_config.get("date_validation", False):
            raw_date = ring_entry.get("date")
            
            if raw_date is not None:
                date_str = str(raw_date).strip()
                
                # If the value is not "NULL" or empty string, process it
                if date_str.upper() not in ("NULL", ""):
                    
                    # 1. Standardize separators: replace dots and slashes with dashes
                    date_str = date_str.replace(".", "-").replace("/", "-")
                    
                    # Save the standardized date back to the data structure
                    ring_entry["date"] = date_str
                    
                    # 2. Check strict pattern DD-MM-YYYY using regex (catches missing/extra digits)
                    if not re.match(r"^\d{2}-\d{2}-\d{4}$", date_str):
                        reasons.append(f"invalid_date_format ({date_str})")
                    else:
                        # 3. Check if it is a valid calendar date (catches e.g., month 13, day 47)
                        try:
                            # This will raise ValueError for '32-01-2024' or '15-13-1994'
                            datetime.strptime(date_str, "%d-%m-%Y")
                        except ValueError:
                            reasons.append(f"invalid_calendar_date ({date_str})")
        
        # --- E) Sex format validation and standardization (runs based on config) ---
        if rule_config.get("sex_validation", False):
            raw_sex = ring_entry.get("sex")
            
            if raw_sex is not None:
                # Convert to string, strip whitespaces and make lowercase 
                # (handles "Female", " M ", etc.)
                sex_str = str(raw_sex).strip().lower()
                
                # If the value is not "null" or empty string, process it
                if sex_str not in ("null", ""):
                    
                    # 1. Standardize abbreviations, symbols, and correctly spelled values
                    if sex_str in ("f", "♀", "female"):
                        ring_entry["sex"] = "female"
                    elif sex_str in ("m", "♂", "male"):
                        ring_entry["sex"] = "male"
                    else:
                        # 2. If it's anything else, log it for review
                        # We don't overwrite the original invalid value so the user can see it
                        reasons.append(f"invalid_sex")
        
        



            


        # --- Location match (mbs_dict) ---
        if locations_lexicon is not None:
            raw_place = ring_entry.get("place")
            if raw_place is not None and str(raw_place).strip() != "":
                loc_match = best_location_match(raw_place, locations_lexicon, location_threshold)
                if loc_match:
                    ring_entry["place_mbs_dict"] = loc_match
                else: #None
                    ring_entry["place_mbs_dict"] = "loc_not_matched"
                    reasons.append("loc_not_matched")  #if the confidence level is less than trashold in config

        # Keep "re" as the last key of the ringing entry, after place_mbs_dict.
        # Popping and re-assigning moves an existing key to the end of the dict.
        # Must run after the location match, because that is what creates
        # place_mbs_dict in the first place.
        if "re" in ring_entry:
            ring_entry["re"] = ring_entry.pop("re")




    #-----order of keys in json-----
    desired_order = [
        "id_row",
        "serial", 
        "ring_no", 
        "species", 
        "ringing", 
        "OfficialName", 
        "EuringCode", 
        "MatchScore", 
        "allowed_wing_range", 
        "allowed_weight_range"
    ]
    
    ordered_enriched = {}
    
    # 1. Nejprve vložíme klíče definované v našem požadovaném pořadí
    for key in desired_order:
        if key in record:
            ordered_enriched[key] = record[key]
            
    # 2. Poté přidáme všechny ostatní klíče (pokud by se tam v budoucnu nějaké objevily)
    for key, value in record.items():
        if key not in ordered_enriched:
            ordered_enriched[key] = value

    # Vrátíme srovnaný slovník místo původního 'enriched'
    return ordered_enriched, reasons    




#==================================================================================
# COPY DATA - dealing with null data which are expected to be taken from rows above
#==================================================================================

# def resolve_prefix(current_val, last_val):
#     if not current_val or not last_val:
#         return current_val
        
#     is_shorter = len(current_val) < len(last_val)
#     starts_special = not current_val[0].isalnum()
    
#     if is_shorter or starts_special:
#         missing_length = len(last_val) - len(current_val)
#         if missing_length < 0:
#             return current_val 
            
#         prefix = last_val[:missing_length]
#         return prefix + current_val
        
#     return current_val

def resolve_prefix(current_val, last_val, max_suffix_length=None):
    if not current_val or not last_val:
        return current_val
        
    # 1. Configured mode: Ignore dots/special characters entirely.
    # Rely purely on alphanumeric character count.
    if max_suffix_length is not None and max_suffix_length > 0:
        alnum_count = sum(c.isalnum() for c in current_val)
        
        # If it fits the suffix length, append it to the missing prefix
        if alnum_count <= max_suffix_length:
            missing_length = len(last_val) - len(current_val)
            if missing_length > 0:
                return last_val[:missing_length] + current_val
        
        # If it exceeds the length, treat it as a standalone complete value
        return current_val

    # 2. Default mode: Original behavior when config limit is not set
    is_shorter = len(current_val) < len(last_val)
    starts_special = not current_val[0].isalnum()
    
    if is_shorter or starts_special:
        missing_length = len(last_val) - len(current_val)
        if missing_length < 0:
            return current_val 
            
        return last_val[:missing_length] + current_val
        
    return current_val




def is_explicit_null(val: Any) -> bool:
    """Buňka explicitně proškrtnutá — NESMÍ dědit z carry_memory.
    
    - \u2013 = – EN DASH (en dash, width of character „n")
    - \u2014 = — EM DASH (em dash, width of character „m")
    """
    EXPLICIT_NULL_TOKENS = {"-", "--", "\u2013", "\u2014"}
    return isinstance(val, str) and val.strip() in EXPLICIT_NULL_TOKENS

def is_empty_value(val: Any) -> bool:
    """
    Vyhodnotí, zda je hodnota prázdná. 
    Zachytává čistý null, textové ekvivalenty a prázdné nebo neplatné znaky od OCR.
    """
    if val is None:
        return True
    
    if isinstance(val, str):
        cleaned = val.strip().lower()
        # Přidán prázdný řetězec "" a běžné OCR "výplně" prázdných buněk
        if cleaned in ["null", "none", "", "-", "--", ".", "n/a", "nan"]:
            return True
            
    return False

def apply_carry_over_rules(node, config, state):
    """
    Rekurzivní funkce upravená tak, aby přijímala dynamický config z YAMLu.
    """
    if isinstance(node, dict):
        for k in list(node.keys()):
            v = node[k]

            # STORM PETREL OVERRIDE: raw pass-through.
            # Leaves the value untouched regardless of prefix_keys /
            # carry_over_keys / assumed_keys in config.yaml.
            if RING_NO_RAW_PASSTHROUGH and k in RING_NO_RAW_FIELDS:
                continue
            
            # PŘÍKLAD A
            if k in config.get("prefix_keys", []) and isinstance(v, str):

                forbidden_chars = ['*', '#', '$', '-', ',', '/', '"', ':', "'"]
                # Check if any character from the list is present anywhere in v
                if any(char in v for char in forbidden_chars):
                    node[k] = v
                    continue

                if k not in state["prefix_memory"]:
                    # První hodnota na kartě — z definice zapsaná v plném znění
                    state["prefix_memory"][k] = v
                    state.setdefault("written_values", {}).setdefault(k, []).append(v)
                else:
                    max_len = config.get("ring_no_max_suffix_length", None)

                    original_v = v
                    v = resolve_prefix(v, state["prefix_memory"][k], max_suffix_length=max_len)

                    node[k] = v
                    state["prefix_memory"][k] = v

                    # resolve_prefix vrací vstup beze změny, když šlo o samostatnou kompletní hodnotu
                    if v == original_v:
                        state.setdefault("written_values", {}).setdefault(k, []).append(v)

            # if k in config.get("prefix_keys", []) and isinstance(v, str):
            #     clean_v = v.lstrip("*")

            #     if k not in state["prefix_memory"]:
            #         state["prefix_memory"][k] = clean_v
            #         node[k] = v  # první záznam nech původní
            #     else:
            #         v = resolve_prefix(clean_v, state["prefix_memory"][k])
            #         node[k] = v
            #         state["prefix_memory"][k] = v
            
            # PŘÍKLAD B
            elif k in config.get("carry_over_keys", []):
                if is_explicit_null(v):
                    # Pomlčka = vynucený NULL. Ruší paměť natvrdo. Platí, dokud v sloupci nepřijde nová skutečná hodnota.
                    node[k] = None
                    state["carry_memory"].pop(k, None)

                elif is_empty_value(v):
                    if k in state["carry_memory"] and state["carry_memory"][k] is not None:
                        node[k] = state["carry_memory"][k]
                    else:
                        node[k] = None # Sjednocení prázdných hodnot na None
                
                else:
                    # Keep the actual value in the current node
                    node[k] = v
                    
                    # Decide whether to save this value to carry_memory for future rows
                    if k == "serial" and len(str(v).strip()) > 3:
                        # Do not update memory, so the previous valid serial is preserved
                        pass 
                    else:
                        # Standard behavior: update memory with the new valid value
                        state["carry_memory"][k] = v

            # PŘÍKLAD C
            elif k in config.get("assumed_keys", []):
                if is_empty_value(v):
                    assumed_val = state["carry_memory"].get(k, None)
                    node[k] = {
                        "written": None,
                        "assumed": assumed_val
                    }
                else:
                    state["carry_memory"][k] = v
                    node[k] = {
                        "written": v,
                        "assumed": None
                    }
                    
            if isinstance(node[k], (dict, list)) and k not in config.get("assumed_keys", []):
                apply_carry_over_rules(node[k], config, state)
            
    elif isinstance(node, list):
        for item in node:
            apply_carry_over_rules(item, config, state)



# =============================================================================
# HLAVNI FUNKCE — validator interface
# =============================================================================

def process_single_result(
    raw_result: Dict[str, Any],
    config: Dict[str, Any],
    carry_over_state: Dict[str, Any],
    lexicon: List[Dict[str, Any]],
    locations_lexicon: List[Dict[str, Any]],
    serial_lexicon: Optional[Dict[str, str]] = None
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Process a single raw result and return enriched data and review records.
    This is a helper function for process_batch.
    """
    # Konfigurace
    validation_config: Dict[str, Any] = config.get("validation", {})
    validator_name: str = validation_config.get("active_validator", "ocr_ringing")
    validator_settings: Dict[str, Any] = validation_config.get(validator_name, {})
    rule_config: Dict[str, bool] = validator_settings.get("rules", {})
    carry_over_config = validator_settings.get("carry_over_settings", {})

    species_threshold: float = float(
        validator_settings.get("species_match_threshold", DEFAULT_SPECIES_THRESHOLD)
    )
    location_match_threshold: float = float(
        validator_settings.get("location_match_threshold", DEFAULT_LOCATION_THRESHOLD)
    )
    measurement_tolerance: float = float(
        validator_settings.get("measurement_tolerance", DEFAULT_MEASUREMENT_TOLERANCE)
    )
    ring_no_mode: str = validator_settings.get("ring_no_mode", "AUTO")
    ring_no_manual_regex: str = validator_settings.get("ring_no_manual_regex", r"^\d{2}\.\d{3}$")
    is_verbose: bool = validator_settings.get("verbose", False)

    # Extrakce metadat
    custom_id: str = raw_result.get("custom_id", "")
    batch_id: str = raw_result.get("batch_id", "")

    # Parsovani textu -> strukturovana data
    parse_failed = False
    structured_data: Dict[str, Any] = {}

    text: str = raw_result.get("text", "")

    if text:
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)

        try:
            cleaned = text.strip()
            json_match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", cleaned)
            if json_match:
                parsed = json.loads(json_match.group(1))
                structured_data = normalize_llm_output(parsed)
            else:
                structured_data = {"raw": text}
                parse_failed = True
        except (json.JSONDecodeError, ValueError):
            structured_data = {"raw": text}
            parse_failed = True
        except Exception:
            structured_data = {"raw": text}
            parse_failed = True

    elif "data" in raw_result:
        structured_data = dict(raw_result)

    else:
        structured_data = {"raw": ""}
        parse_failed = True

    # Validace a obohaceni
    review_records: List[Dict[str, Any]] = []

    if not parse_failed:
        any_rule_active = any(rule_config.values()) if rule_config else False
        records_in_data: List[Dict[str, Any]] = structured_data.get("data", [])

        # --- First FOR CYCLE - carry_over ---
        for record in records_in_data:
            if carry_over_config:
                apply_carry_over_rules(record, carry_over_config, carry_over_state)

        # --- RING_NO VALIDATION ---
        # STORM PETREL OVERRIDE: no pattern is derived or applied for ring_no.
        ring_no_pattern = None
        if (not RING_NO_RAW_PASSTHROUGH) and rule_config.get("ring_no_validation", False):

            if ring_no_mode == "MANUAL":
                ring_no_pattern = ring_no_manual_regex


            
            elif ring_no_mode == "AUTO":



                if RING_NO_VOTE_SOURCE == "written":
                    vote_source = carry_over_state.get("written_values", {}).get("ring_no", [])
                else:
                    vote_source = [r.get("ring_no") for r in records_in_data if r.get("ring_no") is not None]

                chosen, counter, suspicious = dominant_pattern(vote_source)

                if len(vote_source) < RING_NO_MIN_WRITTEN:
                    # Málo podkladů — hlasování by nemělo výpovědní hodnotu.
                    # Uznáme každý nalezený tvar kromě poškozených.
                    accepted = [p for p in counter if p not in suspicious]
                else:
                    accepted = [p for p, c in counter.items()
                                if c >= RING_NO_MIN_SUPPORT and p not in suspicious]

                ring_no_pattern = "|".join(accepted) if accepted else chosen
            
            if is_verbose:
                print("Used regex for validation of ring_no: ", ring_no_pattern, " --> ", str(custom_id))

        # --- Second FOR CYCLE - validation and enrichment ---
        enriched_records: List[Dict[str, Any]] = []
        for record in records_in_data:
            enriched_record, reasons = enrich_record(
                record=record,
                rule_config=rule_config,
                lexicon=lexicon,
                species_threshold=species_threshold,
                measurement_tolerance=measurement_tolerance,
                ring_no_pattern=ring_no_pattern,
                is_verbose=is_verbose,
                locations_lexicon=locations_lexicon if locations_lexicon else None,
                location_threshold=location_match_threshold,
                serial_lexicon=serial_lexicon
            )
            enriched_records.append(enriched_record)

            if reasons:
                review_records.append({
                    "custom_id": custom_id,
                    "batch_id": batch_id,
                    "species_entry": enriched_record,
                    "reasons": reasons,
                    "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                })

        structured_data["data"] = enriched_records

        # --- checking the rule: every records must have 23 rows ---
        actual_row_count = len(enriched_records)
        if rule_config.get("23_rows", False):
            if actual_row_count != 23:
                review_records.append({
                    "custom_id": custom_id,
                    "batch_id": batch_id,
                    "reasons": ["wrong_row_count"],
                    "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                })

    # Pripojeni metadat
    structured_data["custom_id"] = custom_id
    structured_data["batch_id"] = batch_id
    structured_data["row_count"] = len(structured_data.get("data", []))

    return structured_data, review_records


def process_batch(
    all_raw_results: List[Dict[str, Any]],
    config: Dict[str, Any]
) -> List[Tuple[str, Any]]:
    """
    Process and enrich all raw OCR results from AI provider in a single call.
    Implements the new validator interface.

    Args:
        all_raw_results: List of all raw result dicts from AI provider
        config: Configuration dictionary with validation rules and settings

    Returns:
        List[Tuple[str, Any]] — seznam (filename, content):
          - ("results.jsonl",  enriched_dict)        <- uspesne zaznamy
          - ("review.jsonl",   [review_record, ...]) <- zaznamy k rucni kontrole
          - ("problems.jsonl", error_dict)            <- chyby parsovani
    """
    # Konfigurace
    validation_config: Dict[str, Any] = config.get("validation", {})
    validator_name: str = validation_config.get("active_validator", "ocr_ringing")
    validator_settings: Dict[str, Any] = validation_config.get(validator_name, {})
    rule_config: Dict[str, bool] = validator_settings.get("rules", {})
    lexicon_path: str = validation_config.get("species_lexicon_path", "")
    locations_lexicon_path: str = validation_config.get("locations_lexicon_path", "")
    serial_lexicon_path: str = validation_config.get("serial_lexicon_path", "")
    is_verbose: bool = validator_settings.get("verbose", False)

    name_suffix: str = config.get("directories", {}).get("name_suffix", "")

    # Načtení lexikonů
    any_rule_active = any(rule_config.values()) if rule_config else False
    lexicon: List[Dict[str, Any]] = []
    if any_rule_active and lexicon_path:
        try:
            lexicon = load_species_lexicon(lexicon_path)
        except Exception as exc:
            print(f"[WARNING] Error while loading lexicon ({lexicon_path}): {exc}")

    locations_lexicon: List[Dict[str, Any]] = []
    if locations_lexicon_path:
        try:
            locations_lexicon = load_locations_lexicon(locations_lexicon_path)
            if is_verbose:
                print(f"[INFO] Načteno {len(locations_lexicon)} lokací z lexikonu.")
        except Exception as exc:
            print(f"[WARNING] Chyba pri nacitani lexikonu lokací ({locations_lexicon_path}): {exc}")

    serial_lexicon: Dict[str, str] = {}
    if serial_lexicon_path:
        try:
            serial_lexicon = load_serial_lexicon(serial_lexicon_path)
            if is_verbose:
                print(f"[INFO] Loaded {len(serial_lexicon)} serial options from lexicon.")
        except Exception as exc:
            print(f"[WARNING] Error while loading serial lexicon ({serial_lexicon_path}): {exc}")

    # Zpracování všech raw_results
    all_enriched: List[Dict[str, Any]] = []
    all_review_records: List[Dict[str, Any]] = []
    all_problem_records: List[Dict[str, Any]] = []

    # Inicializace carry_over state (sdílená pro všechny záznamy se stejným custom_id)
    carry_over_state = {
        "prefix_memory": {},
        "carry_memory": {}
    }

    for raw_result in all_raw_results:
        # Reset carry_over state pro nový custom_id
        current_custom_id = raw_result.get("custom_id", "")
        if current_custom_id != carry_over_state.get("last_custom_id"):
            carry_over_state = {
                "prefix_memory": {},
                "carry_memory": {}
            }
            carry_over_state["last_custom_id"] = current_custom_id

        enriched, review_records = process_single_result(
            raw_result, config, carry_over_state, lexicon, locations_lexicon, serial_lexicon
        )
        all_enriched.append(enriched)
        all_review_records.extend(review_records)

        # Check for parse failure
        if enriched.get("raw"):
            custom_id = raw_result.get("custom_id", "")
            batch_id = raw_result.get("batch_id", "")
            problem_record = {
                "custom_id": custom_id,
                "batch_id": batch_id,
                "reason": "JSON_PARSE_ERROR",
                "raw_text": raw_result.get("text", "")[:500],
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            all_problem_records.append(problem_record)

    # Apply backfill_species if enabled
    modified_results, backfill_reviews = apply_backfill_species(all_enriched, config, lexicon, serial_lexicon = serial_lexicon)
    all_enriched = modified_results
    all_review_records.extend(backfill_reviews)

    # Merge review records se stejným (custom_id, id_row) do jednoho řádku
    all_review_records = merge_review_records(all_review_records)

    # Sestaveni vystupu
    outputs: List[Tuple[str, Any]] = []

    if all_problem_records:
        outputs.append(("problems.jsonl", all_problem_records))

    outputs.append((f"results{name_suffix}.jsonl", all_enriched))

    if all_review_records:
        outputs.append((f"review{name_suffix}.jsonl", all_review_records))

    return outputs
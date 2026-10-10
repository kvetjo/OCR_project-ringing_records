import json
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional
from rapidfuzz import fuzz, process as fuzz_process
import csv
import unicodedata
import string

DEFAULT_SPECIES_THRESHOLD = 80.0 
DEFAULT_MEASUREMENT_TOLERANCE = 0.10  # ± 10 % od hranic lexikonu
DEFAULT_LOCATION_THRESHOLD = 85.0


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

def normalize_llm_output(parsed_data: Any) -> Dict[str, Any]:
    """
    Helper function to normalize LLM output.
    Ensures that structured data is always in a dictionary format.
    """
    if isinstance(parsed_data, list):
        return {"data": parsed_data}
    elif isinstance(parsed_data, dict):
        return parsed_data
    else:
        return {"value": parsed_data}

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

    # Check for string representations of missing values
    val_str = str(value).strip().upper()
    if val_str in ("", "NONE", "NULL", "NAN", "N/A", "/", "-"):
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


def validate_wing_weight_rule(
    entry: Dict[str, Any],
    matched_entry: Optional[Dict[str, Any]],
    rule_config: Dict[str, bool],
    measurement_tolerance: float = 0.1, # Zde pouzij svuj DEFAULT_MEASUREMENT_TOLERANCE
    wing_key: str = "retrap_wing",
    weight_key: str = "retrap_weight"
) -> List[str]:
    """
    General function for wing_range / weight_range rules.
    Returns a list of reasons, e.g., ["wing_out_of_range", "weight_out_of_range"].
    Empty list = all OK.
    """
    reasons: List[str] = []

    if not matched_entry:
        return reasons

    # ---- Wing ----------------------------------------------------------
    if rule_config.get("wing_range", False):
        lex_range = matched_entry.get("wing_range")
        valid, _detail = validate_measurement(
            entry.get(wing_key), lex_range, measurement_tolerance
        )
        if not valid:
            reasons.append(wing_key + "_out_of_range")

    # ---- Weight ----------------------------------------------------------
    if rule_config.get("weight_range", False):
        lex_range = matched_entry.get("weight_range")
        valid, _detail = validate_measurement(
            entry.get(weight_key), lex_range, measurement_tolerance
        )
        if not valid:
            reasons.append(weight_key + "_out_of_range")

    return reasons



def best_species_match(
    species: str,
    lexicon: List[Dict[str, Any]],
    threshold: float = DEFAULT_SPECIES_THRESHOLD,
) -> Tuple[Optional[Dict[str, Any]], float]:
    """
    Matches the species name to a record in the lexicon using rapidfuzz.
    Returns (lex_entry, score) or (None, 0.0) if nothing meets the threshold.
    Searches through both official and alternative names.
    """
    if not species:
        return None, 0.0

    # Flatten: parallel lists of names and indices to the lexicon
    flat_names: List[str] = []
    flat_idx: List[int] = []
    for lex_idx, entry in enumerate(lexicon):
        for name in entry.get("search_names", []):
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


def validate_and_standardize_date(
    raw_date: Any, 
    field_context: str
) -> Tuple[Optional[str], Optional[datetime], List[str]]:
    """
    Standardizes date separators, validates strict DD-MM-YYYY format,
    checks if it's a real calendar date, ensures the year is <= 2020,
    and flags missing values.
    """
    reasons: List[str] = []
    
    if raw_date is None:
        reasons.append("invalid_" + field_context)
        return None, None, reasons
        
    date_str = str(raw_date).strip()
    if date_str.upper() in ("NULL", "NONE", "", "NAN", "", "/", "-"):
        reasons.append("invalid_" + field_context)
        return date_str, None, reasons

    # 1. Standardize separators
    date_str = date_str.replace(".", "-").replace("/", "-")
    
    # 2. Strict regex check for DD-MM-YYYY
    if not re.match(r"^\d{2}-\d{2}-\d{4}$", date_str):
        reasons.append("invalid_" + field_context)
        return date_str, None, reasons
        
    # 3. & 4. Calendar date and future check (> 2020 limit)
    try:
        parsed_date = datetime.strptime(date_str, "%d-%m-%Y")
        if parsed_date.year > 2020:
            reasons.append("invalid_" + field_context)
        return date_str, parsed_date, reasons
    except ValueError:
        reasons.append("invalid_" + field_context)
        return date_str, None, reasons



def validate_and_correct_age(
    entry: Dict[str, Any], 
    rule_config: Dict[str, Any],
    age_key: str = "retrap_age"
) -> List[str]:
    """
    Corrects OCR errors for the specified 'age' field and validates allowed values.
    Returns a list of reasons if manual review is required.
    """
    reasons: List[str] = []
    raw_age = entry.get(age_key)

    # 1. Automatic OCR error correction (runs ALWAYS)
    if raw_age is not None:
        # Assuming correct_age_ocr is defined elsewhere in your code
        entry[age_key] = correct_age_ocr(raw_age) 
    
    # 2. Validation of allowed values (runs based on config)
    if rule_config.get("age_validation", False):
        current_age = entry.get(age_key)
        allowed_ages = {"1", "2", "3", "4", "5", "6", "7", "1J", "3J"}
        
        if current_age is not None:
            val_str = str(current_age).strip().upper()
            
            # Empty or null values are OK, no review reason added
            if val_str not in ("NULL", "NONE", ""):
                if val_str not in allowed_ages:
                    reasons.append("invalid_" + str(age_key))
                    
    return reasons


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


def validate_and_format_ring_no(
    raw_ring_no: Any, 
    rule_config: Dict[str, Any],
    validator_settings: Dict[str, Any]
) -> Tuple[Optional[str], List[str]]:
    """
    Validates and standardizes the ring_no field.
    Mandatory check runs always. Format validation depends on rule_config.
    Reads mode and regex from validator_settings.

    Performed validation and formatting steps:
    1. Mandatory Checks (Always runs)
    - Rejects None values.
    - Rejects empty strings or literal "NULL", "NONE", "NAN" values.
    2. Configuration Bypass
    - Skips remaining steps if 'ring_no_validation' in rule_config is False.
    3. Data Cleanup
    - Automatically removes all internal whitespace.
    4. Minimum Length Check
    - Rejects strings with fewer than 3 characters.
    5. Mode-Based Validation (Based on 'ring_no_mode')
    - MANUAL Mode:
        - Validates string against custom 'ring_no_manual_regex'.
    - AUTO Mode (Default):
        - Rule C: Allows only alphanumeric characters and dots.
        - Rule A & F: Rightmost 3 characters must always be digits.
        - Rule E: If FORCE_DOT is enabled, requires a dot exactly before the last 3 digits.
    """
    reasons: List[str] = []
    
    # 1. Mandatory check (Runs ALWAYS, regardless of config rules)
    if raw_ring_no is None:
        reasons.append("invalid_ring_no")
        return None, reasons
        
    rn_str = str(raw_ring_no).strip()
    
    if rn_str.upper() in ("NULL", "NONE", "", "NAN"):
        reasons.append("invalid_ring_no")
        return rn_str, reasons

    # 2. Check if detailed format validation is enabled in rules
    if not rule_config.get("ring_no_validation", False):
        return rn_str, reasons

    # 3. Remove all spaces (not considered an error, just cleanup)
    rn_str = rn_str.replace(" ", "")
    
    # 4. Check minimum length
    if len(rn_str) <= 3:
        reasons.append("invalid_ring_no")
        return rn_str, reasons

    # 5. Extract mode and regex from validator_settings
    mode = validator_settings.get("ring_no_mode", "AUTO").upper()
    
    # 6. MANUAL Mode
    if mode == "MANUAL":
        manual_pattern = validator_settings.get("ring_no_manual_regex")
        if manual_pattern:
            if not re.match(manual_pattern, rn_str):
                reasons.append("invalid_ring_no")
    
    # 7. AUTO Mode
    else:
        # IN-CODE TOGGLE: Set to True to strictly require a dot before the last 3 digits
        FORCE_DOT = False 
        
        # Rule C: Only alphanumeric characters and a dot are allowed
        if not re.fullmatch(r"^[A-Za-z0-9\.]+$", rn_str):
            reasons.append("invalid_ring_no")
            
        # Rule A & F: The rightmost 3 characters must ALWAYS be digits
        if not rn_str[-3:].isdigit():
            reasons.append("invalid_ring_no")
            
        # Rule E: If dot is forced, it must be located exactly before the last 3 digits
        if FORCE_DOT:
            if len(rn_str) >= 4 and rn_str[-4] != ".":
                reasons.append("invalid_ring_no")

    return rn_str, reasons





def enrich_record(
    record: Dict[str, Any],
    rule_config: Dict[str, Any],
    validator_settings: Dict[str, Any],
    lexicon: List[Dict[str, Any]],
    species_threshold: float,
    measurement_tolerance: float,
    locations_lexicon: Optional[List[Dict[str, Any]]] = None,
    location_threshold: float = 80.0  # Default RapidFuzz threshold
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Placeholder for future validation and enrichment of a single retrap record.
    
    Args:
        record: A single dictionary representing one row of extracted data.
        rule_config: Configuration dictionary for active validation rules.
        
    Returns:
        Tuple containing the (potentially modified) record and a list of reasons 
        if the record requires manual review.
    """
    reasons: List[str] = []
    # Initialize matched_entry to avoid UnboundLocalError
    matched_entry: Optional[Dict[str, Any]] = None
    


    # =====================================================================
    # RING_NO VALIDATION & FORMATTING
    # =====================================================================
    raw_ring_no = record.get("ring_no")
    
    # Předáváme nyní i validator_settings
    std_ring_no, ring_no_reasons = validate_and_format_ring_no(
        raw_ring_no, rule_config, validator_settings
    )
    
    if std_ring_no is not None:
        record["ring_no"] = std_ring_no
        
    reasons.extend(ring_no_reasons)

    # =====================================================================
    # SPECIES VALIDATION AND ENRICHMENT
    # =====================================================================
    if rule_config.get("species_match", False) and lexicon:
        species_name = record.get("species", "")

        
        if species_name:
            matched_entry, score = best_species_match(
                species_name, lexicon, threshold=species_threshold
            )
            
            if matched_entry:
                # Enrichment - adding new fields, not overwriting the original
                record["OfficialName"] = matched_entry.get("official_name")
                record["EuringCode"] = matched_entry.get("euring_code")
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

                # Fix: Check the actual parsed species string, not matched_entry
                species_str = str(species_name).strip().lower()
                if species_str not in {"", "none", "null", "nan", "n/a", "/", "-"}:
                    reasons.append("sp_low_match_score")
        else:
            reasons.append("missing_species")
    

    # =====================================================================
    # DATE VALIDATION & CHRONOLOGICAL CHECK
    # =====================================================================
    last_valid_date_obj: Optional[datetime] = None
    
    #DATE - HEADER VALIDATION (1.)
    if rule_config.get("date_validation", False):
        # 1. Validate header date (ringing_date)
        raw_ringing_date = record.get("ringing_date")
        std_ringing, parsed_ringing, date_reasons = validate_and_standardize_date(
            raw_ringing_date, "ringing_date"
        )
        
        # Save standardized date back
        if std_ringing is not None:
            record["ringing_date"] = std_ringing
            
        reasons.extend(date_reasons)
        
        # Set the baseline for chronological checks
        if parsed_ringing:
            last_valid_date_obj = parsed_ringing

    # =====================================================================
    # RINGING DATA VALIDATION (Age, Wing, Weight)
    # =====================================================================
    # Validate and correct ringing_age
    reasons.extend(
        validate_and_correct_age(record, rule_config, age_key="ringing_age")
    )

    # Validate ringing_wing and ringing_weight against lexicon
    if matched_entry:
        reasons.extend(
            validate_wing_weight_rule(
                entry=record, 
                matched_entry=matched_entry, 
                rule_config=rule_config, 
                measurement_tolerance=measurement_tolerance,
                wing_key="ringing_wing",
                weight_key="ringing_weight"
            )
        )

    # =====================================================================
    # LOCATION VALIDATION & MATCHING
    # =====================================================================
    if rule_config.get("location_match", False) and locations_lexicon is not None:
        raw_place = record.get("ringing_place")
        
        if raw_place is not None and str(raw_place).strip() != "":
            loc_match = best_location_match(
                raw_place, locations_lexicon, location_threshold
            )
            
            if loc_match:
                record["ringing_place_mbs_dict"] = loc_match
            else:
                record["ringing_place_mbs_dict"] = "loc_not_matched"
                reasons.append("loc_not_matched")
        else:
            # Option to add a reason if place is missing entirely
            reasons.append("missing_place")



    # =====================================================================
    # RETRAPS ITERATION (Age, Wing/Weight, Date Chronology)
    # =====================================================================
    retraps_entries = record.get("retraps", [])
    
    if isinstance(retraps_entries, list):
        # Sort retraps by id_retrap to ensure correct chronological sequence check
        sorted_retraps = sorted(
            [r for r in retraps_entries if isinstance(r, dict)], 
            key=lambda x: x.get("id_retrap", 999)
        )
        
        for retrap_entry in sorted_retraps:
            
            #DATE - RETRAP VALIDATION (2.)
            # --- A. Date validation & Chronology for this retrap ---
            if rule_config.get("date_validation", False):
                raw_retrap_date = retrap_entry.get("retrap_date")
                std_retrap, parsed_retrap, r_reasons = validate_and_standardize_date(
                    raw_retrap_date, "retrap_date"
                )
                
                # Save standardized date back
                if std_retrap is not None:
                    retrap_entry["retrap_date"] = std_retrap
                reasons.extend(r_reasons)
                
                # Check chronology: Must be strictly newer than the previous valid date
                if parsed_retrap and last_valid_date_obj:
                    if parsed_retrap <= last_valid_date_obj:
                        reasons.append("chronological_order_error")
                    last_valid_date_obj = parsed_retrap
                elif parsed_retrap:
                    last_valid_date_obj = parsed_retrap

            # --- B. Age validation and correction ---
            reasons.extend(
                validate_and_correct_age(retrap_entry, rule_config, "retrap_age")
            )

            # --- C. Wing and weight validation ---
            if matched_entry:
                reasons.extend(
                    validate_wing_weight_rule(
                        retrap_entry, 
                        matched_entry, 
                        rule_config, 
                        measurement_tolerance,
                        "retrap_wing",
                        "retrap_weight"
                    )
                )
    else:
        if retraps_entries is not None and retraps_entries != "":
            print("[ERROR] Skipping this record. JSON structure is bad (a).")

    return record, reasons




def process_single_result(
    raw_result: Dict[str, Any],
    config: Dict[str, Any],
    lexicon_species: List[Dict[str, Any]],
    locations_lexicon: List[Dict[str, Any]] = None
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Process a single raw result and return structured data and review records.
    This handles the text parsing, metadata assignment, and calls record-level enrichment.
    """
    # Extract configuration
    validation_config: Dict[str, Any] = config.get("validation", {})
    validator_name: str = validation_config.get("active_validator", "ocr_retrap")
    validator_settings: Dict[str, Any] = validation_config.get(validator_name, {})
    rule_config: Dict[str, bool] = validator_settings.get("rules", {})
    
    #is_verbose: bool = validator_settings.get("verbose", True)

    # Extract species threshold from validator settings, NOT from rules
    location_threshold: float = float(
        validator_settings.get("location_match_threshold", DEFAULT_LOCATION_THRESHOLD)
    )
    species_threshold: float = float(
        validator_settings.get("species_match_threshold", DEFAULT_SPECIES_THRESHOLD)
    )
    measurement_tolerance: float = float(
        validator_settings.get("measurement_tolerance", DEFAULT_MEASUREMENT_TOLERANCE)
    )
    # Extract metadata
    custom_id: str = raw_result.get("custom_id", "")
    batch_id: str = raw_result.get("batch_id", "")
    text: str = raw_result.get("text", "")

    # Parsing text into structured data
    parse_failed = False
    structured_data: Dict[str, Any] = {}

    if text:
        # Clean text (Strip potential ```json wrappers)
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)

        try:
            cleaned = text.strip()
            # Look for anything that looks like a JSON object {} or array []
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

    # Validation and enrichment
    review_records: List[Dict[str, Any]] = []

    if not parse_failed:
        # Process the parsed dictionary directly as a single record
        enriched_record, reasons = enrich_record(
            record=structured_data,
            rule_config=rule_config,
            validator_settings=validator_settings,
            lexicon=lexicon_species,
            species_threshold=species_threshold,
            measurement_tolerance=measurement_tolerance,
            locations_lexicon=locations_lexicon,      # NEW
            location_threshold=location_threshold     # NEW
        )

        # If reasons were returned, flag for manual review
        if reasons:
            review_records.append({
                "custom_id": custom_id,
                "batch_id": batch_id,
                "record_entry": enriched_record, # Store the flat enriched record
                "reasons": reasons,
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            })

        # The dictionary is enriched in-place, so structured_data is already updated
        structured_data = enriched_record

    # Append metadata
    structured_data["custom_id"] = custom_id
    structured_data["batch_id"] = batch_id
    #structured_data["row_count"] = 1 if not parse_failed else 0

    return structured_data, review_records


def process_batch(
    all_raw_results: List[Dict[str, Any]],
    config: Dict[str, Any]
) -> List[Tuple[str, Any]]:
    """
    Process and enrich all raw OCR results from AI provider in a single call.
    Implements the new validator interface.
    """

    validation_config: Dict[str, Any] = config.get("validation", {})
    lexicon_path: str = validation_config.get("species_lexicon_path", "")
    locations_lexicon_path: str = validation_config.get("locations_lexicon_path", "")
    is_verbose: bool = config.get("verbose", True)

    # Get name_suffix for filenames
    directories = config.get('directories', {})
    name_suffix = directories.get('name_suffix', '')

    all_enriched: List[Dict[str, Any]] = []
    all_problem_records: List[Dict[str, Any]] = []
    all_review_records: List[Dict[str, Any]] = []

    # Initialize lexicon to an empty list BEFORE the try block
    lexicon: List[Dict[str, Any]] = []
    if lexicon_path:
        try:
            lexicon = load_species_lexicon(lexicon_path)
        except Exception as exc:
            print(f"[WARNING] Error while loading lexicon ({lexicon_path}): {exc}")

    # =====================================================================
    # LOAD LOCATIONS LEXICON
    # =====================================================================
    locations_lexicon: List[Dict[str, Any]] = []
    if locations_lexicon_path:
        try:
            locations_lexicon = load_locations_lexicon(locations_lexicon_path)
            if is_verbose:
                print(f"[INFO] Loaded {len(locations_lexicon)} locations from lexicon.")
        except Exception as exc:
            print(f"[WARNING] Error while loading locations lexicon ({locations_lexicon_path}): {exc}")
    # =====================================================================



    for raw_result in all_raw_results:
        # Process individual result
        enriched, review_records = process_single_result(raw_result, config, lexicon, locations_lexicon)
        
        # Collect review records
        all_review_records.extend(review_records)

        # Check for parsing errors
        if enriched.get("raw"):
            custom_id = raw_result.get("custom_id", "")
            batch_id = raw_result.get("batch_id", "")
            text = raw_result.get("text", "")
            
            error_record = {
                "custom_id": custom_id,
                "batch_id": batch_id,
                "reason": "JSON_PARSE_ERROR",
                "raw_text": text[:500],
                "timestamp": enriched.get("timestamp", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
            }
            all_problem_records.append(error_record)
        else:
            # Append successfully parsed and enriched data
            all_enriched.append(enriched)

    # Compile output files
    output_files: List[Tuple[str, Any]] = []

    if all_problem_records:
        output_files.append(("problems.jsonl", all_problem_records))

    if all_enriched:
        output_files.append((f"results{name_suffix}.jsonl", all_enriched))
        
    if all_review_records:
        output_files.append((f"review{name_suffix}.jsonl", all_review_records))

    return output_files
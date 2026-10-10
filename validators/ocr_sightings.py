"""
OCR Sightings Validator
Validates and enriches OCR results with species matching and rule-based review.
"""
import csv
import json
import re
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict
from pathlib import Path
from rapidfuzz import process, fuzz
import statistics as _statistics


# ============================================================================
# INTERNAL BOB FUNCTION: Deduplication of Review Files
# ============================================================================

def _deduplicate_review_records(
    review_records: List[Dict[str, Any]],
    identity_keys: List[Tuple[str, ...]]
) -> List[Dict[str, Any]]:
    """
    Merge review records that share the same identity into one record,
    combining their reasons lists.
    
    Args:
        review_records: List of review record dicts to deduplicate
        identity_keys: List of key paths that together define identity.
                       Each key path is a tuple of strings representing
                       a nested path into the record dict.
                       Example: [("custom_id",), ("species_entry", "EuringCode")]
                       means two records are the same if both custom_id
                       and species_entry.EuringCode match.
    
    Returns:
        Deduplicated list of review records
    """
    def extract_key(rec: Dict[str, Any], key_path: Tuple[str, ...]) -> str:
        val = rec
        for k in key_path:
            if not isinstance(val, dict):
                return ""
            val = val.get(k, "")
        return str(val)

    def make_identity(rec: Dict[str, Any]) -> Tuple[str, ...]:
        return tuple(extract_key(rec, kp) for kp in identity_keys)

    merged: Dict[Tuple[str, ...], Dict[str, Any]] = {}

    for rec in review_records:
        identity = make_identity(rec)

        if identity in merged:
            existing_reasons = set(merged[identity]["reasons"])
            for r in rec.get("reasons", []):
                if r not in existing_reasons:
                    merged[identity]["reasons"].append(r)
                    existing_reasons.add(r)
        else:
            merged[identity] = rec

    return list(merged.values())


# ============================================================================
# OCR OUTPUT SCHEMA CONFIGURATION
# ============================================================================

HEADER_FIELDS = ["Location", "Weather", "Date", "Time", "Obs"]
SPECIES_KEYWORDS_TO_SKIP = {"sp.", "sp", "species", "hirundines", "broadwing", "bop", "ringtail", "Wader"}



# skip these species
def is_to_skip_species(name: Optional[str]) -> bool:
    """
    Returns True if the species name contains a keyword indicating an unidentified or grouped species (e.g. "Duck sp.", "hirundines").
    The comparison is performed on individual words — partial matches inside words are ignored (e.g. "Lapland Longspur" will not be removed despite containing "sp" in the name).
    """
    if not name:
        return False
    # Rozdělíme název na slova, každé očistíme od interpunkce a převedeme na lowercase
    words = {token.strip(".,;:()[]").lower() for token in name.split()}
    return bool(words & SPECIES_KEYWORDS_TO_SKIP)



# ============================================================================
# LLM OUTPUT NORMALIZATION
# ============================================================================

def normalize_llm_output(parsed: Any) -> dict:
    """Normalize LLM output to standard format."""
    if isinstance(parsed, dict):
        return parsed

    if isinstance(parsed, list):
        return merge_fragmented_records(parsed)

    raise ValueError("Unsupported LLM output type")


def merge_fragmented_records(records: list) -> dict:
    """Merge fragmented records into single record."""
    if not records:
        raise ValueError("Empty list")

    if not all(isinstance(r, dict) for r in records):
        raise ValueError("List contains non-dict items")

    merged = {k: "" for k in HEADER_FIELDS}
    merged["species"] = []

    for field in HEADER_FIELDS:
        values = [
            r.get(field, "").strip()
            for r in records
            if isinstance(r.get(field), str) and r.get(field).strip()
        ]

        if values:
            if len(set(values)) > 1:
                raise ValueError(f"Conflicting values for field '{field}': {set(values)}")
            merged[field] = values[0]

    for r in records:
        species = r.get("species")
        if not species:
            continue

        if not isinstance(species, list):
            raise ValueError("species is not a list")

        for sp in species:
            if not isinstance(sp, dict):
                raise ValueError("Invalid species entry")
            merged["species"].append(sp)

    if not merged["species"]:
        raise ValueError("No species found after merge")

    return merged


def clean_name(s: Optional[str]) -> str:
    """Clean species name string."""
    if s is None:
        return ""
    s = str(s).strip()
    s = s.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
    s = s.strip(",").strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    return s


def levenshtein_distance(a: str, b: str) -> int:
    """Calculate Levenshtein distance between two strings."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(
                prev[j] + 1,
                cur[j - 1] + 1,
                prev[j - 1] + (ca != cb)
            ))
        prev = cur
    return prev[-1]


def best_species_match(
    pred: Optional[str],
    lexicon: Dict[str, Dict[str, Any]]
) -> Tuple[Dict[str, Any], float]:
    """Find best matching species in lexicon for predicted name."""
    if not pred:
        return {}, 0.0

    p = clean_name(pred).lower()

    # 1. Exact match
    if p in lexicon:
        return lexicon[p], 1.0

    # 2. Fuzzy match across all names
    match = process.extractOne(
        p,
        lexicon.keys(),
        scorer=fuzz.token_set_ratio
    )

    if not match:
        return {}, 0.0

    normalized_name, score, _ = match
    return lexicon[normalized_name], score / 100.0


def number_has_illegal_chars(number: str) -> bool:
    """Check if number has illegal characters."""
    pattern = r'^(c\.?)?\d+\+?$'
    return not bool(re.match(pattern, str(number).strip()))


def is_suspicious_number(number: str) -> bool:
    """Check if number is suspicious (not multiple of 5 or 10 when > 40)."""
    if number is None:
        return False
    try:
        n = int(number)
    except (ValueError, TypeError):
        return False

    if n <= 40:
        return False

    return not (n % 10 == 0 or n % 10 == 5)


def clean_number_value(value: str) -> str:
    """Clean and normalize number value."""
    if value is None:
        return value

    original = str(value).strip()

    # Remove prefix "c." or "c" and symbols ♂ ♀
    original = re.sub(r'^c\.?', '', original)
    original = re.sub(r'\s*[♂♀]\s*', '', original)
    original = re.sub(r'<\s*', '', original)

    # Addition using +
    if '+' in original:
        parts = re.split(r'\+', original)
        numbers = []

        for p in parts:
            p = p.strip()
            if p.isdigit():
                numbers.append(int(p))
            else:
                match = re.match(r'\d+', p)
                if match:
                    numbers.append(int(match.group()))

        if numbers:
            return str(sum(numbers))

    # Commas or semicolons
    if re.search(r'[;,]', original):
        parts = re.split(r'[;,]', original)
        numbers = []

        for p in parts:
            p = p.strip()
            if p.isdigit():
                numbers.append(int(p))

        if numbers:
            return str(sum(numbers))

    # Range (number - number)
    range_match = re.fullmatch(r'(\d+)\s*[-–]\s*(\d+)', original)
    if range_match:
        start, end = range_match.groups()
        return min(start, end, key=int)

    # Single dash after number (e.g. "5-") → remove dash
    single_dash_match = re.fullmatch(r'(\d+)\s*[-–]', original)
    if single_dash_match:
        return single_dash_match.group(1)

    # Pure number
    if original.isdigit():
        return original

    # Safe fallback → don't change
    return original


def normalize_species_numbers(data: dict) -> dict:
    """Normalize species numbers in data."""
    if not isinstance(data, dict):
        return data

    species = data.get("species")
    if not isinstance(species, list):
        return data

    for item in species:
        if not isinstance(item, dict):
            continue
        if "Number" in item:
            item["Number"] = clean_number_value(item["Number"])

    return data


def detect_duplicate_species(species: list) -> dict:
    """Detect duplicate species in a record."""
    groups = defaultdict(list)
    for sp in species:
        euring = sp.get("EuringCode")
        
        if euring and euring.upper() != "NULL":
            key = euring
        else:
            key = sp.get("OfficialName")
        
        if key:
            groups[key].append(sp)
    
    return {k: v for k, v in groups.items() if len(v) > 1}


def analyze_species_for_review(
    entry: Dict[str, Any],
    rule_config: Dict[str, bool],
) -> List[str]:
    """Analyze species entry and return list of reasons for review."""
    reasons: List[str] = []

    def low_match_score() -> bool:
        try:
            score = entry.get("MatchScore")
            return score is not None and float(score) < 0.5
        except Exception:
            return False

    def name_without_number() -> bool:
        name = entry.get("Name")
        number = entry.get("Number")
        return bool(name) and (number is None or str(number).strip() == "")

    def number_has_illegal_chars_rule() -> bool:
        number = entry.get("Number")
        return bool(number) and number_has_illegal_chars(number)

    def suspicious_number_rule() -> bool:
        number = entry.get("Number")
        return bool(number) and is_suspicious_number(number)

    def rare_species_E() -> bool:
        return entry.get("RareSpec") == "E"

    def exceeds_monthly_max() -> bool:
        try:
            number = entry.get("Number")
            max_allowed = entry.get("MonthMax")
            if number is None or max_allowed is None:
                return False
            return int(number) > max_allowed
        except Exception:
            return False

    RULES = {
        "low_match_score": low_match_score,
        "name_without_number": name_without_number,
        "number_has_illegal_chars": number_has_illegal_chars_rule,
        "suspicious_number_rule": suspicious_number_rule,
        "rare_species_E": rare_species_E,
        "exceeds_monthly_max": exceeds_monthly_max
    }

    for rule_name, rule_fn in RULES.items():
        if not rule_config.get(rule_name, False):
            continue
        try:
            if rule_fn():
                reasons.append(rule_name)
        except Exception:
            pass

    return reasons


def create_review_record(
    main_obj: Dict[str, Any],
    species_entry: Dict[str, Any],
    reasons: List[str]
) -> Dict[str, Any]:
    """Create a review record (in memory, not written to file)."""
    return {
        "custom_id": main_obj.get("custom_id"),
        "batch_id": main_obj.get("batch_id"),
        "species_entry": species_entry,
        "reasons": reasons,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    }


def extract_month(date_str: Optional[str]) -> Optional[int]:
    """Extract month from date string in DD-MM-YYYY format."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%d-%m-%Y").month
    except Exception:
        return None


MONTH_FIELDS = [
    "Jan_Max", "Feb_Max", "Mar_Max", "Apr_Max", "May_Max", "Jun_Max",
    "Jul_Max", "Aug_Max", "Sep_Max", "Oct_Max", "Nov_max", "Dec_Max",
]


def load_species_lexicon(path: str) -> Dict[str, Dict[str, Any]]:
    """Load species lexicon from CSV file."""
    lexicon = {}

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            full_name = row["Full_Official_Name"]
            euring = row["Euring_Code"]
            rare = (row.get("Rare_Spec") or "").strip()

            month_max = {}
            for i, field in enumerate(MONTH_FIELDS, start=1):
                try:
                    month_max[i] = int(row[field])
                except Exception:
                    month_max[i] = None

            names = [full_name]
            if row.get("Alternate_Names"):
                names.extend(
                    n.strip()
                    for n in row["Alternate_Names"].split(";")
                    if n.strip()
                )

            for name in names:
                normalized = clean_name(name).lower()
                lexicon[normalized] = {
                    "Full_Official_Name": full_name,
                    "Euring_Code": euring,
                    "Rare_Spec": rare,
                    "Month_Max": month_max,
                    "source_name": name,
                }

    return lexicon


def apply_record_level_rules(
    result: Dict[str, Any],
    species: List[Dict[str, Any]],
    rule_config: Dict[str, bool],
    review_records: List[Dict[str, Any]]
) -> None:
    """Apply record-level validation rules."""
    if not rule_config.get("duplicate_species_in_record", False):
        return

    duplicates = detect_duplicate_species(species)

    for items in duplicates.values():
        for sp in items:
            review_records.append(create_review_record(result, sp, ["duplicate_species_in_record"]))



def validate_and_standardize_date(
    result: Dict[str, Any],
    review_records: List[Dict[str, Any]]
) -> bool:
    """
    Validates and standardizes the record's date. Flags missing dates and out-of-bounds dates.
    Returns True if the date is valid, False otherwise.
    """
    raw_date = result.get("Date")
    reasons = []
    
    # 1. Kontrola chybějícího data (Bod 1)
    if raw_date is None or str(raw_date).strip().upper() in ("NULL", "", "NONE"):
        reasons.append("invalid_date_format")
        review_records.append(create_review_record(result, {}, reasons))
        return False
        
    date_str = str(raw_date).strip()
    
    # Sjednocení oddělovačů (původní logika)
    date_str = date_str.replace(".", "-").replace("/", "-")
    result["Date"] = date_str
    
    # Původní striktní regex (žádná tolerance navíc)
    if not re.match(r"^\d{2}-\d{2}-\d{4}$", date_str):
        reasons.append("invalid_date_format")
    else:
        try:
            parsed_date = datetime.strptime(date_str, "%d-%m-%Y")
            
            # 4. Checking if the date is not completely random --> 01-01-0001 is valid date for the format rule, it also exist, but will never be on sheets
            min_date = datetime(1930, 1, 1)
            max_date = datetime(2026, 6, 1)
            
            if parsed_date < min_date or parsed_date > max_date:
                reasons.append("date_out_of_bounds")
                
        except ValueError:
            reasons.append("invalid_date_format")
    
    if reasons:
        review_records.append(create_review_record(result, {}, reasons))
        return False
        
    return True


def enrich_result(
    result: Dict[str, Any],
    lexicon: Dict[str, Dict[str, Any]],
    rule_config: Dict[str, bool],
    review_records: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Enrich result with species matching and validation."""
    import json
    
    result = normalize_species_numbers(result)
    result["enriched_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    if rule_config.get("date_validation", False):
        validate_and_standardize_date(result, review_records)

    species = result.get("species")
    if isinstance(species, list):

        #this is here to skip some species which are not needed
        filtered_species = [sp for sp in species if not is_to_skip_species(sp.get("Name"))]
        result["species"] = filtered_species

        #for sp in species:
        for sp in filtered_species:

            

            name = sp.get("Name")
            month = extract_month(result.get("Date"))
            match, score = best_species_match(name, lexicon)

            month_max_map = match.get("Month_Max", {})
            month_max_for_observed = (
                month_max_map.get(month) if month else None
            )

            sp["OfficialName"] = match.get("Full_Official_Name", "")
            sp["EuringCode"] = match.get("Euring_Code", "")
            sp["RareSpec"] = match.get("Rare_Spec", "")
            sp["MonthMax"] = month_max_for_observed
            sp["ObservedMonth"] = month
            sp["MatchScore"] = score
            sp["MatchedVia"] = match.get("source_name", "")

            if score > 0:
                sp["LevenshteinDistance"] = levenshtein_distance(
                    clean_name(name).lower(),
                    clean_name(sp["MatchedVia"]).lower()
                )

            reasons = analyze_species_for_review(sp, rule_config)
            if reasons:
                review_records.append(create_review_record(result, sp, reasons))

        apply_record_level_rules(result, species, rule_config, review_records)

    return result



# ============================================================================
# CROSS-RECORD RULE: SUDDEN CHANGE
# ============================================================================


def _parse_date_safe(date_str: Optional[str]) -> Optional[datetime]:
    """Parse DD-MM-YYYY date string, return None on failure."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%d-%m-%Y")
    except ValueError:
        return None


def _get_species_key(sp: Dict[str, Any]) -> Optional[str]:
    """Return canonical species key: EuringCode or OfficialName."""
    euring = (sp.get("EuringCode") or "").strip()
    if euring and euring.upper() not in ("NULL", ""):
        return euring
    official = (sp.get("OfficialName") or "").strip()
    return official if official else None


def _detect_sudden_change(
    series: List[Tuple[datetime, int, Dict, Dict]],
    window_days: int,
    max_gap_days: int,
    min_ratio: float,
    min_absolute_diff: int,
    min_required_days: int = 4,
    max_cv: float = 0.6,            # ← NOVÝ PARAMETR: maximální povolená rozkolísanost okolí (0.5 až 0.8 je dobrý start)
    verbose: bool = False,
) -> List[Tuple[Dict, Dict, str]]:
    """
    Given a time-sorted series of (date, count, result, sp_entry),
    split into continuous segments (gap > max_gap_days → new segment)
    and flag entries that are isolated spikes/dips in a STABLE context.
    Returns list of (result, sp_entry, reason) tuples to be flagged.
    """
    if len(series) < 3:
        return []

    # Split into continuous segments
    segments: List[List[Tuple[datetime, int, Dict, Dict]]] = []
    current: List[Tuple[datetime, int, Dict, Dict]] = [series[0]]
    for i in range(1, len(series)):
        gap = (series[i][0] - series[i - 1][0]).days
        if gap > max_gap_days:
            segments.append(current)
            current = [series[i]]
        else:
            current.append(series[i])
    segments.append(current)

    flagged = []
    for segment in segments:
        if len(segment) < 3:
            continue

        for i, (date, count, result, sp) in enumerate(segment):
            
            # Collect counts from surrounding entries within window_days
            surrounding_entries = [
                (other_date, other_count, other_result)  # ← PŘIDÁNO: other_result
                for j, (other_date, other_count, other_result, _) in enumerate(segment)
                if j != i and abs((other_date - date).days) <= window_days
            ]
            surrounding = [c for _, c, _ in surrounding_entries]
            
            # get it from config
            if len(surrounding) < min_required_days:
                continue

            # --- NOVÁ KONTROLA STABILITY (KOEFICIENT VARIACE) ---
            mean_val = _statistics.mean(surrounding)
            if mean_val > 0: # Prevence dělení nulou
                stdev_val = _statistics.stdev(surrounding)
                cv = stdev_val / mean_val
                if cv > max_cv:
                    # Okolí je příliš divoké (např. 10, 40, 1, 50), ignorujeme tento den
                    continue
            # ----------------------------------------------------

            baseline = _statistics.median(surrounding)
            if baseline == 0 or count == 0:
                continue  # Avoid division by zero / noisy zero-days

            # It should work in both cases (spike up; spike down)
            ratio = max(count, baseline) / min(count, baseline)  
            abs_diff = abs(count - baseline)

            if ratio >= min_ratio and abs_diff >= min_absolute_diff:
                if verbose:
                    window_str = ", ".join(
                        f"{d.strftime('%d-%m-%Y')}:{c}"
                        for d, c, _ in sorted(surrounding_entries, key=lambda x: x[0])  
                    )
                    reason = (
                        f"sudden_change | {date.strftime('%d-%m-%Y')}: {count} "
                        f"(baseline: {baseline:.1f}, cv: {cv:.2f}, window: [{window_str}])"
                    )

                    # ========================================================
                    # PRINT WITH DETAILS (ACTIVE ONLY if config is set to verbose: true)
                    # ========================================================

                     # "ALL" for all species to print OR official name
                    TARGET_SPECIES = ["ALL"] #here you can choose which species to print for checkup  
                    
                    sp_name = str(sp.get("OfficialName") or sp.get("EuringCode") or "Unknown").strip()
                    is_target = ("ALL" in TARGET_SPECIES) or any(t.lower() in sp_name.lower() for t in TARGET_SPECIES)
                    
                    if is_target:
                        batch = result.get("batch_id") or result.get("filename") or "unknown_img"
                        custom = result.get("custom_id") or "unknown_id" 
                        current_cv = cv if 'cv' in locals() else 0.0 
                        
                        print(f"\n---> DETAIL: {sp_name} (Batch: {batch} | Custom ID: {custom}): {date.strftime('%d-%m-%Y')}: {count} (baseline: {baseline:.1f}, cv: {current_cv:.2f})")
                        for d, c, r in sorted(surrounding_entries, key=lambda x: x[0]):
                            surr_custom = r.get("custom_id") or "unknown_id"
                            print(f"     {d.strftime('%d-%m-%Y')}: {c} (Custom ID: {surr_custom})")
                    # ========================================================

                else:
                    reason = "sudden_change"

                flagged.append((result, sp, reason))

    return flagged


def process_single_result(raw_result: Dict[str, Any], config: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Process a single raw result and return enriched data and review records.
    This is a helper function for process_batch.
    """
    # Parse raw result text to structured data
    text = raw_result.get("text", "")
    if not text:
        return raw_result, []
    
    # Try to parse JSON from text
    parse_failed = False
    try:
        cleaned = text.strip()
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", cleaned)
        if match:
            try:
                parsed = json.loads(match.group(1))
                structured_data = normalize_llm_output(parsed)
            except json.JSONDecodeError:
                structured_data = {"raw": text}
                parse_failed = True
        else:
            structured_data = {"raw": text}
            parse_failed = True
    except Exception:
        structured_data = {"raw": text}
        parse_failed = True
    
    # Add metadata from raw_result
    structured_data["custom_id"] = raw_result.get("custom_id", "")
    structured_data["batch_id"] = raw_result.get("batch_id", "")
    
    # Get validation config
    validation_config = config.get('validation', {})
    validator_name = validation_config.get('active_validator', 'ocr_sightings')
    validator_settings = validation_config.get(validator_name, {})
    rule_config = validator_settings.get('rules', {})
    lexicon_path = validation_config.get('species_lexicon_path', 'species_lexicon.csv')
    
    # Load lexicon
    lexicon = load_species_lexicon(lexicon_path)
    
    # Collect review records in memory
    review_records: List[Dict[str, Any]] = []
    
    # Enrich result
    enriched = enrich_result(
        structured_data,
        lexicon,
        rule_config,
        review_records
    )
    
    return enriched, review_records


def _apply_sudden_change(
    all_enriched: List[Dict[str, Any]],
    config: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Apply sudden_change detection across all enriched records.
    Returns list of review records to add.
    """
    validation_config = config.get("validation", {})
    validator_name = validation_config.get("active_validator", "ocr_sightings")
    validator_settings = validation_config.get(validator_name, {})
    rule_config = validator_settings.get("rules", {})

    if not rule_config.get("sudden_change", False):
        return []

    # Read optional tuning parameters from config (with sane defaults)
    sc_cfg = validator_settings.get("sudden_change_config", {}) or {}
    window_days      = int(sc_cfg.get("window_days",       3))    # ±N days of context
    min_required_days = int(sc_cfg.get("min_required_days", 4))
    max_gap_days     = int(sc_cfg.get("max_gap_days",      3))    # gap that resets the series
    min_ratio        = float(sc_cfg.get("min_ratio",       2.5))  # spike must be ≥ 2.5× baseline
    min_absolute_diff = int(sc_cfg.get("min_absolute_diff", 3))   # and differ by ≥ 3 birds
    volatility       = float(sc_cfg.get("volatility", 3))
    verbose           = bool(sc_cfg.get("verbose", False))

    # Build time series keyed by (location, species_key)
    series_map: Dict[Tuple[str, str], List[Tuple[datetime, int, Dict, Dict]]] = defaultdict(list)

    for result in all_enriched:
        location = (result.get("Location") or "").strip()
        date = _parse_date_safe(result.get("Date"))
        if not location or not date:
            continue

        species_list = result.get("species")
        if not isinstance(species_list, list):
            continue

        for sp in species_list:
            key = _get_species_key(sp)
            if not key:
                continue

            try:
                count = int(sp.get("Number", ""))
            except (ValueError, TypeError):
                continue

            series_map[(location, key)].append((date, count, result, sp))

    # Detect spikes in each series
    review_records: List[Dict[str, Any]] = []

    for (location, species_key), series in series_map.items():
        series.sort(key=lambda x: x[0])  # sort by date

        flagged = _detect_sudden_change(
            series, window_days, max_gap_days, min_ratio, min_absolute_diff, 
            min_required_days=min_required_days, verbose=verbose, max_cv=volatility
        )
        for result, sp, reason in flagged:
            review_records.append(create_review_record(result, sp, [reason]))

    return review_records






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
        List of tuples (filename, content) for output files:
        - ("results.jsonl", enriched_data_dict)
        - ("review.jsonl", review_record_dict)
        - ("review.csv", csv_row_list)
    """
    # Get name_suffix for filenames
    directories = config.get('directories', {})
    name_suffix = directories.get('name_suffix', '')
    
    # Get validation config
    validation_config = config.get('validation', {})
    validator_name = validation_config.get('active_validator', 'ocr_sightings')
    validator_settings = validation_config.get(validator_name, {})
    
    # Process all raw results
    all_enriched: List[Dict[str, Any]] = []
    all_review_records: List[Dict[str, Any]] = []
    all_problem_records: List[Dict[str, Any]] = []
    
    for raw_result in all_raw_results:
        text = raw_result.get("text", "")
        if not text:
            all_enriched.append(raw_result)
            continue
        
        enriched, review_records = process_single_result(raw_result, config)
        all_enriched.append(enriched)
        all_review_records.extend(review_records)
        
        # Check for parse failure and add to problems
        if enriched.get("raw"):
            custom_id = raw_result.get("custom_id", "")
            batch_id = raw_result.get("batch_id", "")
            problem_record = {
                "custom_id": custom_id,
                "batch_id": batch_id,
                "reason": "JSON_PARSE_FAILED",
                "raw_text": text[:500],
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            }
            all_problem_records.append(problem_record)
    
    # Apply sudden_change detection (cross-record rule)
    sudden_change_reviews = _apply_sudden_change(all_enriched, config)
    all_review_records.extend(sudden_change_reviews)
    
    # Apply deduplication if enabled in config
    if validator_settings.get("deduplicate_review", False):
        all_review_records = _deduplicate_review_records(
            all_review_records,
            identity_keys=[("custom_id",), ("species_entry", "EuringCode"), ("species_entry", "Name")]
        )
    
    # Prepare output files
    outputs: List[Tuple[str, Any]] = []
    
    # Problems JSONL
    if all_problem_records:
        outputs.append(("problems.jsonl", all_problem_records))
    
    # Results JSONL
    outputs.append((f"results{name_suffix}.jsonl", all_enriched))
    
    # Review JSONL (if any review records)
    if all_review_records:
        outputs.append((f"review{name_suffix}.jsonl", all_review_records))
    
    return outputs

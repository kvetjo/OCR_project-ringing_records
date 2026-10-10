"""
Get OCR Results
Downloads and processes OCR batch results from the AI provider.
This script retrieves completed batch jobs, parses results, and validates them.
"""
import sys
import json
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple, Literal
from zoneinfo import ZoneInfo
import traceback
sys.path.insert(0, str(Path(__file__).parent))

from ai_providers.base import load_config, mark_processed_items_downloaded, build_raw_file_path
from ai_providers.provider_selector import get_provider



def get_raw_file_path(raw_dir: Path, batch_id: str, name_suffix: str) -> Path:
    """Cesta k souboru, kam/odkud se ukládají/čtou surová (raw) data daného batche.

    Tvar jména je sdílený s ai_providers/base.py, protože ten samý soubor
    zapisuje i lokální runtime (Ollama) - aby se zapisovatel a čtenář
    nemohli rozejít."""
    return build_raw_file_path(raw_dir, batch_id, name_suffix)

def find_raw_files_for_batch(raw_dir: Path, batch_id: str) -> List[Path]:
    """Najde v raw_dir raw soubory, jejichž jméno obsahuje batch_id - bez ohledu
    na name_suffix nebo jinou strukturu jména. Pokud batch_id obsahuje '/'
    (např. 'batches/xxx'), hledá se jen část za posledním '/', protože lomítko
    se do jména souboru dostat nemůže."""
    search_key = batch_id.rsplit("/", 1)[-1]
    return sorted(p for p in raw_dir.glob("*_raw.jsonl") if search_key in p.name)

# def _resolve_batch_id_for_raw_file(raw_file_path: Path, name_suffix: str) -> str:
#     """Zjistí batch_id patřící k danému raw souboru. Přednostně čte sidecar
#     .meta.json (přesné originální ID), jinak ho odvodí ze jména souboru
#     (může se lišit od originálu, pokud batch_id obsahovalo např. '/')."""
#     suffix_to_strip = f"{name_suffix}_raw.jsonl"
#     name = raw_file_path.name
#     derived_id = name[: -len(suffix_to_strip)] if name.endswith(suffix_to_strip) else raw_file_path.stem
 
#     # meta_path = raw_file_path.with_name(f"{derived_id}{name_suffix}_raw.meta.json")
#     # if meta_path.exists():
#     #     try:
#     #         meta = json.loads(meta_path.read_text(encoding="utf-8"))
#     #         return meta.get("batch_id", derived_id)
#     #     except Exception:
#     #         pass
 
#     return derived_id



def run_validator_and_write(
    all_records,
    batch_id: str,
    status: str,
    process_batch,
    config,
    name_suffix: str,
    output_dir: Path,
    problems_file: Path,
    log_problem_fn,
    is_error_record_fn,
    extract_text_from_record_fn,
    parse_error_records_fn,
    store_error_custom_ids: bool,
    collect_outputs: Optional[list] = None,
) -> Tuple[bool, List[str]]:
    """Vrací (True, custom_ids), pokud validator proběhl v pořádku (výstupy byly
    zapsány), jinak (False, []) - a problém už byl zalogovaný přes log_problem_fn.

    Seznam custom_ids obsahuje záznamy, které se skutečně dostaly do validatoru;
    používá se pro označení položek v processed_items.txt jako stažených.

    collect_outputs (AI post-eval): a list that gets (file name, content) of everything
    written, after a successful write. None (default) = nothing collected. Writing and
    the return value are the same either way."""

    error_count, error_custom_ids = parse_error_records_fn(all_records, store_error_custom_ids)
    if error_count > 0:
        log_problem_fn(
            batch_id, status, "COMPLETED_WITH_ERRORS", str(problems_file),
            error_count=error_count,
            error_custom_ids=error_custom_ids if store_error_custom_ids else None
        )
 
    all_raw_results = []
    for batch_record in all_records:
        custom_id = batch_record.get("custom_id") or batch_record.get("id")
 
        if is_error_record_fn(batch_record):
            error_data = batch_record.get("error", {})
            if isinstance(error_data, dict):
                error_message = error_data.get("message", "Unknown reason")
                error_code = error_data.get("code", "N/A")
                print(f"[DEBUG] Skipping error record: {custom_id} | Reason: [{error_code}] {error_message}")
            else:
                print(f"[DEBUG] Skipping error record: {custom_id} | Reason: {error_data}")
            continue
 
        text = extract_text_from_record_fn(batch_record)
        if not text:
            print(f"[DEBUG] No output_text found for request: {custom_id}")
            try:
                candidates = batch_record.get("response", {}).get("body", {}).get("candidates", [])
                if not candidates:
                    candidates = batch_record.get("response", {}).get("candidates", [])
                if candidates:
                    finish_reason = candidates[0].get("finishReason", "UNKNOWN")
                    print(f"[DEBUG] -> Reason for missing text (finishReason): {finish_reason}")
            except Exception:
                pass
            continue
 
        all_raw_results.append({"text": text, "custom_id": custom_id, "batch_id": batch_id})
 
    try:
        outputs = process_batch(all_raw_results, config)
 
        # APLIKACE SUFFIXU: Vloží suffix před příponu
        suffixed_outputs = []
        for fname, content in outputs:
            if fname == "problems.jsonl":
                suffixed_outputs.append((fname, content))
            else:
                p = Path(fname)
                if name_suffix in p.stem:
                    new_fname = fname
                else:
                    new_fname = f"{p.stem}{name_suffix}{p.suffix}"
                suffixed_outputs.append((new_fname, content))
 
        write_validator_outputs(suffixed_outputs, output_dir, problems_file)
        if collect_outputs is not None:
            collect_outputs.extend(suffixed_outputs)

        processed_custom_ids = [
            r.get("custom_id") for r in all_raw_results if r.get("custom_id")
        ]
        return True, processed_custom_ids

    except Exception as e:
        print("[ERROR] Validator process_batch failed:", e)
        print("ERROR DETAILS:")
        traceback.print_exc()
        log_problem_fn(
            batch_id, status, reason="VALIDATOR_FAILED", problems_file=str(problems_file),
            error_count=len(all_raw_results),
            error_custom_ids=[r.get("custom_id") for r in all_raw_results]
        )
        return False, []


def ensure_dirs(state_dir: Path, output_dir: Path):
    """Ensure state and output directories exist."""
    state_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)


def build_download_history(previous_entry, downloaded_time: str) -> List[str]:
    """Sestaví seznam časů stažení pro daný batch.

    Stažení téhož batche znovu je běžné a povolené - proto se čas nepřepisuje,
    ale přidává. Starší stavové soubory měly na tomto klíči jeden řetězec;
    ten se převede na seznam, aby se historie neztratila."""
    if not previous_entry:
        return [downloaded_time]

    previous_value = previous_entry.get("date_and_time_downloaded")

    if previous_value is None:
        return [downloaded_time]

    if isinstance(previous_value, list):
        return previous_value + [downloaded_time]

    return [previous_value, downloaded_time]


def merge_batch_entry(previous_entry, new_values: dict) -> dict:
    """Sloučí nové hodnoty do existujícího záznamu v processed_batches.json.

    None znamená "v tomto běhu neznámé" (např. process_only nemá batch_job,
    nebo selhal dotaz na API) - známou hodnotu z dřívějška proto nikdy
    nepřepíše. U nového záznamu se klíč s None založí, aby měly všechny
    záznamy stejný tvar."""
    entry = dict(previous_entry or {})
    for key, value in new_values.items():
        if value is not None or key not in entry:
            entry[key] = value
    return entry


def collect_api_metadata(provider, batch_job, api_key: str, all_records) -> dict:
    """Metadata batche, která zná jen API, plus tokeny ze stažených záznamů.

    Hodnota None znamená "nepodařilo se zjistit" - merge_batch_entry ji
    pak nepoužije a ponechá dřívější hodnotu."""
    completed_requests, failed_requests = provider['get_request_counts'](batch_job, api_key)

    # Tokeny se počítají ze záznamů, které už jsou v paměti. API (u Gemini
    # druhé stažení téhož souboru) jen tehdy, když záznamy usage nenesou.
    tokens_data = provider['get_tokens_from_records'](all_records)
    if tokens_data is None:
        tokens_data = provider['get_batch_tokens'](batch_job, api_key)

    processing_time_seconds = provider['get_processing_time'](batch_job)

    return {
        "model": provider['get_model_name'](batch_job),
        "processing_time_minutes": round(processing_time_seconds / 60, 2) if processing_time_seconds else None,
        "date_and_time_created": provider['get_batch_created_time'](batch_job),
        "date_and_time_completed": provider['get_batch_completed_time'](batch_job),
        "completed_requests": completed_requests,
        "failed_requests": failed_requests,
        "tokens_consumption": tokens_data,
    }


def get_display_name(name_suffix: str) -> str:
    """Get display name from name_suffix, removing leading dash/underscore if present."""
    if not name_suffix:
        return ""
    # Remove leading dash or underscore
    if name_suffix.startswith(('-', '_')):
        return name_suffix[1:]
    return name_suffix


def parse_dd_mm_yyyy(date_str: str) -> datetime:
    """Parse date string in DD-MM-YYYY format."""
    try:
        return datetime.strptime(date_str, "%d-%m-%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError(
            f"Invalid date format '{date_str}'. Expected DD-MM-YYYY."
        )




def write_validator_outputs(outputs: List[Tuple[str, object]], output_dir: Path, problems_file: Optional[Path] = None):
    """Write multiple output files from validator.

    Soubory se otevírají v režimu "a" (append) záměrně - díky tomu se dá
    zpracovat několik batchů za sebou do jednoho výsledkového souboru.

    Args:
        outputs: List of (filename, content) tuples from validator
        output_dir: Directory to write files to
        problems_file: Path to problems file (for problems.jsonl output)
    """
    for filename, content in outputs:
        # Special handling for problems.jsonl - write to problems_file
        if filename == "problems.jsonl" and problems_file:
            if isinstance(content, dict):
                with open(problems_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(content, ensure_ascii=False) + "\n")
            elif isinstance(content, list):
                with open(problems_file, "a", encoding="utf-8") as f:
                    for item in content:
                        f.write(json.dumps(item, ensure_ascii=False) + "\n")
            continue
        
        filepath = output_dir / filename
        
        if filename.endswith('.jsonl'):
            # Write JSONL line
            if isinstance(content, dict):
                with open(filepath, "a", encoding="utf-8") as f:
                    f.write(json.dumps(content, ensure_ascii=False) + "\n")
            elif isinstance(content, list):
                # Write list of dicts as JSONL
                with open(filepath, "a", encoding="utf-8") as f:
                    for item in content:
                        f.write(json.dumps(item, ensure_ascii=False) + "\n")
        
        elif filename.endswith('.csv'):
            # Write CSV rows - supports both dict format (with header) and list format (without header)
            if isinstance(content, dict) and "rows" in content and "header" in content:
                # New format: dict with rows and header defined by validator
                rows = content["rows"]
                header = content["header"]
                write_header = not filepath.exists()
                with open(filepath, "a", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    if write_header:
                        writer.writerow(header)
                    for row in rows:
                        writer.writerow(row)
            elif isinstance(content, list):
                # Legacy format: list of rows without header (backward compatibility)
                write_header = not filepath.exists()
                with open(filepath, "a", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    for row in content:
                        writer.writerow(row)
        
        else:
            # Write as JSON
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(content, ensure_ascii=False) + "\n")






def main(
    process_from: Optional[str] = None,
    batch_ids: Optional[List[str]] = None,
    mode: Literal["download_only", "process_only", "full", "standard"] = "standard",
    ai_post_eval: bool = False,
):
    """Main function to retrieve and process OCR results.

    Args:
        process_from (str, optional): Date string in DD-MM-YYYY format. Only processes batches 
            created on or after this date. Cannot be used with mode="process_only".
        batch_ids (list of str, optional): Explicit list of batch IDs to process. 
            Required when using mode="process_only".
        mode (str): Execution mode determining data sourcing and processing flow.
            - "standard"      – (default, original behavior) downloads via API and processes immediately
            - "download_only" – downloads raw data via API and saves it to disk
            - "process_only"  – DOES NOT USE API. Processes raw data already located in raw_dir_base.
                                Requires 'batch_ids' to be explicitly provided.
            - "full"          – downloads via API, saves to disk, AND processes simultaneously
        ai_post_eval (bool): AI check (ai_post_evaluation/) of the results written in this run,
            at the very end - modes standard / full / process_only (download_only validates
            nothing, so it never runs there). Runs only when ai_post_eval.turn_on in config.yaml
            is on too; if only one of the two is on, the script stops before anything is processed.
    """

    # Validation for process_only mode
    if mode == "process_only":
        if process_from is not None:
            print("[ERROR] Cannot run mode='process_only' together with 'process_from' parameter. Date filtering requires API connection.")
            return
            
        if not batch_ids:
            print("[ERROR] Missing 'batch_ids'. When using mode='process_only', you must provide a list of batch IDs to process. Processing all raw files is not allowed.")
            return

    config = load_config()

    run_settings = config.get('run_settings', {})
    active_provider = run_settings.get('active_provider', 'gemini')
    model_name = run_settings.get('model_name', 'gemini-3.1-pro-preview')
 
    provider = get_provider(active_provider)
 
    directories = config.get('directories', {})
    state_dir_base = directories.get('state_dir_base', 'state')
    output_dir_base = directories.get('output_dir_base', 'output')
    raw_dir_base = directories.get('raw_dir_base', 'data_output/raw')  # NOVÉ
    name_suffix = directories.get('name_suffix', '-FB_scraping')
    processed_log_file = directories.get('processed_log_file', 'processed_items.txt')

    # ========================================================================
    # ZDROJ DAT: lokální runtime (Ollama) nemá batch API - hotový batch vždycky
    # leží jako soubor v raw_dir_base. Mění se JEN to, odkud se data berou;
    # všechno pod tímhle blokem běží úplně stejně jako dosud.
    # ========================================================================
    if provider.get('local_runtime') and mode != "process_only":
        print(f"[INFO] Provider '{active_provider}' runs locally -> results are read from '{raw_dir_base}' (mode='{mode}' -> 'process_only')")
        mode = "process_only"

        if process_from is not None:
            print("[ERROR] 'process_from' cannot be used with a local provider: there is no batch API to filter by date.")
            return

        if not batch_ids:
            print("[ERROR] Missing 'batch_ids'. Pass the id printed by make_OCR_batch.py, e.g. \"ollama-a3f2...\".")
            return

    validation_config = config.get('validation', {})
    active_validator = validation_config.get('active_validator', 'ocr_sightings')
 
    if active_validator == 'ocr_sightings':
        from validators.ocr_sightings import process_batch
    elif active_validator == 'ocr_retrap':
        from validators.ocr_retrap import process_batch
    elif active_validator == 'ocr_ringing':
        from validators.ocr_ringing import process_batch
    elif active_validator == 'ocr_ringing-STORM_PETREL':
            from validators.ocr_ringing import process_batch
    else:
        print(f"ERROR: Unknown validator '{active_validator}'")
        print("I think you forgot to add a line with new validator into 'get_OCR_results.py' file. Something like if active_validator =='new': import")
        print("Yes it is stupid - TODO: you should fix it so it does not need to be named here... Sorry..")
        sys.exit(1)
 
    state_dir = Path(state_dir_base)
    output_dir = Path(output_dir_base)
    raw_dir = Path(raw_dir_base)  # NOVÉ
    processed_log_path = Path(processed_log_file)

    # AI post-eval (off by default): the ai_post_eval parameter AND ai_post_eval.turn_on
    # in config.yaml must both be on; never runs in download_only (nothing is validated).
    # Checked here, before anything is processed. None = off -> everything below runs exactly as before.
    from ai_post_evaluation import post_eval_runner
    ai_post_eval_job = post_eval_runner.prepare(config, ai_post_eval, mode, active_validator, output_dir, name_suffix)
    # what the validator writes in this run, for the AI check (None = AI off -> nothing is kept)
    ai_post_eval_outputs = [] if ai_post_eval_job is not None else None

    ensure_dirs(state_dir, output_dir)
    if mode in ("download_only", "full"):
        raw_dir.mkdir(parents=True, exist_ok=True)
 
    processed_batches_file = state_dir / "processed_batches.json"
    problems_file = state_dir / f"problems_to_deal_with{name_suffix}.jsonl"
 
    store_error_custom_ids = True
 
    process_from_dt = None
    if process_from:
        process_from_dt = parse_dd_mm_yyyy(process_from)
        print(f"[INFO] Processing batches created from {process_from_dt.isoformat()}")
 
    batch_ids_set = None
    if batch_ids:
        batch_ids_set = set(batch_ids)
        print(f"[INFO] Processing only batch IDs: {batch_ids_set}")
 
    print(f"[INFO] Running in mode='{mode}'")
 
    # Provider funkce potřebné ve všech módech (i process_only - žádná z nich nevolá API)
    load_processed_batches_fn = provider['load_processed_batches']
    save_processed_batches_fn = provider['save_processed_batches']
    log_problem_fn = provider['log_problem']
    is_error_record_fn = provider['is_error_record']
    extract_text_from_record_fn = provider['extract_text_from_record']
    parse_error_records_fn = provider['parse_error_records']
    get_tokens_from_records_fn = provider['get_tokens_from_records']

    processed = load_processed_batches_fn(str(processed_batches_file))
 
    # ========================================================================
    # MODE: process_only - čistě diskové zpracování, ŽÁDNÉ API, ŽÁDNÝ api_key
    # ========================================================================
    if mode == "process_only":
        parse_raw_content_fn = provider.get('parse_raw_content')
        if parse_raw_content_fn is None:
            print(f"[ERROR] Provider '{active_provider}' neimplementuje 'parse_raw_content' (potřeba pro mode='process_only')")
            sys.exit(1)
 
        if not raw_dir.exists():
            print(f"[INFO] raw_dir_base '{raw_dir}' neexistuje, není co zpracovávat (mode=process_only)")
            print("[INFO] Results retrieval completed")
            return
 
        # raw_files = sorted(raw_dir.glob(f"*{name_suffix}_raw.jsonl"))
        # if not raw_files:
        #     print(f"[INFO] V '{raw_dir}' nejsou žádné raw soubory ke zpracování (mode=process_only)")
 
        # for raw_file_path in raw_files:
        #     batch_id = _resolve_batch_id_for_raw_file(raw_file_path, name_suffix)
 
        #     if batch_ids_set is not None and batch_id not in batch_ids_set:
        #         print(f"[INFO] Skipping raw file {raw_file_path.name} (batch {batch_id} not in batch_id filter)")
        #         continue
 
        #     print(f"[INFO] Processing raw file for batch {batch_id}: {raw_file_path}")

        for batch_id in sorted(batch_ids_set):
            matches = find_raw_files_for_batch(raw_dir, batch_id)

            if not matches:
                print(f"[WARNING] V '{raw_dir}' není žádný raw soubor obsahující batch_id '{batch_id}', přeskakuji")
                continue

            if len(matches) > 1:
                names = ", ".join(p.name for p in matches)
                print(f"[WARNING] batch_id '{batch_id}' odpovídá více raw souborům ({names}) - nejasné, který zpracovat, přeskakuji")
                continue

            raw_file_path = matches[0]
            print(f"[INFO] Processing raw file for batch {batch_id}: {raw_file_path}")
 
            raw_text = raw_file_path.read_text(encoding="utf-8")
            try:
                all_records = parse_raw_content_fn(raw_text)
            except Exception as e:
                print(f"[ERROR] Nepodařilo se naparsovat raw soubor {raw_file_path}: {e}")
                log_problem_fn(batch_id, "completed", "PARSE_RAW_FAILED", str(problems_file))
                continue
 
            if not all_records:
                log_problem_fn(batch_id, "completed", "COMPLETED_NO_OUTPUT", str(problems_file))
                continue
 
            ok, processed_custom_ids = run_validator_and_write(
                all_records, batch_id, "completed", process_batch, config, name_suffix,
                output_dir, problems_file, log_problem_fn, is_error_record_fn,
                extract_text_from_record_fn, parse_error_records_fn, store_error_custom_ids,
                collect_outputs=ai_post_eval_outputs,
            )
            if not ok:
                continue

            downloaded_time = datetime.now(ZoneInfo("Europe/Malta")).strftime("%d-%m-%Y : %H-%M-%S")
            previous_entry = processed.get(batch_id)
            error_count, _ = parse_error_records_fn(all_records, False)

            # merge_batch_entry: co tu je None (bez batch_job neznámé), převezme
            # se z dřívějšího záznamu - process_only tak nic nesmaže
            processed[batch_id] = merge_batch_entry(previous_entry, {
                "batch_id": batch_id,
                "display_name": get_display_name(name_suffix),
                # skutečný model zná jen API; config je jen záloha pro úplně nový záznam
                "model": (previous_entry or {}).get("model") or model_name,
                "provider": active_provider,
                "active_validator": active_validator,
                "processing_time_minutes": None,    # neznámé bez batch_job
                "date_and_time_created": None,      # neznámé bez batch_job
                "date_and_time_completed": None,    # neznámé bez batch_job
                # seznam všech časů stažení, ne jen posledního
                "date_and_time_downloaded": build_download_history(previous_entry, downloaded_time),
                "completed_requests": len(all_records) - error_count,
                "failed_requests": error_count,
                "tokens_consumption": get_tokens_from_records_fn(all_records),
            })
            save_processed_batches_fn(processed, str(processed_batches_file))

            # Informativní log: označ dané requesty jako stažené a zpracované
            mark_processed_items_downloaded(
                processed_log_path, batch_id, processed_custom_ids, active_validator, downloaded_time
            )

        print("[INFO] Results retrieval completed (process_only)")
        if ai_post_eval_job is not None:
            post_eval_runner.run(ai_post_eval_job, ai_post_eval_outputs)
        return
 
    # ========================================================================
    # MODE: standard / download_only / full - přes API (list_batches_fn)
    # ========================================================================
    api_keys = config.get('api_keys', {})
    api_key = api_keys.get(active_provider, '')
    if not api_key:
        print(f"ERROR: API key for provider '{active_provider}' not found in config.yaml")
        sys.exit(1)
 
    list_batches_fn = provider['list_batches']
    get_batch_id_fn = provider['get_batch_id']
    get_batch_status_fn = provider['get_batch_status']
    parse_batch_results_fn = provider['parse_batch_results']
    batch_created_after_fn = provider['batch_created_after']
 
    download_raw_batch_fn = provider.get('download_raw_batch')
    if mode in ("download_only", "full") and download_raw_batch_fn is None:
        print(f"[ERROR] Provider '{active_provider}' neimplementuje 'download_raw_batch' (potřeba pro mode='{mode}')")
        sys.exit(1)
 
    parse_raw_content_fn = provider.get('parse_raw_content')
    if mode == "full" and parse_raw_content_fn is None:
        print(f"[ERROR] Provider '{active_provider}' neimplementuje 'parse_raw_content' (potřeba pro mode='full')")
        sys.exit(1)
 
    batches = list_batches_fn(api_key)
 
    for batch_job in batches:
        batch_id = get_batch_id_fn(batch_job)
        status = get_batch_status_fn(batch_job)
 
        if batch_ids_set is not None and batch_id not in batch_ids_set:
            print(f"[INFO] Skipping batch {batch_id} (not in batch_id filter)")
            continue
 
        if process_from_dt is not None:
            if not batch_created_after_fn(batch_job, process_from_dt):
                print(f"[INFO] Skipping batch {batch_id} (too old)")
                continue
 
        print(batch_job)
        print(f"[INFO] Batch {batch_id} → {status}")
 
        if status == "failed":
            log_problem_fn(batch_id, status, "FAILED", str(problems_file))
            continue
 
        if status == "in_progress":
            print("[INFO] Batch is still in progress")
            continue
 
        if status != "completed":
            print(f"[WARNING] Batch {batch_id} has bad type '{status}', writing it into problems.jsonl")
            log_problem_fn(batch_id, status, f"NOT_COMPLETED_STATUS_{str(status).upper()}", str(problems_file))
            continue
 
        raw_file_path = get_raw_file_path(raw_dir, batch_id, name_suffix)
 
        # --------------------------------------------------------------
        # Získání all_records podle módu (process_only sem už nepatří)
        # --------------------------------------------------------------
        if mode in ("download_only", "full"):
            if raw_file_path.exists():
                print(f"[INFO] Raw soubor pro batch {batch_id} už existuje, stahování přeskočeno: {raw_file_path}")
                raw_text = raw_file_path.read_text(encoding="utf-8")
            else:
                try:
                    raw_text = download_raw_batch_fn(batch_job, api_key)
                except Exception as e:
                    print(f"[ERROR] Failed to download raw batch data for {batch_id}: {e}")
                    log_problem_fn(batch_id, status, "DOWNLOAD_FAILED", str(problems_file))
                    continue
 
                if not raw_text:
                    log_problem_fn(batch_id, status, "COMPLETED_NO_OUTPUT", str(problems_file))
                    continue
 
                raw_file_path.write_text(raw_text, encoding="utf-8")
                # # sidecar s přesným batch_id (viz _resolve_batch_id_for_raw_file)
                # get_raw_meta_path(raw_dir, batch_id, name_suffix).write_text(
                #     json.dumps({"batch_id": batch_id}, ensure_ascii=False), encoding="utf-8"
                # )
                print(f"[INFO] Raw data pro batch {batch_id} uložena do {raw_file_path}")
 
            if mode == "download_only":
                # Validator se nespouští, ale metadata z API se uloží hned - jinak
                # by je batch zpracovaný později přes process_only nikdy neměl.
                # Čas stažení a validator se nezapisují, nic se nezpracovalo.
                previous_entry = processed.get(batch_id)
                api_values = collect_api_metadata(provider, batch_job, api_key, parse_raw_content_fn(raw_text))
                processed[batch_id] = merge_batch_entry(previous_entry, {
                    "batch_id": batch_id,
                    "display_name": get_display_name(name_suffix),
                    "model": api_values["model"] or (previous_entry or {}).get("model") or model_name,
                    "provider": active_provider,
                    "active_validator": None,
                    "processing_time_minutes": api_values["processing_time_minutes"],
                    "date_and_time_created": api_values["date_and_time_created"],
                    "date_and_time_completed": api_values["date_and_time_completed"],
                    "date_and_time_downloaded": None,
                    "completed_requests": api_values["completed_requests"],
                    "failed_requests": api_values["failed_requests"],
                    "tokens_consumption": api_values["tokens_consumption"],
                })
                save_processed_batches_fn(processed, str(processed_batches_file))
                continue  # v tomto módu se dál nic nezpracovává
 
            try:
                all_records = parse_raw_content_fn(raw_text)
            except Exception as e:
                print(f"[ERROR] Nepodařilo se naparsovat stažená raw data pro batch {batch_id}: {e}")
                log_problem_fn(batch_id, status, "PARSE_RAW_FAILED", str(problems_file))
                continue
 
        else:  # mode == "standard" - původní chování, beze změny
            try:
                all_records = parse_batch_results_fn(batch_job, api_key)
            except Exception as e:
                print(f"[ERROR] Failed to download/parse batch results: {e}")
                log_problem_fn(batch_id, status, "DOWNLOAD_FAILED", str(problems_file))
                continue
 
        if not all_records:
            log_problem_fn(batch_id, status, "COMPLETED_NO_OUTPUT", str(problems_file))
            continue
 
        ok, processed_custom_ids = run_validator_and_write(
            all_records, batch_id, status, process_batch, config, name_suffix,
            output_dir, problems_file, log_problem_fn, is_error_record_fn,
            extract_text_from_record_fn, parse_error_records_fn, store_error_custom_ids,
            collect_outputs=ai_post_eval_outputs,
        )
        if not ok:
            continue

        # Mark batch as processed with full metadata (batch_job je tu vždy k dispozici).
        # merge_batch_entry: co se z API nepodařilo zjistit (None), zůstane z dřívějška
        api_values = collect_api_metadata(provider, batch_job, api_key, all_records)
        downloaded_time = datetime.now(ZoneInfo("Europe/Malta")).strftime("%d-%m-%Y : %H-%M-%S")
        previous_entry = processed.get(batch_id)

        processed[batch_id] = merge_batch_entry(previous_entry, {
            "batch_id": batch_id,
            "display_name": get_display_name(name_suffix),
            "model": api_values["model"] or (previous_entry or {}).get("model") or model_name,
            "provider": active_provider,
            "active_validator": active_validator,
            "processing_time_minutes": api_values["processing_time_minutes"],
            "date_and_time_created": api_values["date_and_time_created"],
            "date_and_time_completed": api_values["date_and_time_completed"],
            # seznam všech časů stažení, ne jen posledního
            "date_and_time_downloaded": build_download_history(previous_entry, downloaded_time),
            "completed_requests": api_values["completed_requests"],
            "failed_requests": api_values["failed_requests"],
            "tokens_consumption": api_values["tokens_consumption"],
        })
        save_processed_batches_fn(processed, str(processed_batches_file))

        # Informativní log: označ dané requesty jako stažené a zpracované
        mark_processed_items_downloaded(
            processed_log_path, batch_id, processed_custom_ids, active_validator, downloaded_time
        )

    print("[INFO] Results retrieval completed")

    if ai_post_eval_job is not None:
        post_eval_runner.run(ai_post_eval_job, ai_post_eval_outputs)

# main options:    - process_from (input date) OR batch_ids (input list of ids)

# "standard"      – (default, original behavior) downloads via API and processes immediately
# "download_only" – downloads raw data via API and saves it to disk
# "process_only"  – DOES NOT USE API. Processes raw data already located in raw_dir_base.
#                  Requires 'batch_ids' to be explicitly provided.
# "full"          – downloads via API, saves to disk, AND processes simultaneously

# ai_post_eval=True – AI check of this run's dataset at the end (not in download_only); config.yaml ai_post_eval.turn_on must be True too

#s2re3y87ta6vhxtvh4k92bm9vujuarompdtu-ringing-subalpine_warblers-full_raw

if __name__ == "__main__":
    # Example usage - modify as needed
    list_of_batches = ["u466drvewbryfwnbc2z89ba3o89628vapqat"]  #["yxng42vyvaskibz0x8g5hf4plevffyre3xvs"]
    #list_of_batches = ["0h4d3txh88hn3pxcl3dp1n9xh1js5gy4seye", "18b0xrzquc6jbhc081f1eonfc4lklzyfwlem"] #m3cqq91lfnu3xroohn9s7nc1svpwadp35hnv batch_69f22fb0661481909ec77b468f115251
    main(batch_ids=list_of_batches, mode="process_only", ai_post_eval=True)

    #main(process_from="06-07-2026", mode="download_only")
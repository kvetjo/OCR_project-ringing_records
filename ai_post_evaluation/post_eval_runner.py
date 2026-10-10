"""
post_eval_runner.py

Connects the AI post-evaluation (openai_post_eval.py / gemini_post_eval.py) to the end of
get_OCR_results.py. Settings: section ai_post_eval in config.yaml.
choose_provider: local_no_ai runs the rule-based silent_errors.py instead - no AI, no prompt
(prompts_file_path / active_prompt_key are ignored), everything else works the same.

    prepare()  - start of main(), before anything is processed: checks both switches and the
                 ai_post_eval section. Returns None when the AI check does not run (switches
                 off, or mode="download_only", which validates nothing).
    run()      - end of main(): gets what the validator wrote in THIS run (collected by
                 run_validator_and_write()), sends the results to the chosen provider script;
                 with append_to_existing_review_file the AI reasons and details are added to
                 this run's review rows (matched by custom_id + id_row).

The provider scripts are used unchanged. Prompt and output folder from config.yaml are set on
the imported module only for the duration of the call (they are module-level constants there),
so the scripts' own CONFIG blocks and their standalone run stay as they are.
"""

import importlib
import json
import logging
import os
import sys
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# CONFIG
# ============================================================

# choose_provider in config.yaml -> provider script in this folder
PROVIDER_MODULES = {
    "openai": "ai_post_evaluation.openai_post_eval",
    "gemini": "ai_post_evaluation.gemini_post_eval",
    "local_no_ai": "ai_post_evaluation.local_post_eval"
}

# Providers without AI: rule-based, no prompt -> prompts_file_path / active_prompt_key are ignored,
# and they are called through run_silent_errors() with parameters instead of patched module constants.
LOCAL_PROVIDERS = {"local_no_ai"}

# The validator writes results.jsonl / review.jsonl, get_OCR_results.py adds name_suffix
# (results-<suffix>.jsonl, review-<suffix>.jsonl). Change here if a validator names them differently.
RESULTS_FILE_STEM = "results"
REVIEW_FILE_STEM = "review"

# AI "reasons" are appended to the review row's "reasons". These fields of the AI record go to the
# review row under AI_DETAILS_KEY - a list, one entry per AI record, so nothing gets overwritten
# when the AI flags the same row more than once. suspicious_fields says which field suggested_value is for.
AI_DETAILS_KEY = "post_eval"
AI_FIELDS_TO_REVIEW = ("suspicious_fields", "suggested_value", "ai_note")
# The local rule-based check puts its details under its own key, so in review it is clear they
# do not come from an AI. Set it to AI_DETAILS_KEY to keep both under one key.
LOCAL_DETAILS_KEY = AI_DETAILS_KEY

# ============================================================


def prepare(config, main_switch, mode, active_validator, output_dir, name_suffix):
    """Returns None (AI check does not run) or a dict for run().

    Stops the script when only one of the two switches is on or the ai_post_eval section is
    wrong - here nothing has been processed yet, after the workflow it would be too late."""
    section = config.get("ai_post_eval") or {}
    config_switch = section.get("turn_on")

    # download_only validates nothing -> no results to check. The switches are not checked here
    # either, so a plain download does not need ai_post_eval=True when turn_on is on in config.yaml.
    if mode == "download_only":
        if main_switch or config_switch:
            print("[INFO] AI post-eval is not used in mode='download_only' (no validation -> nothing to check)")
        return None

    if config_switch is None:
        config_switch = False
    if not isinstance(config_switch, bool):
        _stop(f"ai_post_eval.turn_on in config.yaml must be true or false, got {config_switch!r}.")
    main_switch = bool(main_switch)

    if not main_switch and not config_switch:
        return None
    if main_switch != config_switch:
        _stop(f"the two switches disagree - main(ai_post_eval={main_switch}) but ai_post_eval.turn_on "
              f"in config.yaml is {config_switch}. Turn both on to run the AI check, or both off.")

    for_validators = section.get("for_validators") or []
    if isinstance(for_validators, str):
        for_validators = [for_validators]
    if not for_validators:
        _stop("ai_post_eval.for_validators in config.yaml is empty - list the validators the AI check is for.")
    if active_validator not in for_validators:
        print(f"[WARNING] AI post-eval is on, but the active validator '{active_validator}' is not in "
              f"ai_post_eval.for_validators {for_validators} -> AI check skipped in this run")
        return None

    provider = str(section.get("choose_provider") or "").strip().lower()
    if provider not in PROVIDER_MODULES:
        _stop(f"ai_post_eval.choose_provider must be one of {list(PROVIDER_MODULES)}, "
              f"got {section.get('choose_provider')!r}.")
    try:
        module = importlib.import_module(PROVIDER_MODULES[provider])
    except Exception as e:
        _stop(f"cannot import {PROVIDER_MODULES[provider]}: {e}")

    local = provider in LOCAL_PROVIDERS

    # null in config -> the provider script's own default (its CONFIG block)
    prompts_path = _optional(section.get("prompts_file_path"))
    prompts_path = Path(prompts_path) if prompts_path else None
    prompt_key = _optional(section.get("active_prompt_key"))
    if local:
        prompts_path = prompt_key = None                # no AI -> no prompt, both settings ignored
    else:
        try:
            module.load_prompts(prompts_path, prompt_key)   # the provider's own loader -> a typo fails now
        except Exception as e:
            _stop(f"cannot load the prompt: {e}")

    if provider == "gemini" and getattr(module, "UPLOAD_AS_CSV", False) and prompt_key and prompt_key != module.PROMPT_KEY:
        print(f"[WARNING] gemini_post_eval.py uploads the data as CSV (UPLOAD_AS_CSV = True) and its own prompt for "
              f"that is '{module.PROMPT_KEY}', but config uses '{prompt_key}' - make sure that prompt describes CSV")

    output_path = _optional(section.get("output_path"))
    flags_dir = Path(output_path) if output_path else None
    append_to_review = section.get("append_to_existing_review_file")
    if append_to_review is None:
        append_to_review = False
    if not isinstance(append_to_review, bool):
        _stop(f"ai_post_eval.append_to_existing_review_file must be true or false, got {append_to_review!r}.")
    if flags_dir is None and not append_to_review:
        _stop("output_path is null and append_to_existing_review_file is false - the AI flags would not be saved anywhere.")
    if flags_dir is not None and not flags_dir.exists():
        try:
            flags_dir.mkdir(parents=True)
        except OSError as e:
            _stop(f"cannot create the output_path folder {flags_dir}: {e}")
        print(f"[INFO] AI post-eval: created folder {flags_dir.resolve()}")

    results_path = output_dir / f"{RESULTS_FILE_STEM}{name_suffix}.jsonl"
    review_path = output_dir / f"{REVIEW_FILE_STEM}{name_suffix}.jsonl"

    if local:
        print(f"[INFO] AI post-eval ON: provider={provider} (rule-based, no AI - prompt settings ignored)")
    else:
        print(f"[INFO] AI post-eval ON: provider={provider}, prompt '{prompt_key or module.PROMPT_KEY}' "
              f"from {prompts_path or module.PROMPTS_PATH}")
    print(f"[INFO] AI post-eval output: flags file -> {flags_dir.resolve() if flags_dir else 'not saved'}, "
          f"review -> {review_path.name if append_to_review else 'not changed'}")

    return {
        "provider": provider,
        "module": module,
        "local": local,
        "details_key": LOCAL_DETAILS_KEY if local else AI_DETAILS_KEY,
        "prompts_path": prompts_path,
        "prompt_key": prompt_key,
        "flags_dir": flags_dir,
        "append_to_review": append_to_review,
        "results_path": results_path,
        "review_path": review_path,
    }


def run(job, validator_outputs):
    """AI check of what the validator wrote in this run (+ review update).

    validator_outputs - (file name, content) of everything run_validator_and_write() wrote in this
    run, collected by get_OCR_results.py. Errors are printed, not raised: the OCR part of the run
    is finished and saved by now."""
    print(f"[INFO] AI post-eval ({job['provider']}) started")
    try:
        records = _written_rows(validator_outputs, job["results_path"].name)
        if not records:
            written = sorted({name for name, _ in validator_outputs})
            print(f"[INFO] AI post-eval: no results written in this run - nothing to check (files written: {written})")
            return

        review_rows = _written_rows(validator_outputs, job["review_path"].name)
        result = _call_provider(job, records, review_rows)
        ai_records = result.get("output_records") or []
        status = result.get("response_status")
        print(f"[INFO] AI post-eval: {len(records)} record(s) checked -> {len(ai_records)} flagged row(s), "
              f"response status '{status}'")

        if job["append_to_review"] and ai_records:
            if status == "completed":
                _append_to_review(job["review_path"], review_rows, ai_records, records, job["details_key"])
            else:
                saved = result.get("output_path") or "not saved (output_path is null)"
                print(f"[WARNING] AI post-eval: the response did not complete, its output may be incomplete -> "
                      f"review file left unchanged. AI output: {saved}")

        print("[INFO] AI post-eval completed")
    except Exception as e:
        print(f"[ERROR] AI post-eval failed: {e}")
        traceback.print_exc()
        print("[INFO] The OCR part of this run is finished and saved - only the AI post-eval step failed")


def _written_rows(validator_outputs, file_name):
    """Rows written into file_name in this run - same rule as write_validator_outputs():
    a dict is one line, a list is one line per item."""
    rows = []
    for name, content in validator_outputs:
        if name == file_name:
            if isinstance(content, dict):
                rows.append(content)
            elif isinstance(content, list):
                rows.extend(content)
    return rows


def _call_provider(job, records, review_rows):
    """run_post_ai_eval() of the provider script with prompt / output folder from config.yaml,
    or run_silent_errors() of the local rule-based script."""
    module = job["module"]
    if job["local"]:
        # Everything goes in as parameters, so the script's CONFIG stays for its standalone run.
        # review_jsonl = this run's review rows -> rows already in review are left out of the check;
        # record_shape "post_eval" = the record shape _append_to_review reads.
        return module.run_silent_errors(input_jsonl=records,
                                        review_jsonl=review_rows,
                                        record_shape="post_eval",
                                        save_to_disk=job["flags_dir"] is not None,
                                        output_dir=job["flags_dir"])

    _show_provider_logs(module)

    overrides = {"PROMPTS_PATH": job["prompts_path"], "PROMPT_KEY": job["prompt_key"], "OUTPUT_DIR": job["flags_dir"]}
    overrides = {name: value for name, value in overrides.items() if value is not None}
    originals = {name: getattr(module, name) for name in overrides}
    for name, value in overrides.items():
        setattr(module, name, value)
    try:
        return module.run_post_ai_eval(input_jsonl=records, save_to_disk=job["flags_dir"] is not None)
    finally:
        for name, value in originals.items():
            setattr(module, name, value)


def _show_provider_logs(module):
    """The provider scripts report progress through logging (status every few minutes, tokens,
    saved file). Standalone they call basicConfig themselves; get_OCR_results.py only prints,
    so without a handler those lines would stay hidden during a run that can take long."""
    module_logger = logging.getLogger(module.__name__)
    if module_logger.hasHandlers():
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    module_logger.addHandler(handler)
    module_logger.setLevel(logging.INFO)


def _append_to_review(review_path, run_review_rows, ai_records, records, details_key=AI_DETAILS_KEY):
    """Adds the AI reasons (+ AI_FIELDS_TO_REVIEW under details_key) to the review rows written
    by this run (custom_id + id_row). details_key: AI_DETAILS_KEY, or LOCAL_DETAILS_KEY for the
    local rule-based check.

    run_review_rows come from the workflow (not from the file). Their lines in the file are found
    by exact text - the workflow wrote each row as json.dumps(row) on its own line - searching from
    the end: the file is append-only, so this run's lines are the newest and lines from earlier
    runs are never touched, even identical ones.
    Nothing is overwritten - a reason is appended only when it is not in "reasons" yet. A flagged
    row without a review row gets a new row in the validator's shape, built from `records` =
    exactly the results the AI saw."""
    lines = []
    if review_path.exists():
        with open(review_path, encoding="utf-8") as f:
            lines = f.readlines()
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    file_line_count = len(lines)

    # this run's review rows -> their line numbers: line number -> row, (custom_id, id_row) -> line numbers
    wanted = Counter(json.dumps(row, ensure_ascii=False) for row in run_review_rows)
    rows, by_key = {}, defaultdict(list)
    for i in range(file_line_count - 1, -1, -1):
        if not wanted:
            break
        text = lines[i].rstrip("\n")
        if text in wanted:
            wanted[text] -= 1
            if not wanted[text]:
                del wanted[text]
            row = json.loads(text)
            rows[i] = row
            by_key[_row_key(row.get("custom_id"), (row.get("species_entry") or {}).get("id_row"))].append(i)
    if wanted:
        print(f"[WARNING] AI post-eval: {sum(wanted.values())} review row(s) of this run not found in "
              f"{review_path.name} (was the file changed meanwhile?) - review file left unchanged")
        return

    results_rows = _index_results(records)
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    next_line = file_line_count                 # new rows get line numbers after the end of the file
    changed, not_found, ambiguous, without_reasons = set(), [], set(), 0

    for ai_record in ai_records:
        reasons = _as_list(ai_record.get("reasons"))
        if not reasons:
            without_reasons += 1
            continue
        key = _row_key(ai_record.get("custom_id"), ai_record.get("id_row"))
        details = {field: ai_record[field] for field in AI_FIELDS_TO_REVIEW
                   if ai_record.get(field) not in (None, "", [])}

        if key not in by_key:                   # no review row -> new one from this run's results
            for entry in results_rows.get(key, []):
                rows[next_line] = {**entry, "reasons": [], "timestamp": timestamp}
                by_key[key].append(next_line)
                next_line += 1
        if key not in by_key:
            not_found.append(key)
            continue
        if len(by_key[key]) > 1:
            ambiguous.add(key)

        for i in by_key[key]:
            row = rows[i]
            row_reasons = row["reasons"] = _as_list(row.get("reasons"))
            for reason in reasons:
                if reason not in row_reasons:
                    row_reasons.append(reason)
                    changed.add(i)
            if details:
                row_details = row[details_key] = _as_list(row.get(details_key))
                if details not in row_details:
                    row_details.append(details)
                    changed.add(i)

    for i in sorted(changed):
        text = json.dumps(rows[i], ensure_ascii=False) + "\n"
        if i < file_line_count:
            lines[i] = text
        else:
            lines.append(text)

    if changed:
        tmp_path = review_path.with_name(review_path.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.replace(tmp_path, review_path)       # whole file at once, never left half-written

    extended = sum(1 for i in changed if i < file_line_count)
    print(f"[INFO] AI post-eval: {review_path.name} - {extended} review row(s) extended, "
          f"{len(changed) - extended} new row(s) added")
    if ambiguous:
        print(f"[WARNING] AI post-eval: custom_id + id_row matches more than one row of this run "
              f"{list(ambiguous)[:5]} - reasons added to all of them")
    if not_found:
        print(f"[WARNING] AI post-eval: {len(not_found)} flagged row(s) are not in this run's review or results "
              f"(custom_id, id_row): {not_found[:5]}")
    if without_reasons:
        print(f"[WARNING] AI post-eval: {without_reasons} AI record(s) without 'reasons' - nothing to add for them")


def _index_results(records):
    """(custom_id, id_row) -> [{custom_id, batch_id, species_entry}] from the results records."""
    index = defaultdict(list)
    for record in records:
        for entry in record.get("data") or []:
            if isinstance(entry, dict):
                index[_row_key(record.get("custom_id"), entry.get("id_row"))].append({
                    "custom_id": record.get("custom_id"),
                    "batch_id": record.get("batch_id"),
                    "species_entry": entry,
                })
    return index


def _row_key(custom_id, id_row):
    """id_row compared as a number when possible - the AI may return "2" instead of 2."""
    try:
        id_row = int(id_row)
    except (TypeError, ValueError):
        pass
    return str(custom_id), id_row


def _as_list(value):
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def _optional(value):
    """YAML null / empty -> None. The text None / null too (YAML reads `None` as a string)."""
    if value is None or str(value).strip().lower() in ("", "none", "null"):
        return None
    return str(value).strip()


def _stop(message):
    print(f"[ERROR] AI post-eval: {message}")
    print("[ERROR] Script stopped - nothing has been processed.")
    sys.exit(1)
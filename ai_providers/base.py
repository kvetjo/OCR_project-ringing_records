"""
Shared Provider Helpers
=======================
Provider-independent building blocks used by every AI provider adapter.

Nothing in this module talks to a provider API. It only contains the
"process" side of the pipeline: reading configuration, reading prompts,
deriving ids from file paths, splitting requests into batch files,
reading and writing the state files, and logging problems.

A new provider adapter should import from here instead of copying these
functions, so a fix made once applies everywhere.

Functions that DO depend on a provider (building a request line, uploading,
listing batches, reading the response shape) stay in the provider file.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import yaml


# The project root is the folder that contains this package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Default location of the file holding the API keys.
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"

# Placeholders such as ${GEMINI_API_KEY} inside config.yaml.
ENV_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Placeholder for the name of the current run / dataset, for example
# "data_output/output/{run_name}". Its value comes from run_settings.run_name.
#
# Only this EXACT token is replaced - never any other {...} group. The config
# contains regular expressions such as '^[A-Za-z0-9]+\.\d{3}$', and a generic
# brace substitution (str.format) would destroy them.
RUN_NAME_PLACEHOLDER = "{run_name}"

# Default folder / file naming convention used to derive the ids.
# Can be overridden in config.yaml under processing.path_parsing.
DEFAULT_BATCH_PATTERN = r"(batch|davka)[_\-](.+)"
DEFAULT_PAGE_PATTERN = r"page[_\-](.+)"

# Shape of the request id. It is intentionally fixed: the same string ends up
# in the batch files, in the result files, in the review files and in the
# processed items log, so changing it would break the link between them.
CUSTOM_ID_TEMPLATE = "img_{batch_id}_{page_id}"


# ============================================================================
# ENVIRONMENT AND CONFIGURATION
# ============================================================================

def load_env_file(env_path: Any = None) -> None:
    """
    Read KEY=VALUE lines from a .env file into the process environment.

    Lines that are empty or start with '#' are ignored. Surrounding quotes
    are stripped. A variable that is already present in the real environment
    is never overwritten, so a single value can be overridden from the shell
    without editing the file.
    """
    path = Path(env_path) if env_path else DEFAULT_ENV_PATH

    if not path.exists():
        print(f"[WARNING] No .env file found at {path}. Expecting the keys in the environment.")
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            continue

        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()

        # Strip a matching pair of surrounding quotes, if present.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]

        if name and name not in os.environ:
            os.environ[name] = value


def substitute_env_names(text: str) -> str:
    """
    Replace every ${VAR} placeholder in a single string.

    An unknown variable becomes an empty string and produces a warning. That
    way the usual "API key for provider ... not found" check still fires,
    instead of sending a literal '${GEMINI_API_KEY}' to the provider.
    """
    def replace_one(match: "re.Match") -> str:
        name = match.group(1)

        if name in os.environ:
            return os.environ[name]

        print(f"[WARNING] Environment variable '{name}' is not set but is used in the config. Using an empty value.")
        return ""

    return ENV_PLACEHOLDER_PATTERN.sub(replace_one, text)


def resolve_env_placeholders(value: Any) -> Any:
    """Walk a loaded config and replace ${VAR} placeholders in every string."""
    if isinstance(value, dict):
        return {key: resolve_env_placeholders(item) for key, item in value.items()}

    if isinstance(value, list):
        return [resolve_env_placeholders(item) for item in value]

    if isinstance(value, str):
        return substitute_env_names(value)

    return value


def resolve_run_name(value: Any, run_name: str) -> Any:
    """
    Replace every {run_name} placeholder in the config with the actual name.

    A plain string replacement of one exact token is used on purpose, so that
    braces which mean something else (regex quantifiers like \\d{3}) are left
    completely alone.
    """
    if isinstance(value, dict):
        return {key: resolve_run_name(item, run_name) for key, item in value.items()}

    if isinstance(value, list):
        return [resolve_run_name(item, run_name) for item in value]

    if isinstance(value, str):
        return value.replace(RUN_NAME_PLACEHOLDER, run_name)

    return value


def config_uses_run_name(value: Any) -> bool:
    """Return True if the {run_name} placeholder appears anywhere in the config."""
    if isinstance(value, dict):
        return any(config_uses_run_name(item) for item in value.values())

    if isinstance(value, list):
        return any(config_uses_run_name(item) for item in value)

    if isinstance(value, str):
        return RUN_NAME_PLACEHOLDER in value

    return False


def load_config(config_path: str = "config.yaml", env_path: Any = None) -> Dict[str, Any]:
    """
    Load the YAML configuration, fill in the secrets from the .env file and
    expand the {run_name} placeholder.

    Two independent substitutions happen here:

      ${VAR}       -> value from the environment / .env  (secrets)
      {run_name}   -> run_settings.run_name              (name of this run)

    Secrets are never stored in config.yaml. The run name lets you rename a
    whole run in one place instead of in four. Any value can still be written
    out in full - just do not use the placeholder in it.
    """
    load_env_file(env_path)

    with open(config_path, "r", encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)

    config = resolve_env_placeholders(config)

    run_settings = config.get("run_settings", {}) or {}
    run_name = str(run_settings.get("run_name") or "").strip()

    if run_name:
        print(f"[INFO] Run name: {run_name}")
        config = resolve_run_name(config, run_name)

    elif config_uses_run_name(config):
        print(f"ERROR: The config uses the {RUN_NAME_PLACEHOLDER} placeholder, "
              f"but run_settings.run_name is empty.")
        print(f"       Either fill in run_settings.run_name, or replace every "
              f"{RUN_NAME_PLACEHOLDER} with the value you want.")
        sys.exit(1)

    return config


# ============================================================================
# PROMPTS
# ============================================================================

def load_prompts(filepath: str, prompt_key: str) -> Tuple[str, str]:
    """
    Load a YAML file and return the system and user prompts for a given key.
    Includes error handling for missing files and invalid keys.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as prompts_file:
            all_prompts = yaml.safe_load(prompts_file)

        if prompt_key not in all_prompts:
            available_prompts = list(all_prompts.keys())
            print(f"ERROR: Prompt key '{prompt_key}' was not found in the file.")
            print(f"Available prompts are: {available_prompts}")
            sys.exit(1)

        system_prompt = all_prompts[prompt_key].get("system_prompt", "")
        user_prompt = all_prompts[prompt_key].get("user_prompt", "")

        if not system_prompt or not user_prompt:
            print(f"WARNING: 'system_prompt' or 'user_prompt' is missing/empty for key '{prompt_key}'.")

        return system_prompt, user_prompt

    except FileNotFoundError:
        print(f"ERROR: File '{filepath}' does not exist. Please check the file path.")
        sys.exit(1)


# ============================================================================
# PATHS AND REQUEST IDS
# ============================================================================

def get_path_parsing_patterns(config: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """
    Return the (batch_pattern, page_pattern) used to read the ids from a path.

    The patterns live in config.yaml under processing.path_parsing so the
    folder naming convention can be changed without touching the code.
    """
    if not config:
        return DEFAULT_BATCH_PATTERN, DEFAULT_PAGE_PATTERN

    path_parsing = config.get("processing", {}).get("path_parsing", {}) or {}

    batch_pattern = path_parsing.get("batch_pattern") or DEFAULT_BATCH_PATTERN
    page_pattern = path_parsing.get("page_pattern") or DEFAULT_PAGE_PATTERN

    return batch_pattern, page_pattern


def _id_from_match(match: "re.Match") -> str:
    """
    Return the id captured by a pattern.

    The patterns are written so that the LAST capturing group holds the id:
      '(batch|davka)[_\\-](.+)'  -> group 2
      'page[_\\-](.+)'           -> group 1
    A pattern without any group falls back to the whole match.
    """
    if match.lastindex:
        return match.group(match.lastindex)

    return match.group(0)


def extract_batch_page_ids(
    path: Path,
    config: Optional[Dict[str, Any]] = None
) -> Tuple[str, str]:
    """
    Extract batch ID and page ID from a file path.

    The naming convention comes from the config; the logic itself is
    unchanged: every part of the path is tested, and the last part that
    matches wins (so a page name overrides a folder name of the same kind).
    """
    batch_pattern, page_pattern = get_path_parsing_patterns(config)

    batch_id = None
    page_id = None

    for part in path.parts:
        lower = part.lower()

        batch_match = re.search(batch_pattern, lower)
        if batch_match:
            batch_id = _id_from_match(batch_match)

        page_match = re.search(page_pattern, lower)
        if page_match:
            page_id = _id_from_match(page_match)

    if page_id:
        page_id = Path(page_id).stem

    if batch_id is None or page_id is None:
        raise ValueError(f"Cannot extract batch/page id from path: {path}")

    return batch_id, page_id


def build_custom_id(batch_id: str, page_id: str) -> str:
    """Build the request id from the batch and page ids."""
    return CUSTOM_ID_TEMPLATE.format(batch_id=batch_id, page_id=page_id)


def is_in_allowed_batch(path: Path, allowed_batches: List[str]) -> bool:
    """Check if any part of the path is one of the allowed batch folders."""
    for part in path.parts:
        if part in allowed_batches:
            return True

    return False


# ============================================================================
# BATCH FILES
# ============================================================================

def find_jsonl_files_in_output(output_dir: Path) -> List[Path]:
    """Find all JSONL files directly inside the output directory."""
    if not output_dir.exists():
        return []

    return sorted(p for p in output_dir.iterdir() if p.suffix == ".jsonl")


def find_batch_file_in_directory(batch_output_dir: Path, filename: str) -> Optional[Path]:
    """
    Find a batch file in the batches_out directory or its subdirectories.

    Returns the path to the found file, or None if it is not there.
    """
    root_path = batch_output_dir / filename
    if root_path.exists():
        return root_path

    for item in batch_output_dir.rglob(filename):
        if item.is_file():
            return item

    return None


def split_lines_into_batch_files(
    labelled_lines: Iterable[Tuple[str, str]],
    output_dir: Path,
    prefix: str,
    limits: Dict[str, Any]
) -> List[Path]:
    """
    Write already built JSONL request lines into one or more batch files.

    Args:
        labelled_lines: iterable of (source_label, jsonl_line) pairs. The
            label is only used for error messages, so a line that is too big
            can be reported together with the image it came from. An iterable
            (typically a generator) is used on purpose: images are uploaded
            lazily while the lines are consumed, exactly as before.
        output_dir: where the batch files are written
        prefix: filename prefix, files are named <prefix>_0001.jsonl
        limits: batch_generation.limits section of the config

    Returns:
        List of created batch file paths.
    """
    max_lines = limits.get("max_lines", 50000)
    max_jsonl_bytes = limits.get("max_jsonl_bytes", 209715200)
    safety_factor = limits.get("safety_factor", 0.90)
    effective_limit = int(max_jsonl_bytes * safety_factor)

    output_dir.mkdir(parents=True, exist_ok=True)

    batch_files: List[Path] = []
    current_lines: List[str] = []
    current_size = 0

    for source_label, line in labelled_lines:
        line_len = len(line.encode("utf-8"))

        if line_len > effective_limit:
            print(f"[ERROR] Item too large (single line exceeds limit): {source_label}")
            continue

        exceeds_size_limit = current_size + line_len > effective_limit
        exceeds_line_limit = len(current_lines) >= max_lines

        if exceeds_size_limit or exceeds_line_limit:
            out = output_dir / f"{prefix}_{len(batch_files) + 1:04d}.jsonl"
            out.write_text("".join(current_lines), encoding="utf-8")
            batch_files.append(out)

            current_lines = []
            current_size = 0

        current_lines.append(line)
        current_size += line_len

    if current_lines:
        out = output_dir / f"{prefix}_{len(batch_files) + 1:04d}.jsonl"
        out.write_text("".join(current_lines), encoding="utf-8")
        batch_files.append(out)

    return batch_files


def read_custom_ids_from_jsonl(
    path: Path,
    get_request_custom_id_fn: Callable[[Dict[str, Any]], Optional[str]]
) -> List[str]:
    """
    Read the request ids out of a generated batch JSONL file.

    The field holding the id differs per provider ('id' for Gemini,
    'custom_id' for OpenAI), so the reader gets it from the provider.
    """
    custom_ids: List[str] = []

    if not path.exists():
        return custom_ids

    with open(path, "r", encoding="utf-8") as jsonl_file:
        for line in jsonl_file:
            line = line.strip()

            if not line:
                continue

            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue

            custom_id = get_request_custom_id_fn(request)

            if custom_id:
                custom_ids.append(custom_id)

    return custom_ids


def filter_jsonl_by_allowed_images(
    input_file: Path,
    allowed_images: List[str],
    get_request_custom_id_fn: Callable[[Dict[str, Any]], Optional[str]]
) -> Path:
    """
    Write a copy of a batch file that keeps only the listed requests.

    Returns the path of the new file, which gets an '-err' suffix.
    """
    output_file = input_file.parent / f"{input_file.stem}-err{input_file.suffix}"

    allowed_set = set(allowed_images)
    filtered_lines: List[str] = []

    with open(input_file, "r", encoding="utf-8") as jsonl_file:
        for line in jsonl_file:
            line = line.strip()

            if not line:
                continue

            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue

            custom_id = get_request_custom_id_fn(request)

            if custom_id and custom_id in allowed_set:
                filtered_lines.append(line)

    with open(output_file, "w", encoding="utf-8") as out_file:
        for line in filtered_lines:
            out_file.write(line + "\n")

    print(f"[INFO] Filtered {len(filtered_lines)} lines from {input_file.name} to {output_file.name}")
    return output_file


# ============================================================================
# STATE FILE: processed_batches.json
# ============================================================================

def save_processed_batches(state: Dict[str, Any], processed_batches_file: str) -> None:
    """Save processed batches to the state file."""
    with open(processed_batches_file, "w", encoding="utf-8") as state_file:
        json.dump(state, state_file, indent=2, ensure_ascii=False)


def load_processed_batches(processed_batches_file: str) -> Dict[str, Any]:
    """
    Load processed batches from the state file.

    A missing file, an empty file and a corrupted file all return an empty
    dictionary, so a half written state file never stops the whole run.
    """
    path = Path(processed_batches_file)

    if not path.exists():
        return {}

    with open(path, "r", encoding="utf-8") as state_file:
        try:
            return json.load(state_file)
        except json.JSONDecodeError:
            return {}


# ============================================================================
# INFORMATIVE LOG: processed_items.txt
# ============================================================================
# This log records which images were actually turned into requests, in which
# batch, and whether their results were later downloaded and processed.
# It is written as one JSON object per line.
#
# IMPORTANT: nothing in the pipeline reads this log to decide what to do.
# It exists so a human can look up what happened to a given scan.
# ============================================================================

def append_processed_items(
    log_path: Path,
    custom_ids: List[str],
    item_info: Dict[str, Any]
) -> None:
    """
    Append one line per request to the processed items log.

    Args:
        log_path: path to the log file
        custom_ids: request ids taken from the batch file that was sent
        item_info: context shared by all of them (batch file, batch job id,
            provider, model, validator, upload method, timestamps)
    """
    if not custom_ids:
        return

    with Path(log_path).open("a", encoding="utf-8") as log_file:
        for custom_id in custom_ids:
            record: Dict[str, Any] = {"custom_id": custom_id}
            record.update(item_info)

            log_file.write(json.dumps(record, ensure_ascii=False) + "\n")


def mark_processed_items_downloaded(
    log_path: Path,
    batch_job_id: str,
    custom_ids: List[str],
    validator: str,
    downloaded_at: str
) -> int:
    """
    Mark the matching lines in the processed items log as downloaded.

    Only lines that belong to the given batch job AND whose custom_id is in
    custom_ids are touched. Lines written by an older version of the pipeline
    (plain text instead of JSON) are copied over untouched, so the existing
    history is never lost.

    Returns the number of updated lines.
    """
    path = Path(log_path)

    if not path.exists():
        return 0

    custom_id_set = set(custom_ids)
    updated_count = 0
    output_lines: List[str] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()

        if not stripped:
            continue

        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            # Older plain text line - keep it exactly as it is.
            output_lines.append(line)
            continue

        if not isinstance(record, dict):
            output_lines.append(line)
            continue

        belongs_to_batch = record.get("batch_job_id") == batch_job_id
        is_wanted_item = record.get("custom_id") in custom_id_set

        if belongs_to_batch and is_wanted_item:
            record["results_downloaded"] = True
            record["results_downloaded_at"] = downloaded_at
            record["validator"] = validator
            updated_count += 1

        output_lines.append(json.dumps(record, ensure_ascii=False))

    # Write through a temporary file so an interrupted run cannot leave a
    # half written log behind.
    new_content = "\n".join(output_lines) + "\n" if output_lines else ""
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(new_content, encoding="utf-8")
    os.replace(temp_path, path)

    return updated_count


# ============================================================================
# PROBLEM LOG AND ERROR RECORDS
# ============================================================================

def log_problem(
    batch_id: str,
    status: str,
    reason: str,
    problems_file: str,
    error_count: int = 0,
    error_custom_ids: Optional[List[str]] = None
) -> None:
    """Log a problem with batch processing into the problems file."""
    record: Dict[str, Any] = {
        "batch_id": batch_id,
        "status": status,
        "reason": reason,
        "error_count": error_count,
        "timestamp": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    }

    if error_custom_ids:
        record["error_custom_ids"] = error_custom_ids

    with open(problems_file, "a", encoding="utf-8") as problems:
        problems.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_error_records(
    all_records: List[Dict[str, Any]],
    store_error_custom_ids: bool,
    is_error_record_fn: Callable[[Dict[str, Any]], bool]
) -> Tuple[int, List[str]]:
    """
    Count error records and collect their custom_ids.

    Detecting an error is provider specific, so the check is passed in.
    """
    error_count = 0
    custom_ids: List[str] = []

    for record in all_records:
        if is_error_record_fn(record):
            error_count += 1

            if store_error_custom_ids:
                custom_id = record.get("custom_id") or record.get("id")

                if custom_id:
                    custom_ids.append(custom_id)

    return error_count, custom_ids


# ============================================================================
# RAW BATCH CONTENT
# ============================================================================

def build_raw_file_path(raw_dir: Path, batch_id: str, name_suffix: str) -> Path:
    """
    Build the path of the raw results file of one batch.

    The name is a contract between whoever WRITES the file (a provider that
    downloads a finished batch, or a local runtime such as Ollama that produces
    the results itself) and get_OCR_results.py, which finds the file again by
    globbing '*{name_suffix}_raw.jsonl' and derives the batch id back from the
    name. That is why it lives here and not in a single provider.
    """
    safe_id = batch_id.replace("batches/", "").replace("/", "_")

    return raw_dir / f"{safe_id}{name_suffix}_raw.jsonl"


def parse_raw_content(raw_text: str) -> List[Dict[str, Any]]:
    """
    Parse raw JSONL text (freshly downloaded or read from disk) into records.

    Both providers return one JSON object per line, so the parsing itself is
    shared. What those objects contain is read by the provider specific
    extract_text_from_record / is_error_record functions.
    """
    records: List[Dict[str, Any]] = []

    for line in raw_text.splitlines():
        line = line.strip()

        if not line:
            continue

        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            print("[WARNING] There is a PROBLEM with a JSON which was not possible to decode:")
            print(str(line))
            continue

    return records


def normalize_batch_name(name: str) -> str:
    """Normalize a batch name for consistent comparison."""
    return name

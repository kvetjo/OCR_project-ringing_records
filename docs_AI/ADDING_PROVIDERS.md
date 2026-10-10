# Adding a new AI provider (example: Claude)

This page shows how to connect a new **cloud AI service** that has a *batch API*. A batch API lets you send many requests at once and collect the answers later. The worked example is **Claude** (Anthropic).

> For a new **local** model through Ollama you don't need any of this. You only write a small model module; see [OLLAMA_LOCAL_MODELS.md → Adding a new local model](OLLAMA_LOCAL_MODELS.md#adding-a-new-local-model).

**On this page**

1. [How providers are plugged in](#how-providers-are-plugged-in)
2. [The functions a provider must have](#the-functions-a-provider-must-have)
3. [How the scripts use these functions](#how-the-scripts-use-these-functions)
4. [Claude: what to know before you start](#claude-what-to-know-before-you-start)
5. [Step by step](#step-by-step)
6. [The example file claude_provider.py](#the-example-file-claude_providerpy)
7. [Registering the provider](#registering-the-provider)
8. [Configuration](#configuration)
9. [Testing](#testing)
10. [Common mistakes](#common-mistakes)
11. [Further reading](#further-reading)

---

## How providers are plugged in

- **One provider = one Python file** in `ai_providers/`, e.g. `gemini_provider.py`.
- `ai_providers/provider_selector.py` contains the function `get_provider(name)`. For the name in `run_settings.active_provider`, it imports that provider's file and returns a **dictionary of functions**. The dictionary has the same keys for every provider, e.g. `provider['list_batches']`.
- The two main scripts only call functions from this dictionary. They never know which provider they talk to, so a new provider needs **no change** in `make_OCR_batch.py` or `get_OCR_results.py`.
- Before the dictionary is returned, it is checked against the list `REQUIRED_PROVIDER_FUNCTIONS` in `ai_providers/provider_interface.py`. A missing function stops the script right away:
  ```
  NotImplementedError: Provider 'claude' does not fulfil the provider interface - missing functions: ['get_request_counts']. See ai_providers/provider_interface.py for the full contract.
  ```
- `provider_interface.py` describes every function, with its arguments and return value. Keep it open next to this page.
- **You don't write everything yourself.** Everything that does not talk to the provider is already in `ai_providers/base.py` and is simply imported: reading the config and the prompts, building image ids, splitting batch files, the state files and the problem log.

---

## The functions a provider must have

The dictionary must contain **34 functions**. The key in the dictionary is fixed; the name of the function inside your file may be different. For example, Gemini's `upload_image_to_gemini` is stored under the key `upload_image_to_provider`.

### Taken unchanged from base.py (10)

| Key | What it does |
|---|---|
| `load_config` | reads `config.yaml` and fills in `${...}` and `{run_name}` |
| `load_prompts` | reads `system_prompt` and `user_prompt` from `prompts.yaml` |
| `extract_batch_page_ids` | path → (batch, page), e.g. `batch_3/page_12.jpg` → (`3`, `12`) |
| `find_jsonl_files_in_output` | lists `.jsonl` files in a folder |
| `is_in_allowed_batch` | is the image inside one of the `allowed_batches` folders? |
| `save_processed_batches` / `load_processed_batches` | write / read `processed_batches.json` |
| `log_problem` | adds a line to the problems file |
| `parse_raw_content` | JSONL text → list of records (Python dictionaries) |
| `normalize_batch_name` | returns the name unchanged; kept for compatibility |

`base.py` also has helpers you will use inside your file: `build_custom_id` (→ `img_3_12`), `split_lines_into_batch_files` (splits requests into files by the size limits) and `parse_error_records` (counts failed requests).

### Written for each provider (24)

| Key | Must return | Notes |
|---|---|---|
| `get_image_adapter(config)` | a dictionary with `collect_image_files`, `get_image_base64_and_mime`, `get_image_file_for_upload` | copy it unchanged from `gemini_provider.py` |
| `make_jsonl_line(custom_id, mime_type, image_data, upload_method, model_name, system_prompt, user_prompt)` | one request as JSON text, ending with `"\n"` | the provider's request format |
| `get_request_custom_id(request)` | the image id stored in one line of *your* batch file | e.g. `request["custom_id"]` |
| `build_jsonl_batches(image_paths, output_dir, prefix, config, api_key, model_name)` | the list of batch files created | builds one line per image, then calls `split_lines_into_batch_files` |
| `upload_image_to_provider(...)` | (reference, mime type) | only needed for `image_upload_method: "file_api"` |
| `upload_file_to_provider(path, api_key)` | (uploaded thing, display name) | uploads a batch file |
| `create_batch_job(uploaded, display_name, model_name, api_key)` | the batch job object | |
| `list_batches(api_key)` | a list of batch job objects | |
| `get_batch(batch_id, api_key)` | one batch job object | not called by the main scripts |
| `download_file(...)` | – | a helper; not called by the main scripts |
| `download_raw_batch(batch_job, api_key)` | all results as JSONL text | used by `full` / `download_only` |
| `parse_batch_results(batch_job, api_key)` | a list of result records | usually `parse_raw_content(download_raw_batch(...))` |
| `extract_text_from_record(record)` | the answer text of one result, or `None` | |
| `is_error_record(record)` | `True` if that request failed | |
| `parse_error_records(all_records, store_error_custom_ids)` | (number of failed requests, their ids) | a thin wrapper around the `base.py` version |
| `get_batch_status(batch_job)` | `"completed"`, `"in_progress"` or `"failed"` | any other word is logged as a problem |
| `get_batch_id(batch_job)` | the batch id as text | |
| `batch_created_after(batch_job, threshold)` | `True` if the job was created at or after `threshold` (a UTC date and time) | used by `process_from` |
| `get_request_counts(batch_job, api_key)` | (completed, failed) | for `processed_batches.json` |
| `get_batch_tokens(batch_job, api_key)` | `{"input_tokens", "output_tokens", "total_tokens"}` or `None` | |
| `get_processing_time(batch_job)` | seconds, or `None` | |
| `get_model_name(batch_job)` | the model name, or `None` (the config value is used then) | |
| `get_batch_created_time(batch_job)`, `get_batch_completed_time(batch_job)` | `"DD-MM-YYYY : HH-MM-SS"` in the Europe/Malta time zone, or `None` | |

A function that your provider cannot support must still exist. It may simply raise an error that explains why; `base_ollama.py` does this for the whole batch API.

---

## How the scripts use these functions

`make_OCR_batch.py`:

```
get_image_adapter(config)                     -> collect_image_files(...)       find the scans
extract_batch_page_ids + is_in_allowed_batch                                    filter them
build_jsonl_batches(...)                                                        write the batch files
      (calls make_jsonl_line for every image, and uploads images first with file_api)
for every batch file:
      upload_file_to_provider(file, key)      -> (uploaded, name)
      create_batch_job(uploaded, name, model, key) -> job
      get_batch_id(job)                       -> printed, and written to processed_items.txt
      get_request_custom_id(line)             -> the image ids for processed_items.txt
```

`get_OCR_results.py`:

```
list_batches(key)
for every job:
      get_batch_id(job), get_batch_status(job), batch_created_after(job, date)
      standard      : parse_batch_results(job, key)
      full/download : download_raw_batch(job, key) -> saved to data_output/raw -> parse_raw_content(text)
      process_only  : parse_raw_content(text of the saved raw file)           (no provider calls)
      parse_error_records / is_error_record   -> failed requests
      extract_text_from_record(record)        -> the text handed to the validator
      get_request_counts, get_batch_tokens, get_processing_time, get_model_name,
      get_batch_created_time, get_batch_completed_time -> processed_batches.json
```

---

## Claude: what to know before you start

Before writing code, learn how the provider's batch API works. For Claude, this is the **Message Batches API**: <https://platform.claude.com/docs/en/build-with-claude/batch-processing>.

| Fact | What it means for the provider file |
|---|---|
| A batch is created by sending the list of requests **directly**; there is no file upload. | `upload_file_to_provider` only reads the local batch file into a list, and `create_batch_job` sends it. |
| Each request is `{"custom_id": ..., "params": {...}}`, where `params` holds the normal Messages API parameters. | That is what `make_jsonl_line` writes. |
| `custom_id` must be 1–64 characters: letters, digits, `-` and `_`. | Our ids such as `img_3_12` fit. |
| `max_tokens` (the maximum length of the answer) is **required**. | Choose enough for the whole JSON answer, e.g. 8000 for a 23-row ringing sheet. |
| A batch may have up to 100,000 requests or 256 MB. | Set `batch_generation.limits` accordingly. |
| The batch status is `in_progress`, `canceling` or `ended`. | Translate `ended` → `"completed"`. |
| Each result has a type: `succeeded`, `errored`, `canceled` or `expired`. | Everything except `succeeded` is an error record. |
| Results can come back in **any order**. | Always match them by `custom_id`, which the pipeline already does. |
| Most batches finish within 1 hour. A batch expires after 24 hours. Results can be downloaded for 29 days. | Download within 29 days. |
| Images can be sent as base64, as a URL, or as a Files API `file_id`. JPEG, PNG, GIF and WebP are supported, up to 10 MB per image. PDF files go in a `document` block. | Map our `image_upload_method` values to these. |
| Claude works best when the image comes **before** the text. | Put the image block first in the message. |

---

## Step by step

1. **Install the Claude package:**
   ```bash
   pip install anthropic
   ```
   Also add `anthropic` to `requirements.txt` and `pyproject.toml`, so others get it too.
2. **Get an API key** in the Claude Console (<https://platform.claude.com>, *API keys*). Add it to `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```
   Also add an empty line `ANTHROPIC_API_KEY=` to `.env.example`.
3. **Create `ai_providers/claude_provider.py`.** The complete example is below.
4. **Register it** in `ai_providers/provider_selector.py` ([Registering the provider](#registering-the-provider)).
5. **Configure `config.yaml`** ([Configuration](#configuration)).
6. **Test with 2 images** ([Testing](#testing)).

---

## The example file claude_provider.py

> ⚠ **Example code.** It is written from the Claude API documentation and has **not been tested with this project**. Check the current Anthropic documentation, and test with 2 images before you use it on real data. For simplicity, it supports `image_upload_method` `base64` and `pass_a_url`, but not `file_api`.

```python
"""
Claude Provider for OCR Batch Processing (EXAMPLE - not tested).
Uses Anthropic's Message Batches API.

Everything that is not specific to Claude comes from ai_providers/base.py
and is only re-exported here, so provider_selector.py finds it in this file.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

import anthropic

# Shared, provider-independent helpers.
from ai_providers.base import (
    load_config,
    load_prompts,
    extract_batch_page_ids,
    build_custom_id,
    is_in_allowed_batch,
    find_jsonl_files_in_output,
    split_lines_into_batch_files,
    save_processed_batches,
    load_processed_batches,
    log_problem,
    parse_raw_content,
    normalize_batch_name,
)
from ai_providers.base import parse_error_records as base_parse_error_records

MAX_TOKENS = 8000                     # Claude requires a maximum answer length
LOCAL_TIMEZONE = ZoneInfo("Europe/Malta")


def _client(api_key: str) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key)


# ============================================================================
# IMAGES
# ============================================================================

def get_image_adapter(config: Dict[str, Any]):
    """Same as in gemini_provider.py: scans in a local folder or in Cloudflare R2."""
    active_adapter = config.get('image_management', {}).get('active_adapter', 'local')

    if active_adapter == 'cloudflare':
        from image_management.cloudflare_adapter import (
            collect_image_files, get_image_base64_and_mime, get_image_file_for_upload
        )
    elif active_adapter == 'local':
        from image_management.local_adapter import (
            collect_image_files, get_image_base64_and_mime, get_image_file_for_upload
        )
    else:
        raise ValueError(f"Unknown image adapter: {active_adapter}")

    return {
        'collect_image_files': collect_image_files,
        'get_image_base64_and_mime': get_image_base64_and_mime,
        'get_image_file_for_upload': get_image_file_for_upload,
    }


def upload_image_to_provider(*args, **kwargs):
    """image_upload_method 'file_api' is not supported in this example."""
    raise NotImplementedError("claude_provider: use image_upload_method 'base64' or 'pass_a_url'")


# ============================================================================
# BUILDING THE BATCH FILE
# ============================================================================

def make_jsonl_line(custom_id, mime_type, image_data, upload_method, model_name,
                    system_prompt="", user_prompt=""):
    """One Claude batch request as one line of JSON text."""
    if upload_method == "base64":
        block_type = "document" if mime_type == "application/pdf" else "image"
        image_block = {"type": block_type,
                       "source": {"type": "base64", "media_type": mime_type, "data": image_data}}
    elif upload_method == "pass_a_url":
        image_block = {"type": "image", "source": {"type": "url", "url": image_data}}
    else:
        raise ValueError(f"Unsupported image_upload_method for Claude: {upload_method}")

    request = {
        "custom_id": custom_id,
        "params": {
            "model": model_name,
            "max_tokens": MAX_TOKENS,
            "system": system_prompt,
            "messages": [
                {"role": "user",
                 "content": [image_block, {"type": "text", "text": user_prompt}]}
            ],
        },
    }
    return json.dumps(request, ensure_ascii=False) + "\n"


def get_request_custom_id(request: Dict[str, Any]) -> Optional[str]:
    """Claude keeps the image id in 'custom_id'."""
    return request.get("custom_id")


def _generate_jsonl_lines(image_paths, config, model_name, system_prompt, user_prompt):
    """Yield (image path, request line) for every image - same idea as in gemini_provider.py."""
    upload_method = config.get('batch_generation', {}).get('image_upload_method', 'base64')
    adapter = get_image_adapter(config)

    for img_path in image_paths:
        try:
            batch_id, page_id = extract_batch_page_ids(img_path, config)
            custom_id = build_custom_id(batch_id, page_id)

            if upload_method == "base64":
                image_data, mime_type = adapter['get_image_base64_and_mime'](img_path, config)
            elif upload_method == "pass_a_url":
                base_url = config['image_management']['cloudflare']['public_base_url'].rstrip('/')
                image_data = f"{base_url}/{quote(Path(img_path).as_posix())}"
                mime_type = "image/jpeg"
            else:
                raise ValueError(f"Unsupported image_upload_method for Claude: {upload_method}")
        except Exception as e:
            print(f"[WARN] Skipping {img_path}: {e}")
            continue

        yield str(img_path), make_jsonl_line(custom_id, mime_type, image_data, upload_method,
                                             model_name, system_prompt, user_prompt)


def build_jsonl_batches(image_paths, output_dir, prefix, config, api_key, model_name):
    """Write the batch file(s). Splitting by size and line count is shared (base.py)."""
    run_settings = config.get('run_settings', {})
    system_prompt, user_prompt = load_prompts(
        run_settings.get('prompts_file_path', 'prompts.yaml'),
        run_settings.get('active_prompt_key', 'PROMPT_2'),
    )
    lines = _generate_jsonl_lines(image_paths, config, model_name, system_prompt, user_prompt)
    limits = config.get('batch_generation', {}).get('limits', {})
    return split_lines_into_batch_files(lines, output_dir, prefix, limits)


# ============================================================================
# SENDING THE BATCH
# ============================================================================

def upload_file_to_provider(path: Path, api_key: str) -> Tuple[Any, str]:
    """
    Claude has no batch FILE upload - the requests are sent directly when the
    batch is created. So this only reads the local batch file into a list.
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    requests_list = [json.loads(line) for line in lines if line.strip()]
    print(f"[INFO] Read {len(requests_list)} requests from {Path(path).name}")
    return requests_list, Path(path).stem


def create_batch_job(uploaded_file, display_name: str, model_name: str, api_key: str):
    """Create the batch. 'uploaded_file' is the list returned by upload_file_to_provider."""
    print(f"[INFO] Creating Claude batch for {display_name} ({len(uploaded_file)} requests)...")
    return _client(api_key).messages.batches.create(requests=uploaded_file)


# ============================================================================
# BATCH JOBS
# ============================================================================

def list_batches(api_key: str):
    """All batches of the account (the SDK loads further pages automatically)."""
    return list(_client(api_key).messages.batches.list(limit=100))


def get_batch(batch_id: str, api_key: str):
    return _client(api_key).messages.batches.retrieve(batch_id)


def get_batch_id(batch_job) -> str:
    return batch_job.id


def get_batch_status(batch_job) -> str:
    """Translate Claude's status into the words the pipeline understands."""
    mapping = {"ended": "completed", "in_progress": "in_progress", "canceling": "in_progress"}
    return mapping.get(batch_job.processing_status, "unknown")


def batch_created_after(batch_job, threshold: datetime) -> bool:
    return batch_job.created_at >= threshold


# ============================================================================
# RESULTS
# ============================================================================

def download_raw_batch(batch_job, api_key: str) -> str:
    """All results of a finished batch as JSONL text, one result per line."""
    results = _client(api_key).messages.batches.results(batch_job.id)
    lines = [json.dumps(result.to_dict(), ensure_ascii=False) for result in results]
    return "\n".join(lines) + "\n" if lines else ""


def parse_batch_results(batch_job, api_key: str) -> List[Dict[str, Any]]:
    return parse_raw_content(download_raw_batch(batch_job, api_key))


def download_file(*args, **kwargs):
    """Not needed for Claude - the results come from download_raw_batch()."""
    raise NotImplementedError("claude_provider: use download_raw_batch()")


def is_error_record(record: Dict[str, Any]) -> bool:
    """Only 'succeeded' results carry an answer; errored/canceled/expired are errors."""
    return (record.get("result") or {}).get("type") != "succeeded"


def parse_error_records(all_records, store_error_custom_ids):
    return base_parse_error_records(all_records, store_error_custom_ids, is_error_record)


def extract_text_from_record(record: Dict[str, Any]) -> Optional[str]:
    """Join all text blocks of Claude's answer."""
    if is_error_record(record):
        return None
    content = (record["result"].get("message") or {}).get("content") or []
    texts = [block.get("text", "") for block in content if block.get("type") == "text"]
    return "\n".join(texts).strip() or None


# ============================================================================
# INFORMATION FOR processed_batches.json
# ============================================================================

def get_request_counts(batch_job, api_key: str) -> Tuple[int, int]:
    counts = batch_job.request_counts
    return counts.succeeded, counts.errored + counts.canceled + counts.expired


def get_batch_tokens(batch_job, api_key: str) -> Optional[Dict[str, int]]:
    """Claude reports the tokens per result, so they are added up here.
    This downloads the results a second time - return None if you don't need tokens."""
    input_tokens = output_tokens = 0
    for record in parse_batch_results(batch_job, api_key):
        usage = ((record.get("result") or {}).get("message") or {}).get("usage") or {}
        input_tokens += usage.get("input_tokens", 0)
        output_tokens += usage.get("output_tokens", 0)
    return {"input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens}


def get_processing_time(batch_job) -> Optional[float]:
    if batch_job.created_at and batch_job.ended_at:
        return (batch_job.ended_at - batch_job.created_at).total_seconds()
    return None


def get_model_name(batch_job) -> Optional[str]:
    """A Claude batch has no model of its own -> None, so the model from config.yaml is used."""
    return None


def _local_time(value) -> Optional[str]:
    return value.astimezone(LOCAL_TIMEZONE).strftime("%d-%m-%Y : %H-%M-%S") if value else None


def get_batch_created_time(batch_job) -> Optional[str]:
    return _local_time(batch_job.created_at)


def get_batch_completed_time(batch_job) -> Optional[str]:
    return _local_time(batch_job.ended_at)
```

How the pieces map to the Claude API:

- `make_jsonl_line` writes the request in the shape `client.messages.batches.create()` expects.
- `download_raw_batch` saves every result exactly as Claude returns it, e.g.
  `{"custom_id": "img_3_12", "result": {"type": "succeeded", "message": {"content": [{"type": "text", "text": "[...]"}], "usage": {...}}}}`.
- `extract_text_from_record` and `is_error_record` read exactly that shape.

---

## Registering the provider

Add a new branch to `get_provider()` in `ai_providers/provider_selector.py`, next to the `gemini` and `openai` branches. Each **key** must be exactly the name from `REQUIRED_PROVIDER_FUNCTIONS`:

```python
    elif provider_name == "claude":
        from ai_providers.claude_provider import (
            load_config, get_image_adapter, load_prompts, make_jsonl_line,
            get_request_custom_id, extract_batch_page_ids, build_jsonl_batches,
            upload_image_to_provider, upload_file_to_provider, create_batch_job,
            find_jsonl_files_in_output, is_in_allowed_batch, save_processed_batches,
            load_processed_batches, list_batches, get_batch, download_file,
            parse_batch_results, extract_text_from_record, get_batch_status,
            get_batch_id, log_problem, is_error_record, get_batch_tokens,
            get_processing_time, get_model_name, get_batch_created_time,
            get_batch_completed_time, get_request_counts, parse_error_records,
            normalize_batch_name, batch_created_after, download_raw_batch,
            parse_raw_content,
        )

        functions = {
            'load_config': load_config,
            'get_image_adapter': get_image_adapter,
            'load_prompts': load_prompts,
            'make_jsonl_line': make_jsonl_line,
            'get_request_custom_id': get_request_custom_id,
            'extract_batch_page_ids': extract_batch_page_ids,
            'build_jsonl_batches': build_jsonl_batches,
            'upload_image_to_provider': upload_image_to_provider,
            'upload_file_to_provider': upload_file_to_provider,
            'create_batch_job': create_batch_job,
            'find_jsonl_files_in_output': find_jsonl_files_in_output,
            'is_in_allowed_batch': is_in_allowed_batch,
            'save_processed_batches': save_processed_batches,
            'load_processed_batches': load_processed_batches,
            'list_batches': list_batches,
            'get_batch': get_batch,
            'download_file': download_file,
            'parse_batch_results': parse_batch_results,
            'extract_text_from_record': extract_text_from_record,
            'get_batch_status': get_batch_status,
            'get_batch_id': get_batch_id,
            'log_problem': log_problem,
            'is_error_record': is_error_record,
            'get_batch_tokens': get_batch_tokens,
            'get_processing_time': get_processing_time,
            'get_model_name': get_model_name,
            'get_batch_created_time': get_batch_created_time,
            'get_batch_completed_time': get_batch_completed_time,
            'get_request_counts': get_request_counts,
            'parse_error_records': parse_error_records,
            'normalize_batch_name': normalize_batch_name,
            'batch_created_after': batch_created_after,
            'download_raw_batch': download_raw_batch,
            'parse_raw_content': parse_raw_content,
        }

        check_provider_functions(provider_name, functions)
        return functions
```

Optionally, add `claude` to the list of available providers in the error message at the end of `get_provider()`.

---

## Configuration

The **provider name** (`claude`) must be the same in three places:

1. the `elif provider_name == "claude":` branch in `provider_selector.py`,
2. `run_settings.active_provider` in `config.yaml`,
3. the key under `api_keys`, because the scripts read `api_keys[<active_provider>]`.

```yaml
run_settings:
  active_provider: "claude"
  model_name: "claude-opus-5-5"          # models: https://platform.claude.com/docs/en/models/overview
  active_prompt_key: "PROMPT_4"

api_keys:
  openai: "${OPENAI_API_KEY}"
  gemini: "${GEMINI_API_KEY}"
  claude: "${ANTHROPIC_API_KEY}"         # new line

batch_generation:
  image_upload_method: "base64"          # or "pass_a_url" with Cloudflare; file_api is not in the example
  limits:
    max_lines: 100000                    # Claude: up to 100,000 requests per batch
    max_jsonl_bytes: 268435456           # 256 MB
    safety_factor: 0.90
```

---

## Testing

1. Set `processing.mode: "explicit"` with **2 images**.
2. Set `batch_operation_mode: "generate"` and run `python make_OCR_batch.py`. Open the created batch file and check that a line looks like the request shape above.
3. Switch to `batch_operation_mode: "upload"`, put the file name into `upload_files`, and run again. Note the batch id (`msgbatch_...`).
4. Check the batch in the Claude Console. There is no `list_batches_G.py` equivalent for Claude.
5. In `get_OCR_results.py`: `main(batch_ids=["msgbatch_..."], mode="full")`, then run it.
6. Check:
   - the raw file in `data_output/raw/`,
   - the results and review files,
   - the problems file,
   - the new entry in `processed_batches.json`.

---

## Common mistakes

| Symptom | Cause |
|---|---|
| `ValueError: Unknown provider: claude` | The branch in `provider_selector.py` is missing, or the name is spelled differently. |
| `NotImplementedError: Provider 'claude' does not fulfil the provider interface - missing functions: [...]` | A key is missing in the dictionary in `provider_selector.py`. |
| `ERROR: API key for provider 'claude' not found` | There is no `claude:` line under `api_keys`, or the variable in `.env` has another name. |
| Every batch is written to the problems file as `NOT_COMPLETED_STATUS_UNKNOWN` | `get_batch_status` does not translate the provider's status words. |
| The validator gets nothing, or `No output_text found` | `extract_text_from_record` does not match the real shape of the result. Look at one line of the raw file (`mode="full"`). |
| `ModuleNotFoundError: No module named 'anthropic'` | Run `pip install anthropic`. |

---

## Further reading

- Claude Message Batches API: <https://platform.claude.com/docs/en/build-with-claude/batch-processing>
- Images (vision): <https://platform.claude.com/docs/en/build-with-claude/vision>
- Python package `anthropic`: <https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python>
- Models: <https://platform.claude.com/docs/en/models/overview>
- In this project:
  - `ai_providers/provider_interface.py` (the description of every function),
  - `ai_providers/gemini_provider.py` and `ai_providers/openai_provider.py` (complete, working examples),
  - `ai_providers/base.py` (what already exists).

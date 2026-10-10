"""
Gemini Provider for OCR Batch Processing
Handles batch creation, upload, and result retrieval for Google Gemini API.

Everything that is not specific to the Gemini API (config, prompts, path ids,
batch file splitting, state files, problem log, raw JSONL parsing) lives in
ai_providers/base.py and is only re-exported here, so the provider dictionary
built by provider_selector.py stays complete.
"""
import os
import json
from google import genai
from google.genai import types
from pathlib import Path
from typing import List, Dict, Tuple, Any, Optional
from datetime import datetime, timezone
import requests
from zoneinfo import ZoneInfo

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


def get_image_adapter(config: Dict[str, Any]):
    """Get the appropriate image storage adapter based on configuration."""
    image_config = config.get('image_management', {})
    active_adapter = image_config.get('active_adapter', 'local')

    if active_adapter == 'cloudflare':
        from image_management.cloudflare_adapter import (
            collect_image_files,
            get_image_base64_and_mime,
            get_image_file_for_upload
        )
    elif active_adapter == 'local':
        from image_management.local_adapter import (
            collect_image_files,
            get_image_base64_and_mime,
            get_image_file_for_upload
        )
    else:
        raise ValueError(f"Unknown image adapter: {active_adapter}")

    return {
        'collect_image_files': collect_image_files,
        'get_image_base64_and_mime': get_image_base64_and_mime,
        'get_image_file_for_upload': get_image_file_for_upload
    }


def make_jsonl_line(
    custom_id: str,
    mime_type: str,
    image_data: str,
    method: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str
) -> str:
    """
    Build a single JSONL line for Gemini Batch API with dynamic prompts.
    - base64:   inlineData  (works for images and PDF with mime_type application/pdf)
    - file_api: fileData    (works for images and PDF - Gemini File API accepts PDF natively)
    """
    if method == "base64":
        image_part = {
            "inlineData": {
                "mimeType": mime_type,
                "data": image_data
            }
        }
    elif method == "file_api":
        image_part = {
            "fileData": {
                "mimeType": mime_type,
                "fileUri": image_data
            }
        }
    else:
        raise ValueError("Invalid IMAGE_UPLOAD_METHOD")

    request_body = {
        "id": custom_id,
        "request": {
            "model": f"models/{model_name}",
            "systemInstruction": {
                "parts": [{"text": system_prompt}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": user_prompt},
                        image_part
                    ]
                }
            ]
        }
    }
    return json.dumps(request_body, ensure_ascii=False) + "\n"


def get_request_custom_id(request: Dict[str, Any]) -> Optional[str]:
    """
    Return the request id of one line of a generated Gemini batch file.

    Gemini batch input files carry the id in the 'id' field (OpenAI uses
    'custom_id'), which is why this lives in the provider.
    """
    return request.get("id")


def _generate_jsonl_lines(
    image_paths: List[Path],
    config: Dict[str, Any],
    api_key: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str
):
    """
    Yield (source_label, jsonl_line) pairs for the given images.

    This is a generator on purpose. With image_upload_method 'file_api' each
    image is uploaded to the Gemini File API at the moment its line is built,
    so the upload stays lazy exactly as it was before.
    """
    batch_config = config.get('batch_generation', {})
    upload_method = batch_config.get('image_upload_method', 'base64')

    image_adapter = get_image_adapter(config)
    get_image_base64_and_mime_fn = image_adapter['get_image_base64_and_mime']
    get_image_file_for_upload_fn = image_adapter['get_image_file_for_upload']

    for img_path in image_paths:
        try:
            batch_id, page_id = extract_batch_page_ids(img_path, config)
            custom_id = build_custom_id(batch_id, page_id)
        except Exception as e:
            print(f"[WARN] Cannot build custom_id from path {img_path}: {e}")
            continue

        try:
            if upload_method == "base64":
                image_data, mime_type = get_image_base64_and_mime_fn(img_path, config)
            elif upload_method == "file_api":
                print(f"[INFO] Uploading image to Gemini File API: {img_path}")
                image_data, mime_type = upload_image_to_gemini(
                    img_path, api_key, config, get_image_file_for_upload_fn
                )
            else:
                raise ValueError("Invalid IMAGE_UPLOAD_METHOD. Use 'base64' or 'file_api'.")
        except Exception as e:
            print(f"[WARN] Skipping {img_path}: {e}")
            continue

        line = make_jsonl_line(
            custom_id,
            mime_type,
            image_data,
            upload_method,
            model_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt
        )

        yield str(img_path), line


def build_jsonl_batches(
    image_paths: List[Path],
    output_dir: Path,
    prefix: str,
    config: Dict[str, Any],
    api_key: str,
    model_name: str
) -> List[Path]:
    """Build JSONL batch files from image paths."""
    batch_config = config.get('batch_generation', {})
    limits = batch_config.get('limits', {})

    run_settings = config.get('run_settings', {})
    prompts_file_path = run_settings.get('prompts_file_path', 'prompts.yaml')
    active_prompt_key = run_settings.get('active_prompt_key', 'PROMPT_2')

    system_prompt_text, user_prompt_text = load_prompts(prompts_file_path, active_prompt_key)

    lines = _generate_jsonl_lines(
        image_paths,
        config,
        api_key,
        model_name,
        system_prompt_text,
        user_prompt_text
    )

    # The size / line count splitting is not Gemini specific, so it is shared.
    return split_lines_into_batch_files(lines, output_dir, prefix, limits)


def upload_image_to_gemini(path: Path, api_key: str, config: Dict[str, Any], get_image_file_for_upload_fn) -> Tuple[str, str]:
    """
    Upload file (image or PDF) via Gemini File API and return its URI and MIME type.
    MIME type is determined by extension - ensures correct PDF processing.
    """
    client = genai.Client(api_key=api_key)

    # Use adapter to get file for upload
    file_path, mime = get_image_file_for_upload_fn(path, config)

    if file_path != str(path):
        # Adapter returned a temporary file (Cloudflare case)
        try:
            uploaded_file = client.files.upload(file=file_path, config={'mime_type': mime})
        finally:
            os.remove(file_path)
    else:
        # Local file, use directly
        uploaded_file = client.files.upload(file=file_path, config={'mime_type': mime})

    return uploaded_file.uri, mime


def upload_file_to_gemini(path: Path, api_key: str) -> Tuple[Any, str]:
    """Upload batch JSONL file to Gemini and return uploaded file and display name."""
    client = genai.Client(api_key=api_key)
    display_name = path.stem
    print(f"[INFO] Uploading batch JSONL {path.name} as '{display_name}' to Gemini...")

    uploaded_file = client.files.upload(
        file=str(path),
        config=types.UploadFileConfig(
            display_name=display_name,
            mime_type='application/jsonl'
        )
    )
    print(f"[INFO] Uploaded file URI: {uploaded_file.uri}")
    return uploaded_file, display_name


def create_batch_job(uploaded_file, display_name: str, model_name: str, api_key: str):
    """Create a batch job from uploaded file."""
    client = genai.Client(api_key=api_key)
    job_display_name = f"{display_name}-job"
    print(f"[INFO] Creating batch job '{job_display_name}' for {uploaded_file.name}...")

    job = client.batches.create(
        model=f"models/{model_name}",
        src=uploaded_file.name,
        config=types.CreateBatchJobConfig(
            display_name=job_display_name
        )
    )
    return job


#THIS CAUSED PROBLEMS WHEN A FILE WAS PRESENT BUT WAS EMPTY
# (the guarded version now lives in ai_providers/base.py and is shared by all providers)
# def load_processed_batches(processed_batches_file: str) -> Dict[str, str]:
#     """Load processed batches from state file."""
#     if not Path(processed_batches_file).exists():
#         return {}
#     with open(processed_batches_file, "r", encoding="utf-8") as f:
#         return json.load(f)

# ============================================================
# RESULT RETRIEVAL FUNCTIONS
# ============================================================

def _normalize_batch_name(batch_id: str) -> str:
    """Ensures the batch name has the 'batches/' prefix."""
    return batch_id if batch_id.startswith("batches/") else f"batches/{batch_id}"


def batch_created_after(batch_job, threshold: datetime) -> bool:
    """
    Check if batch job was created after threshold datetime.

    Gemini uses batch_job.create_time (datetime object).
    """
    create_time = getattr(batch_job, "create_time", None)
    if create_time is None:
        return False

    if isinstance(create_time, datetime):
        batch_dt = create_time
        if batch_dt.tzinfo is None:
            batch_dt = batch_dt.replace(tzinfo=timezone.utc)
    else:
        return False

    return batch_dt >= threshold


def list_batches(api_key: str):
    """List all batch jobs from Gemini API."""
    client = genai.Client(api_key=api_key)
    return list(client.batches.list())


def get_batch(batch_id: str, api_key: str):
    """Get a single batch job by ID."""
    client = genai.Client(api_key=api_key)
    name = _normalize_batch_name(batch_id)
    return client.batches.get(name=name)


def download_file(file_name: str, api_key: str) -> str:
    """Download a file from Gemini Files API and return its text content."""
    client = genai.Client(api_key=api_key)
    content_bytes = client.files.download(file=file_name)
    return content_bytes.decode("utf-8")


def parse_gemini_batch_results(batch_job, api_key: str) -> List[Dict[str, Any]]:
    """
    Zachováno kvůli zpětné kompatibilitě - pokud tuhle funkci někde voláš
    přímo (mimo provider dict), název i chování zůstávají stejné.

    Interně je to teď jen tenký wrapper nad download_raw_batch +
    parse_raw_content, aby nevznikala duplicitní implementace stejné logiky.
    'standard' mode (přes provider['parse_batch_results']) tak automaticky
    běží přes stejný kód jako download_only/full/process_only - beze
    změny v main().
    """
    return parse_raw_content(download_raw_batch(batch_job, api_key))


def extract_text_from_gemini_record(record: Dict[str, Any]) -> Optional[str]:
    """
    Extract the text content from a Gemini batch result record.

    Gemini batch JSONL output typically has this structure:
    {
      "custom_id": "...",
      "response": {
        "candidates": [
          {
            "content": {
              "parts": [{"text": "..."}]
            }
          }
        ]
      }
    }
    or for inline results, the record already has a "text" key.
    """
    # Inline result already extracted
    if "text" in record:
        return record["text"]

    # File-based result — traverse the response structure
    try:
        response = record.get("response", {})

        # Handle error responses
        if "error" in record or response.get("error"):
            return None

        candidates = response.get("candidates", [])
        if not candidates:
            return None

        texts = []
        for candidate in candidates:
            content = candidate.get("content", {})
            parts = content.get("parts", [])
            for part in parts:
                if isinstance(part, dict) and "text" in part:
                    texts.append(part["text"])

        return "\n".join(texts).strip() if texts else None

    except Exception as e:
        print("[DEBUG] extract_text_from_gemini_record error:", e)
        return None


def get_gemini_batch_status(batch_job) -> str:
    """
    Normalize Gemini batch job state to a simple string.
    Maps Gemini states to logical equivalents used in the processing flow.
    """
    state = getattr(batch_job, "state", None)
    if state is None:
        return "unknown"

    state_name = state.name if hasattr(state, "name") else str(state)

    mapping = {
        "JOB_STATE_SUCCEEDED": "completed",
        "JOB_STATE_FAILED": "failed",
        "JOB_STATE_RUNNING": "in_progress",
        "JOB_STATE_PENDING": "in_progress",
        "JOB_STATE_QUEUED": "in_progress",
        "JOB_STATE_CANCELLING": "in_progress",
        "JOB_STATE_CANCELLED": "failed",
        "JOB_STATE_PAUSED": "in_progress",
        "JOB_STATE_UNSPECIFIED": "unknown",
    }

    return mapping.get(state_name, "unknown")


def get_batch_id(batch_job) -> str:
    """Extract a clean batch ID from a batch job object."""
    name = getattr(batch_job, "name", "") or ""
    # name is like "batches/12345" — return just the ID part
    return name.split("/")[-1] if "/" in name else name


def is_error_record(record: Dict[str, Any]) -> bool:
    """
    Check if a single Gemini record has an error.

    Gemini batch results may have errors at the record level or in the response.
    """
    return "error" in record or record.get("response", {}).get("error")


def parse_error_records(all_records: List[Dict[str, Any]], store_error_custom_ids: bool) -> Tuple[int, List[str]]:
    """
    Count error records and collect their custom_ids.
    Uses the shared implementation with the Gemini error check.
    """
    return base_parse_error_records(all_records, store_error_custom_ids, is_error_record)


def get_tokens_from_records(records: List[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    """
    Sum the token usage of already parsed Gemini batch records.

    Gemini reports thinking separately: candidatesTokenCount is only the
    visible answer and thoughtsTokenCount is added on top of it, so
    totalTokenCount = prompt + candidates + thoughts.

    Returns None when no record carries usageMetadata (e.g. inline results).
    """
    total_input_tokens = 0
    total_output_tokens = 0
    total_thinking_tokens = 0
    found_usage = False

    for record in records:
        response_data = record.get("response", record)
        if not isinstance(response_data, dict):
            continue

        usage = response_data.get("usageMetadata")
        if not usage:
            continue

        found_usage = True
        total_input_tokens += int(usage.get("promptTokenCount") or 0)
        total_output_tokens += int(usage.get("candidatesTokenCount") or 0)
        total_thinking_tokens += int(usage.get("thoughtsTokenCount") or 0)

    if not found_usage:
        return None

    return {
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "thinking_tokens": total_thinking_tokens,
        "total_tokens": total_input_tokens + total_output_tokens + total_thinking_tokens
    }


def get_batch_tokens(batch_job, api_key: str) -> Optional[Dict[str, int]]:
    """
    Get token consumption information for a Gemini batch job.

    Downloads and parses the output file to extract token counts from usageMetadata.
    Uses the workaround for the 40-char bug in Google API.

    Args:
        batch_job: Batch job object from Gemini SDK
        api_key: API key for Gemini

    Returns:
        Dictionary with token counts or None if not available
        (same shape as get_tokens_from_records)
    """
    try:
        # Get the responses file from batch job
        responses_file = None
        if hasattr(batch_job, 'dest') and batch_job.dest:
            if hasattr(batch_job.dest, 'file_name') and batch_job.dest.file_name:
                responses_file = batch_job.dest.file_name

        if not responses_file:
            return None

        # WORKAROUND v2 for Google API 40-char bug:
        # Prefix /download/ and suffix :download to force correct method
        download_url = f"https://generativelanguage.googleapis.com/download/v1beta/{responses_file}:download?alt=media"
        headers = {"x-goog-api-key": api_key}

        file_response = requests.get(download_url, headers=headers)

        if file_response.status_code != 200:
            return None

        return get_tokens_from_records(parse_raw_content(file_response.text))

    except Exception as e:
        print(f"[DEBUG] Failed to get batch tokens: {e}")
        return None


def get_processing_time(batch_job) -> Optional[float]:
    """
    Get processing time for a Gemini batch job in seconds.

    Gemini uses create_time and end_time fields.

    Args:
        batch_job: Batch job object from Gemini SDK

    Returns:
        Processing time in seconds or None if not available
    """
    try:
        # Get start time (create_time)
        start_time = None
        if hasattr(batch_job, 'create_time') and batch_job.create_time:
            start_time = batch_job.create_time

        # Get end time
        end_time = None
        if hasattr(batch_job, 'end_time') and batch_job.end_time:
            end_time = batch_job.end_time

        if start_time and end_time:
            # Calculate difference in seconds
            diff = (end_time - start_time).total_seconds()
            return diff if diff > 0 else None

        return None

    except Exception as e:
        print(f"[DEBUG] Failed to get processing time: {e}")
        return None


def get_model_name(batch_job) -> Optional[str]:
    """
    Get the model name used for a Gemini batch job.

    Args:
        batch_job: Batch job object from Gemini SDK

    Returns:
        Model name or None if not available
    """
    try:
        if hasattr(batch_job, 'model') and batch_job.model:
            # Gemini returns full path like 'models/gemini-3.1-pro-preview'
            # Extract just the model name
            model_str = batch_job.model
            if '/' in model_str:
                return model_str.split('/')[-1]
            return model_str
        return None
    except Exception as e:
        print(f"[DEBUG] Failed to get model name: {e}")
        return None


def get_batch_created_time(batch_job) -> Optional[str]:
    """
    Get the creation time of a Gemini batch job in DD-MM-YYYY : HH-MM-SS format.

    Args:
        batch_job: Batch job object from Gemini SDK

    Returns:
        Time string in DD-MM-YYYY : HH-MM-SS format or None if not available
    """
    try:
        if hasattr(batch_job, 'create_time') and batch_job.create_time:
            dt = batch_job.create_time
            # Převod z UTC do naší časové zóny (zohledňuje i letní/zimní čas)
            local_dt = dt.astimezone(ZoneInfo("Europe/Malta"))
            return local_dt.strftime("%d-%m-%Y : %H-%M-%S")
        return None
    except Exception as e:
        print(f"[DEBUG] Failed to get created time: {e}")
        return None


def get_batch_completed_time(batch_job) -> Optional[str]:
    """
    Get the completion time of a Gemini batch job in DD-MM-YYYY : HH-MM-SS format.

    Args:
        batch_job: Batch job object from Gemini SDK

    Returns:
        Time string in DD-MM-YYYY : HH-MM-SS format or None if not available
    """
    try:
        if hasattr(batch_job, 'end_time') and batch_job.end_time:
            dt = batch_job.end_time
            # Převod z UTC do naší časové zóny
            local_dt = dt.astimezone(ZoneInfo("Europe/Malta"))
            return local_dt.strftime("%d-%m-%Y : %H-%M-%S")
        return None
    except Exception as e:
        print(f"[DEBUG] Failed to get completed time: {e}")
        return None


def get_request_counts(batch_job, api_key: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Get completed and failed request counts for a Gemini batch job.

    Gemini SDK doesn't provide request counts directly, so we need to call
    the REST API to get batchStats from metadata.

    Args:
        batch_job: Batch job object from Gemini SDK
        api_key: API key for Gemini

    Returns:
        Tuple of (completed_count, failed_count); (None, None) when the
        counts cannot be read, so a failed lookup is not mistaken for zero
    """
    try:
        # Get batch name
        batch_name = None
        if hasattr(batch_job, 'name') and batch_job.name:
            batch_name = batch_job.name

        if not batch_name:
            return None, None

        # Call REST API to get batch metadata
        url = f"https://generativelanguage.googleapis.com/v1beta/{batch_name}"
        headers = {"x-goog-api-key": api_key}

        response = requests.get(url, headers=headers)

        if response.status_code != 200:
            return None, None

        data = response.json()

        # Extract statistics from metadata
        metadata = data.get("metadata", {})
        stats = metadata.get("batchStats", {})

        successful = int(stats.get("successfulRequestCount", 0))
        failed = int(stats.get("failedRequestCount", 0))

        return successful, failed

    except Exception as e:
        print(f"[DEBUG] Failed to get request counts: {e}")
        return None, None


def download_raw_batch(batch_job, api_key: str) -> str:
    """
    Vrátí obsah dokončeného Gemini batche jako JSONL text.

    - File-based výsledky: obsah je identický s tím, co vrátí API (žádná
      transformace).
    - Inline výsledky: nejde o "syrová" data z API (jsou to objekty ze SDK),
      proto se serializují do stejného normalizovaného tvaru
      (custom_id/text/error), jaký očekává zbytek pipeline - jinak by je
      parse_raw_content nedokázal správně zpětně rozparsovat.

    POZNÁMKA: pokud tvůj stávající download_file(result_file_name, api_key)
    dělá něco navíc oproti client.files.download() (retry, logování...),
    nahraď níže přímé volání client.files.download() voláním download_file,
    ať se ta logika neztratí:
        raw = download_file(batch_job.dest.file_name, api_key)
    """
    client = genai.Client(api_key=api_key)

    if getattr(batch_job, "dest", None) and getattr(batch_job.dest, "file_name", None):
        content_bytes = client.files.download(file=batch_job.dest.file_name)
        return content_bytes.decode("utf-8")

    if getattr(batch_job, "dest", None) and getattr(batch_job.dest, "inlined_responses", None):
        lines = []
        for i, inline_response in enumerate(batch_job.dest.inlined_responses):
            rec: Dict[str, Any] = {
                "custom_id": getattr(inline_response, "custom_id", None) or str(i)
            }

            if inline_response.response:
                try:
                    rec["text"] = inline_response.response.text
                except AttributeError:
                    rec["text"] = str(inline_response.response)
            elif inline_response.error:
                rec["error"] = str(inline_response.error)

            lines.append(json.dumps(rec, ensure_ascii=False))

        return "\n".join(lines) + "\n"

    return ""

"""
OpenAI Provider for OCR Batch Processing
Implements the provider interface for OpenAI API.

Everything that is not specific to the OpenAI API (config, prompts, path ids,
batch file splitting, state files, problem log, raw JSONL parsing) lives in
ai_providers/base.py and is only re-exported here, so the provider dictionary
built by provider_selector.py stays complete.
"""
import os
import json
from pathlib import Path
from typing import List, Dict, Tuple, Any, Optional
from datetime import datetime, timezone
from openai import OpenAI
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


# ============================================================================
# CONFIGURATION
# ============================================================================

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


# ============================================================================
# BATCH CREATION
# ============================================================================

def make_jsonl_line(
    custom_id: str,
    mime_type: str,
    image_data: str,
    upload_method: str,
    model_name: str,
    system_prompt: str = "",
    user_prompt: str = ""
) -> str:
    """
    Create a single JSONL line for OpenAI batch processing.

    OpenAI format:
    {
      "custom_id": "...",
      "method": "POST",
      "url": "/v1/responses",
      "body": {
        "model": "...",
        "input": [
          {"role": "system", "content": "..."},
          {
            "role": "user",
            "content": [
              {"type": "input_text", "text": "..."},
              {"type": "input_image", "image_url": "..."}  // or "file_id": "..."
            ]
          }
        ]
      }
    }
    """
    # Build image content based on upload method
    if upload_method == "file_api":
        image_content = {"type": "input_image", "file_id": image_data}
    else:
        # base64 or pass_a_url
        image_content = {"type": "input_image", "image_url": image_data}

    body = {
        "model": model_name,
        "input": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": user_prompt},
                    image_content,
                ],
            },
        ],
    }

    envelope = {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/responses",
        "body": body,
    }

    return json.dumps(envelope, ensure_ascii=False) + "\n"


def get_request_custom_id(request: Dict[str, Any]) -> Optional[str]:
    """
    Return the request id of one line of a generated OpenAI batch file.

    OpenAI batch input files carry the id in the 'custom_id' field (Gemini
    uses 'id'), which is why this lives in the provider.
    """
    return request.get("custom_id")


def cloudflare_path_to_public_url(key_path: Path, config: Dict[str, Any]) -> str:
    """Convert Cloudflare R2 path to public URL."""
    from urllib.parse import quote
    image_config = config.get('image_management', {})
    cloudflare_config = image_config.get('cloudflare', {})
    public_base_url = cloudflare_config.get('public_base_url', '')
    return f"{public_base_url}/{quote(key_path.as_posix())}"


def upload_image_to_openai_files_api(path: Path, api_key: str) -> str:
    """Upload image to OpenAI Files API with purpose='vision' and return file_id."""
    client = OpenAI(api_key=api_key)
    with open(path, "rb") as f:
        result = client.files.create(file=f, purpose="vision")
    return result.id


def upload_image_to_openai(
    path: Path,
    api_key: str,
    config: Dict[str, Any],
    get_image_file_for_upload_fn
) -> Tuple[str, str]:
    """
    Upload one image (or PDF) via the OpenAI Files API and return
    (file_id, mime_type).

    Mirrors upload_image_to_gemini: the storage adapter decides how to get a
    local file. The local adapter returns the original path, the Cloudflare
    adapter downloads the object into a temporary file which is deleted here.
    """
    file_path, mime = get_image_file_for_upload_fn(path, config)

    # The adapter returned a temporary copy if the path is not the original one.
    is_temporary_file = file_path != str(path)

    try:
        file_id = upload_image_to_openai_files_api(Path(file_path), api_key)
    finally:
        if is_temporary_file:
            os.remove(file_path)

    return file_id, mime


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
    image is uploaded to the OpenAI Files API at the moment its line is built,
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
                print(f"[INFO] Uploading image to OpenAI Files API: {img_path}")
                image_data, mime_type = upload_image_to_openai(
                    img_path, api_key, config, get_image_file_for_upload_fn
                )

            elif upload_method == "pass_a_url":
                # Use public URL from Cloudflare R2
                image_data = cloudflare_path_to_public_url(img_path, config)
                mime_type = "image/jpeg"  # MIME type not needed for URL, but required by function signature

            else:
                raise ValueError("Invalid IMAGE_UPLOAD_METHOD. Use 'base64', 'file_api', or 'pass_a_url'.")

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

    # The size / line count splitting is not OpenAI specific, so it is shared.
    return split_lines_into_batch_files(lines, output_dir, prefix, limits)


def upload_file_to_provider(path: Path, api_key: str) -> Tuple[Any, str]:
    """Upload batch JSONL file to OpenAI and return uploaded file and display name."""
    client = OpenAI(api_key=api_key)
    display_name = path.stem
    print(f"[INFO] Uploading batch JSONL {path.name} as '{display_name}' to OpenAI...")

    with open(path, "rb") as f:
        uploaded_file = client.files.create(file=f, purpose="batch")

    print(f"[INFO] Uploaded file ID: {uploaded_file.id}")
    return uploaded_file, display_name


def create_batch_job(uploaded_file, display_name: str, model_name: str, api_key: str):
    """Create a batch job from uploaded file."""
    client = OpenAI(api_key=api_key)
    print(f"[INFO] Creating batch job for {uploaded_file.id}...")

    job = client.batches.create(
        input_file_id=uploaded_file.id,
        endpoint="/v1/responses",
        completion_window="24h",
    )
    return job


# ============================================================================
# BATCH RETRIEVAL
# ============================================================================

def list_batches(api_key: str):
    """List all batch jobs from OpenAI API."""
    client = OpenAI(api_key=api_key)
    return list(client.batches.list())


def get_batch(batch_id: str, api_key: str):
    """Get a single batch job by ID."""
    client = OpenAI(api_key=api_key)
    return client.batches.retrieve(batch_id)


def download_file(file_id: str, api_key: str) -> str:
    """Download a file from OpenAI Files API and return its text content."""
    client = OpenAI(api_key=api_key)
    content_bytes = client.files.content(file_id).read()
    return content_bytes.decode("utf-8")


def parse_openai_batch_results(batch_job, api_key: str) -> List[Dict[str, Any]]:
    """
    Zachováno kvůli zpětné kompatibilitě - stejný název a signatura.
    Interně delegováno na download_raw_batch + parse_raw_content, takže nově
    (na rozdíl od původní verze) zahrnuje i error_file_id výsledky - přesně
    jako download_only/full/process_only.
    """
    return parse_raw_content(download_raw_batch(batch_job, api_key))


def extract_text_from_openai_record(record: Dict[str, Any]) -> Optional[str]:
    """
    Extract the text content from an OpenAI batch result record.

    OpenAI batch JSONL output structure:
    {
      "custom_id": "...",
      "response": {
        "body": {
          "output": [
            {"content": [{"type": "text", "text": "..."}]}
          ]
        }
      }
    }
    """
    try:
        outputs = record.get("response", {}).get("body", {}).get("output", [])
        texts = []

        for item in outputs:
            content = item.get("content")

            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and "text" in block:
                        texts.append(block["text"])

        return "\n".join(texts).strip() if texts else None

    except Exception as e:
        print("[DEBUG] extract_text_from_openai_record error:", e)
        return None


def get_openai_batch_status(batch_job) -> str:
    """
    Get the status of an OpenAI batch job.

    OpenAI status values: "completed", "failed", "in_progress", "cancelling", "cancelled"
    """
    status = getattr(batch_job, "status", None)
    if status is None:
        return "unknown"

    # Map OpenAI statuses to standard values
    mapping = {
        "completed": "completed",
        "failed": "failed",
        "in_progress": "in_progress",
        "cancelling": "in_progress",
        "cancelled": "failed",
        "validating": "in_progress",
        "finalizing": "in_progress",
        "expired": "failed"
    }

    return mapping.get(status, "unknown")


def get_batch_id(batch_job) -> str:
    """Extract batch ID from OpenAI batch job object."""
    return getattr(batch_job, "id", "")


def batch_created_after(batch_job, threshold: datetime) -> bool:
    """
    Check if batch job was created after threshold datetime.

    OpenAI uses batch.created_at (timestamp).
    """
    created_at = getattr(batch_job, "created_at", None)
    if created_at is None:
        return False

    batch_dt = datetime.fromtimestamp(created_at, tz=timezone.utc)
    return batch_dt >= threshold


# ============================================================================
# ERROR HANDLING
# ============================================================================

def is_error_record(record: Dict[str, Any]) -> bool:

    root_error = record.get("error")
    response = record.get("response") or {}
    response_error = response.get("error")
    body = response.get("body") or {}
    body_error = body.get("error")

    return bool(root_error or response_error or body_error)


def parse_error_records(all_records: List[Dict[str, Any]], store_error_custom_ids: bool) -> Tuple[int, List[str]]:
    """
    Count error records and collect their custom_ids.
    Uses the shared implementation with the OpenAI error check.
    """
    return base_parse_error_records(all_records, store_error_custom_ids, is_error_record)


def _split_reasoning_tokens(input_tokens: int, output_tokens: int, reasoning_tokens: int) -> Dict[str, int]:
    """
    Build the shared token dictionary from OpenAI numbers.

    OpenAI counts reasoning INSIDE output_tokens. The shared shape keeps
    thinking separate (as Gemini reports it), so it is subtracted here.
    """
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens - reasoning_tokens,
        "thinking_tokens": reasoning_tokens,
        "total_tokens": input_tokens + output_tokens
    }


def get_tokens_from_records(records: List[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    """
    Sum the token usage of already parsed OpenAI batch records
    (response.body.usage of the /v1/responses endpoint).

    Returns None when no record carries usage.
    """
    total_input_tokens = 0
    total_output_tokens = 0
    total_reasoning_tokens = 0
    found_usage = False

    for record in records:
        response = record.get("response") or {}
        body = response.get("body") or {}
        usage = body.get("usage")
        if not usage:
            continue

        found_usage = True
        output_details = usage.get("output_tokens_details") or {}

        total_input_tokens += int(usage.get("input_tokens") or 0)
        total_output_tokens += int(usage.get("output_tokens") or 0)
        total_reasoning_tokens += int(output_details.get("reasoning_tokens") or 0)

    if not found_usage:
        return None

    return _split_reasoning_tokens(total_input_tokens, total_output_tokens, total_reasoning_tokens)


def get_batch_tokens(batch_job, api_key: str) -> Optional[Dict[str, int]]:
    """
    Get token consumption information for an OpenAI batch job.

    OpenAI provides token counts directly in the batch.usage field.

    Args:
        batch_job: Batch job object from OpenAI SDK
        api_key: API key for OpenAI (not used for OpenAI, kept for interface consistency)

    Returns:
        Dictionary with token counts or None if not available
        (same shape as get_tokens_from_records)
    """
    try:
        # OpenAI batch object has usage field with token counts
        if hasattr(batch_job, 'usage') and batch_job.usage:
            usage = batch_job.usage

            input_tokens = getattr(usage, 'input_tokens', 0) or 0
            output_tokens = getattr(usage, 'output_tokens', 0) or 0
            output_details = getattr(usage, 'output_tokens_details', None)
            reasoning_tokens = getattr(output_details, 'reasoning_tokens', 0) or 0

            return _split_reasoning_tokens(input_tokens, output_tokens, reasoning_tokens)

        return None

    except Exception as e:
        print(f"[DEBUG] Failed to get batch tokens: {e}")
        return None


def get_processing_time(batch_job) -> Optional[float]:
    """
    Get processing time for an OpenAI batch job in seconds.

    OpenAI uses created_at and completed_at timestamps (Unix timestamps).

    Args:
        batch_job: Batch job object from OpenAI SDK

    Returns:
        Processing time in seconds or None if not available
    """
    try:
        # Get start time (created_at - Unix timestamp)
        start_time = None
        if hasattr(batch_job, 'created_at') and batch_job.created_at:
            start_time = batch_job.created_at

        # Get end time (completed_at - Unix timestamp)
        end_time = None
        if hasattr(batch_job, 'completed_at') and batch_job.completed_at:
            end_time = batch_job.completed_at

        if start_time and end_time:
            # Calculate difference in seconds (both are Unix timestamps)
            diff = end_time - start_time
            return diff if diff > 0 else None

        return None

    except Exception as e:
        print(f"[DEBUG] Failed to get processing time: {e}")
        return None


def get_model_name(batch_job) -> Optional[str]:
    """
    Get the model name used for an OpenAI batch job.

    Args:
        batch_job: Batch job object from OpenAI SDK

    Returns:
        Model name or None if not available
    """
    try:
        if hasattr(batch_job, 'model') and batch_job.model:
            return batch_job.model
        return None
    except Exception as e:
        print(f"[DEBUG] Failed to get model name: {e}")
        return None


def get_batch_created_time(batch_job) -> Optional[str]:
    """
    Get the creation time of an OpenAI batch job in DD-MM-YYYY : HH-MM-SS format.

    Args:
        batch_job: Batch job object from OpenAI SDK

    Returns:
        Time string in DD-MM-YYYY : HH-MM-SS format or None if not available
    """
    try:
        if hasattr(batch_job, 'created_at') and batch_job.created_at:
            # Convert Unix timestamp to datetime
            dt = datetime.fromtimestamp(batch_job.created_at, tz=ZoneInfo("Europe/Malta"))
            return dt.strftime("%d-%m-%Y : %H-%M-%S")
        return None
    except Exception as e:
        print(f"[DEBUG] Failed to get created time: {e}")
        return None


def get_batch_completed_time(batch_job) -> Optional[str]:
    """
    Get the completion time of an OpenAI batch job in DD-MM-YYYY : HH-MM-SS format.

    Args:
        batch_job: Batch job object from OpenAI SDK

    Returns:
        Time string in DD-MM-YYYY : HH-MM-SS format or None if not available
    """
    try:
        if hasattr(batch_job, 'completed_at') and batch_job.completed_at:
            # Convert Unix timestamp to datetime
            dt = datetime.fromtimestamp(batch_job.completed_at, tz=ZoneInfo("Europe/Malta"))
            return dt.strftime("%d-%m-%Y : %H-%M-%S")
        return None
    except Exception as e:
        print(f"[DEBUG] Failed to get completed time: {e}")
        return None


def get_request_counts(batch_job, api_key: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Get completed and failed request counts for an OpenAI batch job.

    OpenAI provides request counts directly in the batch.request_counts field.

    Args:
        batch_job: Batch job object from OpenAI SDK
        api_key: API key for OpenAI (not used, kept for interface consistency)

    Returns:
        Tuple of (completed_count, failed_count); (None, None) when the
        counts cannot be read, so a failed lookup is not mistaken for zero
    """
    try:
        if hasattr(batch_job, 'request_counts') and batch_job.request_counts:
            rc = batch_job.request_counts
            completed = getattr(rc, 'completed', 0) or 0
            failed = getattr(rc, 'failed', 0) or 0
            return completed, failed

        return None, None

    except Exception as e:
        print(f"[DEBUG] Failed to get request counts: {e}")
        return None, None


def download_raw_batch(batch_job, api_key: str) -> str:
    """
    Download raw data of a completed OpenAI batch as a JSONL text string.

    OpenAI splits results into two files: output_file_id for successes and
    error_file_id for failures. This function concatenates both (if available)
    so the resulting string contains all records exactly as returned by the API.
    """
    raw_content = ""

    # Download successful results
    if getattr(batch_job, "output_file_id", None):
        raw_content += download_file(batch_job.output_file_id, api_key)
        if raw_content and not raw_content.endswith("\n"):
            raw_content += "\n"

    # Download error results
    if getattr(batch_job, "error_file_id", None):
        raw_content += download_file(batch_job.error_file_id, api_key)
        if raw_content and not raw_content.endswith("\n"):
            raw_content += "\n"

    return raw_content

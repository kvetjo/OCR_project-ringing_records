"""
Make OCR Batch
Creates and uploads OCR batch jobs for processing.
This script generates JSONL batch files and optionally uploads them to the AI provider.
"""
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

# Import provider selector for dynamic provider selection
sys.path.insert(0, str(Path(__file__).parent))

from ai_providers.base import (
    load_config,
    build_custom_id,
    find_batch_file_in_directory,
    filter_jsonl_by_allowed_images,
    read_custom_ids_from_jsonl,
    append_processed_items,
)
from ai_providers.provider_selector import get_provider


def now_string() -> str:
    """Current local time in the format used across the whole pipeline."""
    return datetime.now(ZoneInfo("Europe/Malta")).strftime("%d-%m-%Y : %H-%M-%S")


def build_item_info(
    batch_file: Path,
    batch_job_id: str,
    config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Build the part of a processed items log record that is the same for every
    request inside one batch file.

    'results_downloaded' starts as False. get_OCR_results.py flips it once the
    results of this batch job have been downloaded and validated.
    """
    run_settings = config.get('run_settings', {})
    batch_config = config.get('batch_generation', {})
    validation_config = config.get('validation', {})

    return {
        "batch_file": batch_file.name,
        "batch_job_id": batch_job_id,
        "provider": run_settings.get('active_provider', ''),
        "model": run_settings.get('model_name', ''),
        "validator": validation_config.get('active_validator', ''),
        "image_upload_method": batch_config.get('image_upload_method', ''),
        # Always True: a record is only written after a successful upload.
        "uploaded": True,
        "uploaded_at": now_string(),
        "results_downloaded": False,
        "results_downloaded_at": None,
    }


def log_batch_requests(
    batch_file: Path,
    batch_job_id: str,
    processed_log_path: Path,
    get_request_custom_id_fn,
    config: Dict[str, Any]
) -> None:
    """
    Record every request of one batch file in the processed items log.

    Only called after the batch really reached the API, so batch_job_id is
    always a real id. That is what lets get_OCR_results.py find these lines
    again later.

    The ids are read back from the batch file itself, so only images that
    really ended up as a request are logged - an image that was skipped
    (unreadable path, failed image upload) never appears here.
    """
    custom_ids = read_custom_ids_from_jsonl(batch_file, get_request_custom_id_fn)

    if not custom_ids:
        print(f"[WARN] No requests found in {batch_file.name}, nothing logged")
        return

    item_info = build_item_info(batch_file, batch_job_id, config)
    append_processed_items(processed_log_path, custom_ids, item_info)

    print(f"[INFO] Logged {len(custom_ids)} requests from {batch_file.name} to {processed_log_path}")


def upload_and_log_batch(
    batch_file: Path,
    api_key: str,
    model_name: str,
    provider: Dict[str, Any],
    processed_log_path: Path,
    config: Dict[str, Any]
) -> Optional[str]:
    """
    Upload one batch file, create the batch job and log its requests.

    Returns the batch job id, or None when the upload failed. A failed upload
    is NOT written to the processed items log: without a batch job id such a
    line could never be matched to its results and would sit there as a
    permanent "not downloaded" entry.
    """
    upload_file_to_provider_fn = provider['upload_file_to_provider']
    create_batch_job_fn = provider['create_batch_job']
    get_batch_id_fn = provider['get_batch_id']
    get_request_custom_id_fn = provider['get_request_custom_id']

    try:
        uploaded_file, display_name = upload_file_to_provider_fn(batch_file, api_key)

        job = create_batch_job_fn(uploaded_file, display_name, model_name, api_key)
        batch_job_id = get_batch_id_fn(job)

        print(f"[SUCCESS] Batch job created: {batch_job_id}")

    except Exception as e:
        print(f"[ERROR] Failed to process batch {batch_file.name}: {e}")
        print(f"[ERROR] Nothing logged for {batch_file.name} - it never reached the API.")
        return None

    log_batch_requests(
        batch_file,
        batch_job_id,
        processed_log_path,
        get_request_custom_id_fn,
        config
    )

    return batch_job_id


def filter_images(
    images: List[Path],
    processing_mode: str,
    allowed_batches: List[str],
    allowed_images: List[str],
    provider: Dict[str, Any],
    config: Dict[str, Any]
) -> List[Path]:
    """
    Keep only the images selected by processing.mode.

    Modes:
      all      - no filtering
      batches  - only images inside one of the allowed_batches folders
      explicit - only images whose custom_id is in allowed_images
    """
    extract_batch_page_ids_fn = provider['extract_batch_page_ids']
    is_in_allowed_batch_fn = provider['is_in_allowed_batch']

    filtered_images: List[Path] = []

    for img in images:
        try:
            batch_id, page_id = extract_batch_page_ids_fn(img, config)
            custom_id = build_custom_id(batch_id, page_id)
        except Exception:
            # The path does not follow the naming convention - skip it.
            continue

        if processing_mode == "batches":
            if not is_in_allowed_batch_fn(img, allowed_batches):
                continue

        elif processing_mode == "explicit":
            if custom_id not in allowed_images:
                continue

        elif processing_mode == "all":
            pass  # No filtering

        else:
            print(f"[WARN] Unknown processing mode: {processing_mode}")
            continue

        filtered_images.append(img)

    return filtered_images


def main():
    """Main function to create and upload OCR batches."""
    # Record start time
    start_time = time.time()

    # Load config (secrets come from the .env file)
    config = load_config()

    # Get configuration values
    run_settings = config.get('run_settings', {})
    active_provider = run_settings.get('active_provider', 'gemini')
    model_name = run_settings.get('model_name', 'gemini-3.1-pro-preview')

    # Load the correct provider based on config
    provider = get_provider(active_provider)

    api_keys = config.get('api_keys', {})
    api_key = api_keys.get(active_provider, '')

    # A local runtime (Ollama) talks to a server on this machine or over SSH,
    # so there is no API key to look up.
    if not api_key and not provider.get('local_runtime'):
        print(f"ERROR: API key for provider '{active_provider}' not found. Check your .env file.")
        sys.exit(1)

    directories = config.get('directories', {})
    state_dir_base = directories.get('state_dir_base', 'state')
    output_dir_base = directories.get('output_dir_base', 'output')
    batch_out_dir = directories.get('batch_out_dir', 'batches_out')
    processed_log_file = directories.get('processed_log_file', 'processed_items.txt')

    batch_config = config.get('batch_generation', {})
    output_prefix = batch_config.get('output_prefix', 'batch-gemini')
    batch_operation_mode = batch_config.get('batch_operation_mode', 'generate_and_upload')
    upload_files = batch_config.get('upload_files', [])

    processing = config.get('processing', {})
    processing_mode = processing.get('mode', 'batches')
    allowed_batches = processing.get('allowed_batches', [])
    allowed_images = processing.get('allowed_images', [])
    image_extensions = tuple(processing.get('image_extensions', ['.jpg', '.jpeg', '.png', '.webp', '.pdf']))

    image_config = config.get('image_management', {})
    local_root = Path(image_config.get('local_root', '.'))

    # Set paths
    state_dir = Path(state_dir_base)
    output_dir = Path(output_dir_base)
    batch_output_dir = Path(batch_out_dir)
    processed_log_path = Path(processed_log_file)

    # Create directories
    state_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    batch_output_dir.mkdir(parents=True, exist_ok=True)

    # Validation: try_again_upload mode requires explicit processing_mode
    if batch_operation_mode == "try_again_upload":
        if processing_mode != "explicit":
            print("[ERROR] batch_operation_mode 'try_again_upload' requires processing_mode to be 'explicit'")
            sys.exit(1)
        if not allowed_images:
            print("[ERROR] batch_operation_mode 'try_again_upload' requires allowed_images to be specified")
            sys.exit(1)
        if not upload_files:
            print("[ERROR] batch_operation_mode 'try_again_upload' requires upload_files to be specified")
            sys.exit(1)

    # Get image adapter and collect images
    image_adapter = provider['get_image_adapter'](config)
    collect_image_files_fn = image_adapter['collect_image_files']

    images = collect_image_files_fn(local_root, image_extensions, config)

    print(f"[INFO] Found {len(images)} images before filtering")

    filtered_images = filter_images(
        images,
        processing_mode,
        allowed_batches,
        allowed_images,
        provider,
        config
    )

    print(f"[INFO] Processing {len(filtered_images)} images after filtering")

    # ------------------------------------------------------------------
    # Local runtime (Ollama): there is no batch API, so nothing is generated
    # and nothing is uploaded. The images are sent to the model one by one and
    # the finished batch is written straight into directories.raw_dir_base,
    # ready for get_OCR_results.py. The whole batch_generation section of the
    # config is unused here.
    #
    # Everything above this point - the image adapter and the processing
    # filters - has already run exactly as it does for gemini / openai.
    # ------------------------------------------------------------------
    if provider.get('local_runtime'):
        batch_id, raw_file_path = provider['run_local_batch'](
            filtered_images,
            config,
            model_name
        )

        if batch_id:
            # The raw file carries the request id in the same 'id' field a
            # generated batch file does, so this is the usual logging path.
            log_batch_requests(
                raw_file_path,
                batch_id,
                processed_log_path,
                provider['get_request_custom_id'],
                config
            )

            print(f"[SUCCESS] Local batch finished: {batch_id}")
            print(f"[INFO] Raw results: {raw_file_path}")
            print(f'[INFO] Next step: in get_OCR_results.py run main(batch_ids=["{batch_id}"])')
        else:
            print("[ERROR] The local batch produced no results.")

        end_time = time.time()
        duration_minutes = (end_time - start_time) / 60
        duration_seconds = end_time - start_time
        print(f"[INFO] Script execution completed in {duration_minutes:.2f} minutes (which is {duration_seconds} seconds)")

        print("[INFO] Batch creation completed")
        return

    # Build JSONL batches if mode is 'generate' or 'generate_and_upload'
    generated: List[Path] = []

    if batch_operation_mode in ['generate', 'generate_and_upload']:
        build_jsonl_batches_fn = provider['build_jsonl_batches']
        generated = build_jsonl_batches_fn(
            filtered_images,
            batch_output_dir,
            prefix=output_prefix,
            config=config,
            api_key=api_key,
            model_name=model_name
        )
        print(f"[INFO] JSONL files created: {[str(f) for f in generated]}")

    # ------------------------------------------------------------------
    # Mode: generate only - nothing is sent, so nothing is logged.
    # The processed items log only records requests that reached the API, so
    # that every entry has a batch job id linking it to its results.
    # ------------------------------------------------------------------
    if batch_operation_mode == 'generate':
        print(f"[INFO] Generate-only mode: {len(generated)} batch file(s) created, nothing uploaded.")

    # ------------------------------------------------------------------
    # Mode: upload / generate_and_upload
    # ------------------------------------------------------------------
    elif batch_operation_mode in ['upload', 'generate_and_upload']:
        # Determine which files to upload
        if batch_operation_mode == 'upload':
            # Upload only the files named in the config
            batches = [batch_output_dir / f for f in upload_files if (batch_output_dir / f).exists()]

            if not batches:
                print(f"[ERROR] None of the files listed in upload_files were found in {batch_output_dir}")
                print(f"[ERROR] Looked for: {upload_files}")
                sys.exit(1)

            print(f"[INFO] Uploading specified files: {[f.name for f in batches]}")

        else:
            # generate_and_upload: send exactly what was just generated.
            # No fallback to "everything in the folder" - that used to re-send
            # old batches when the filter matched nothing.
            batches = generated

            if not batches:
                print("[ERROR] No JSONL files were generated, so there is nothing to upload.")
                print("[ERROR] Check processing.mode and the allowed_batches / allowed_images settings.")
                sys.exit(1)

        for batch in batches:
            upload_and_log_batch(
                batch,
                api_key,
                model_name,
                provider,
                processed_log_path,
                config
            )

    # ------------------------------------------------------------------
    # Mode: try_again_upload - resend a subset of an existing batch file
    # ------------------------------------------------------------------
    elif batch_operation_mode == 'try_again_upload':
        get_request_custom_id_fn = provider['get_request_custom_id']

        for filename in upload_files:
            # Find the file in batches_out directory or subdirectories
            original_file = find_batch_file_in_directory(batch_output_dir, filename)

            if not original_file:
                print(f"[WARN] File not found: {filename} (searched in {batch_output_dir} and subdirectories)")
                continue

            print(f"[INFO] Processing original file: {original_file}")

            # Keep only the requests listed in allowed_images
            filtered_file = filter_jsonl_by_allowed_images(
                original_file,
                allowed_images,
                get_request_custom_id_fn
            )

            upload_and_log_batch(
                filtered_file,
                api_key,
                model_name,
                provider,
                processed_log_path,
                config
            )

    else:
        print(f"[ERROR] Unknown batch_operation_mode: {batch_operation_mode}")
        sys.exit(1)

    # Record end time and print duration
    end_time = time.time()
    duration_minutes = (end_time - start_time) / 60
    duration_seconds = end_time - start_time
    print(f"[INFO] Script execution completed in {duration_minutes:.2f} minutes (which is {duration_seconds} seconds)")

    print("[INFO] Batch creation completed")


if __name__ == "__main__":
    main()

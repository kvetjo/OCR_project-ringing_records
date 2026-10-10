"""
Provider Interface Contract
This file defines the interface that all AI provider adapters must implement.
When adding a new provider (e.g., OpenAI, Claude), implement all functions below.

The interface is designed to be provider-agnostic where possible, but some
functions return provider-specific objects (e.g., batch job objects) that
must be handled by the provider implementation.

Not every function has to be written from scratch. Everything that does not
talk to the provider API already exists in ai_providers/base.py and can simply
be re-exported:

    from ai_providers.base import load_config, load_prompts, log_problem, ...

REQUIRED_PROVIDER_FUNCTIONS at the bottom of this file lists the keys that the
dictionary returned by provider_selector.get_provider() must contain. The
selector checks that list at runtime, so a missing function is reported with a
clear message instead of failing somewhere deep in the pipeline.
"""

from pathlib import Path
from typing import List, Dict, Tuple, Any, Optional
from datetime import datetime


# ============================================================================
# CONFIGURATION
# ============================================================================

def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """
    Load configuration from YAML file.
    
    Args:
        config_path: Path to config.yaml file
        
    Returns:
        Dictionary with configuration values
    """
    raise NotImplementedError("Provider must implement load_config")


def get_image_adapter(config: Dict[str, Any]):
    """
    Get the appropriate image storage adapter based on configuration.
    
    Args:
        config: Configuration dictionary
        
    Returns:
        Dictionary with image adapter functions:
        - collect_image_files: function to collect image files
        - get_image_base64_and_mime: function to get base64 encoded image
        - get_image_file_for_upload: function to get file for upload
    """
    raise NotImplementedError("Provider must implement get_image_adapter")


# ============================================================================
# PROMPTS
# ============================================================================

def load_prompts(filepath: str, prompt_key: str) -> Tuple[str, str]:
    """
    Load a YAML file and return the system and user prompts for a given key.
    
    Args:
        filepath: Path to prompts.yaml file
        prompt_key: Key to look up in prompts file (e.g., "PROMPT_2")
        
    Returns:
        Tuple of (system_prompt, user_prompt)
        
    Raises:
        FileNotFoundError: If prompts file doesn't exist
        KeyError: If prompt_key not found in file
    """
    raise NotImplementedError("Provider must implement load_prompts")


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
    Create a single JSONL line for batch processing.
    
    Note: The JSONL format varies by provider. Each provider must implement
    their own format (e.g., OpenAI uses different structure than Gemini).
    
    Args:
        custom_id: Unique identifier for this request
        mime_type: MIME type of the image (e.g., "image/jpeg", "application/pdf")
        image_data: Either base64 string or file URI depending on upload_method
        upload_method: "base64" or "file_api"
        model_name: Name of the model to use
        system_prompt: System prompt text
        user_prompt: User prompt text
        
    Returns:
        JSON string for the JSONL file
    """
    raise NotImplementedError("Provider must implement make_jsonl_line")


def extract_batch_page_ids(path: Path, config: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """
    Extract batch_id and page_id from file path.
    Expected format: .../batch_XX/page_YY.ext or similar.

    The naming convention is configurable under processing.path_parsing.
    A shared implementation is available in ai_providers/base.py.

    Args:
        path: Path to image file
        config: Configuration dictionary (optional, falls back to defaults)

    Returns:
        Tuple of (batch_id, page_id)

    Raises:
        ValueError: If path doesn't match expected format
    """
    raise NotImplementedError("Provider must implement extract_batch_page_ids")


def get_request_custom_id(request: Dict[str, Any]) -> Optional[str]:
    """
    Return the request id of a single line of a GENERATED batch input file.

    Note: the field name varies by provider:
    - Gemini: request["id"]
    - OpenAI: request["custom_id"]

    This is what keeps the id field out of the main scripts, so retry
    filtering and the processed items log work for any provider.

    Args:
        request: One parsed line of a generated batch JSONL file

    Returns:
        The request id, or None if the line has none
    """
    raise NotImplementedError("Provider must implement get_request_custom_id")


def build_jsonl_batches(
    image_paths: List[Path],
    output_dir: Path,
    prefix: str,
    config: Dict[str, Any],
    api_key: str,
    model_name: str
) -> List[Path]:
    """
    Build JSONL batch files from image paths.
    
    Args:
        image_paths: List of image file paths
        output_dir: Directory to save JSONL files
        prefix: Prefix for JSONL filenames
        config: Configuration dictionary
        api_key: API key for the provider
        model_name: Name of the model to use
        
    Returns:
        List of created JSONL file paths
    """
    raise NotImplementedError("Provider must implement build_jsonl_batches")


def upload_file_to_provider(path: Path, api_key: str) -> Tuple[Any, str]:
    """
    Upload batch JSONL file to provider and return uploaded file and display name.
    
    Args:
        path: Path to JSONL file to upload
        api_key: API key for the provider
        
    Returns:
        Tuple of (uploaded_file_object, display_name)
        Note: uploaded_file_object type depends on provider's SDK
    """
    raise NotImplementedError("Provider must implement upload_file_to_provider")


def create_batch_job(
    uploaded_file: Any,
    display_name: str,
    model_name: str,
    api_key: str
) -> Any:
    """
    Create a batch job from uploaded file.
    
    Args:
        uploaded_file: Uploaded file object from provider SDK
        display_name: Display name for the batch
        model_name: Name of the model to use
        api_key: API key for the provider
        
    Returns:
        Batch job object from provider SDK
    """
    raise NotImplementedError("Provider must implement create_batch_job")


# ============================================================================
# FILE OPERATIONS
# ============================================================================

def find_jsonl_files_in_output(output_dir: Path) -> List[Path]:
    """
    Find all JSONL files in the output directory.
    
    Args:
        output_dir: Directory to search
        
    Returns:
        List of JSONL file paths
    """
    raise NotImplementedError("Provider must implement find_jsonl_files_in_output")


def is_in_allowed_batch(path: Path, allowed_batches: List[str]) -> bool:
    """
    Check if path is in one of the allowed batches.
    
    Args:
        path: File path to check
        allowed_batches: List of allowed batch IDs
        
    Returns:
        True if path is in allowed batches
    """
    raise NotImplementedError("Provider must implement is_in_allowed_batch")


# Note: the processed items log (processed_items.txt) is provider-independent
# and lives entirely in ai_providers/base.py:
#   append_processed_items()          - one line per sent request
#   mark_processed_items_downloaded() - flag them once the results are in
# It is informative only; nothing in the pipeline reads it to decide anything.


# ============================================================================
# BATCH RETRIEVAL
# ============================================================================

def list_batches(api_key: str) -> List[Any]:
    """
    List all batch jobs for the API key.
    
    Args:
        api_key: API key for the provider
        
    Returns:
        List of batch job objects from provider SDK
    """
    raise NotImplementedError("Provider must implement list_batches")


def get_batch(batch_id: str, api_key: str) -> Any:
    """
    Get a specific batch job by ID.
    
    Args:
        batch_id: ID of the batch job
        api_key: API key for the provider
        
    Returns:
        Batch job object from provider SDK
    """
    raise NotImplementedError("Provider must implement get_batch")


def download_file(uri: str, output_path: Path, api_key: str):
    """
    Download a file from provider storage.
    
    Args:
        uri: URI or file ID of the file to download
        output_path: Local path to save the file
        api_key: API key for the provider
    """
    raise NotImplementedError("Provider must implement download_file")


def parse_batch_results(batch_job: Any, api_key: str) -> List[Dict[str, Any]]:
    """
    Parse and download results from a completed batch job.
    
    Note: The result structure varies by provider. Each provider must
    implement their own parsing logic to extract:
    - custom_id: Unique identifier
    - response: Provider's response object
    - status: Status of the request
    
    Args:
        batch_job: Batch job object from provider SDK
        api_key: API key for the provider
        
    Returns:
        List of result dictionaries, each containing:
        - custom_id: Unique identifier
        - response: Provider's response object
        - status: Status of the request
    """
    raise NotImplementedError("Provider must implement parse_batch_results")


def extract_text_from_record(record: Dict[str, Any]) -> str:
    """
    Extract text content from a provider's result record.
    
    Note: The response structure varies by provider:
    - Gemini: record.get("response", {}).get("candidates", [])[0].get("content", {}).get("parts", [])[0].get("text")
    - OpenAI: record["response"]["body"]["output"] with content blocks
    
    Args:
        record: Result record from parse_batch_results
        
    Returns:
        Extracted text content as string
    """
    raise NotImplementedError("Provider must implement extract_text_from_record")


def get_batch_status(batch_job: Any) -> str:
    """
    Get the status of a batch job.
    
    Note: Status values vary by provider:
    - Gemini: "completed", "failed", "in_progress", etc.
    - OpenAI: "completed", "failed", "in_progress", "cancelling", etc.
    
    Args:
        batch_job: Batch job object from provider SDK
        
    Returns:
        Status string (e.g., "completed", "failed", "in_progress")
    """
    raise NotImplementedError("Provider must implement get_batch_status")


def get_batch_id(batch_job: Any) -> str:
    """
    Get the ID of a batch job.
    
    Note: Field name varies by provider:
    - Gemini: batch_job.name
    - OpenAI: batch_job.id
    
    Args:
        batch_job: Batch job object from provider SDK
        
    Returns:
        Batch ID as string
    """
    raise NotImplementedError("Provider must implement get_batch_id")


def batch_created_after(batch_job: Any, threshold: datetime) -> bool:
    """
    Check if batch job was created after threshold datetime.
    
    Note: Field name varies by provider:
    - Gemini: batch_job.create_time
    - OpenAI: batch_job.created_at (timestamp)
    
    Args:
        batch_job: Batch job object from provider SDK
        threshold: Datetime threshold to compare against
        
    Returns:
        True if batch was created after threshold, False otherwise
    """
    raise NotImplementedError("Provider must implement batch_created_after")


def get_batch_tokens(batch_job: Any, api_key: str) -> Optional[Dict[str, int]]:
    """
    Get token consumption information for a batch job.
    
    Note: Token retrieval varies by provider:
    - Gemini: Parse from output file (usageMetadata in each response)
    - OpenAI: Available in batch.usage field
    
    Args:
        batch_job: Batch job object from provider SDK
        api_key: API key for the provider
        
    Returns:
        Dictionary with token counts or None if not available
        (same shape as get_tokens_from_records)
    """
    raise NotImplementedError("Provider must implement get_batch_tokens")


def get_tokens_from_records(records: List[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    """
    Sum the token usage of already parsed batch records (parse_raw_content).

    Works without the API, so it is used in every get_OCR_results.py mode,
    including 'process_only'.

    Returns:
        Dictionary with token counts or None if the records carry no usage:
        {
            "input_tokens": int,
            "output_tokens": int,     # visible answer only, WITHOUT thinking
            "thinking_tokens": int,   # thinking / reasoning tokens
            "total_tokens": int       # input + output + thinking
        }
    """
    raise NotImplementedError("Provider must implement get_tokens_from_records")


def get_processing_time(batch_job: Any) -> Optional[float]:
    """
    Get processing time for a batch job in seconds.
    
    Note: Timestamp fields vary by provider:
    - Gemini: Uses create_time and end_time
    - OpenAI: Uses created_at and completed_at (timestamps)
    
    Args:
        batch_job: Batch job object from provider SDK
        
    Returns:
        Processing time in seconds or None if not available
    """
    raise NotImplementedError("Provider must implement get_processing_time")


def get_model_name(batch_job: Any) -> Optional[str]:
    """
    Get the model name used for the batch job.
    
    Note: Field name varies by provider:
    - Gemini: batch_job.model
    - OpenAI: batch_job.model
    
    Args:
        batch_job: Batch job object from provider SDK
        
    Returns:
        Model name or None if not available
    """
    raise NotImplementedError("Provider must implement get_model_name")


def get_batch_created_time(batch_job: Any) -> Optional[str]:
    """
    Get the creation time of the batch job in DD-MM-YYYY : HH-MM-SS format.
    
    Note: Timestamp fields vary by provider:
    - Gemini: batch_job.create_time (datetime)
    - OpenAI: batch_job.created_at (Unix timestamp)
    
    Args:
        batch_job: Batch job object from provider SDK
        
    Returns:
        Time string in DD-MM-YYYY : HH-MM-SS format or None if not available
    """
    raise NotImplementedError("Provider must implement get_batch_created_time")


def get_batch_completed_time(batch_job: Any) -> Optional[str]:
    """
    Get the completion time of the batch job in DD-MM-YYYY : HH-MM-SS format.
    
    Note: Timestamp fields vary by provider:
    - Gemini: batch_job.end_time (datetime)
    - OpenAI: batch_job.completed_at (Unix timestamp)
    
    Args:
        batch_job: Batch job object from provider SDK
        
    Returns:
        Time string in DD-MM-YYYY : HH-MM-SS format or None if not available
    """
    raise NotImplementedError("Provider must implement get_batch_completed_time")


def get_request_counts(batch_job: Any, api_key: str) -> Tuple[int, int]:
    """
    Get completed and failed request counts from batch job.
    
    Note: Field names and retrieval methods vary by provider:
    - Gemini: Requires REST API call to get batchStats from metadata
    - OpenAI: Uses request_counts field directly
    
    Args:
        batch_job: Batch job object from provider SDK
        api_key: API key for the provider (required for Gemini REST API)
        
    Returns:
        Tuple of (completed_count, failed_count); (None, None) when the counts
        cannot be read, so a failed lookup is not mistaken for zero
    """
    raise NotImplementedError("Provider must implement get_request_counts")


# ============================================================================
# ERROR HANDLING
# ============================================================================

def log_problem(
    batch_id: str,
    status: str,
    reason: str,
    problems_file: str,
    error_count: Optional[int] = None,
    error_custom_ids: Optional[List[str]] = None
):
    """
    Log a problem with a batch to the problems file.
    
    Args:
        batch_id: ID of the batch
        status: Status of the batch
        reason: Reason for the problem
        problems_file: Path to problems log file
        error_count: Number of errors (optional)
        error_custom_ids: List of error custom IDs (optional)
    """
    raise NotImplementedError("Provider must implement log_problem")


def is_error_record(record: Dict[str, Any]) -> bool:
    """
    Check if a single record has an error.
    
    Note: Error detection varies by provider:
    - Gemini: Check for "error" in record or response
    - OpenAI: Check for error in response structure
    
    Args:
        record: A single result record from parse_batch_results
        
    Returns:
        True if the record has an error, False otherwise
    """
    raise NotImplementedError("Provider must implement is_error_record")


def parse_error_records(all_records: List[Dict[str, Any]], store_error_custom_ids: bool) -> Tuple[int, List[str]]:
    """
    Parse error records from batch results.
    
    Note: Error detection varies by provider:
    - Gemini: Check for "error" in record or response
    - OpenAI: Check batch.error_file_id separately
    
    Args:
        all_records: All result records from parse_batch_results
        store_error_custom_ids: Whether to collect error custom IDs
        
    Returns:
        Tuple of (error_count, list_of_error_custom_ids)
    """
    raise NotImplementedError("Provider must implement parse_error_records")


# ============================================================================
# HELPER FUNCTIONS (Optional but recommended)
# ============================================================================

def normalize_batch_name(name: str) -> str:
    """
    Normalize batch name for consistent comparison.
    
    Args:
        name: Batch name to normalize
        
    Returns:
        Normalized batch name
    """
    raise NotImplementedError("Provider must implement normalize_batch_name")




def download_raw_batch(batch_job: Any, api_key: str) -> str:
    """
    Download raw data of a completed batch as a JSONL text string.
    
    Note: The implementation varies by provider:
    - Gemini: Downloads the single output file directly.
    - OpenAI: Combines successful (output_file_id) and failed (error_file_id) results.
    
    Args:
        batch_job: Batch job object from provider SDK
        api_key: API key for the provider
        
    Returns:
        Raw JSONL text string containing all records
    """
    raise NotImplementedError("Provider must implement download_raw_batch")


def parse_raw_content(raw_text: str) -> List[Dict[str, Any]]:
    """
    Parse raw JSONL text into a list of dictionary records.

    Args:
        raw_text: Raw JSONL text downloaded from API or loaded from disk

    Returns:
        List of parsed JSON dictionary records
    """
    raise NotImplementedError("Provider must implement parse_raw_content")


# ============================================================================
# CONTRACT CHECK
# ============================================================================
# Keys that provider_selector.get_provider() must return for every provider.
# The selector verifies this list at runtime, so a new provider that forgets
# a function is reported immediately with a readable message.
#
# When adding a function to the contract, add it here as well.
# ============================================================================

REQUIRED_PROVIDER_FUNCTIONS = [
    # Configuration and prompts
    "load_config",
    "get_image_adapter",
    "load_prompts",

    # Batch creation
    "make_jsonl_line",
    "get_request_custom_id",
    "extract_batch_page_ids",
    "build_jsonl_batches",
    "upload_image_to_provider",
    "upload_file_to_provider",
    "create_batch_job",

    # File operations
    "find_jsonl_files_in_output",
    "is_in_allowed_batch",
    "save_processed_batches",
    "load_processed_batches",

    # Batch retrieval
    "list_batches",
    "get_batch",
    "download_file",
    "parse_batch_results",
    "extract_text_from_record",
    "get_batch_status",
    "get_batch_id",
    "batch_created_after",
    "download_raw_batch",
    "parse_raw_content",

    # Batch metadata
    "get_batch_tokens",
    "get_tokens_from_records",
    "get_processing_time",
    "get_model_name",
    "get_batch_created_time",
    "get_batch_completed_time",
    "get_request_counts",

    # Error handling
    "log_problem",
    "is_error_record",
    "parse_error_records",

    # Helpers
    "normalize_batch_name",
]
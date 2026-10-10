"""
Validator Interface Contract
This file defines the interface that all validators must implement.
Validators receive raw OCR results and return enriched/validated JSON structures.
"""

from typing import Dict, Any, List, Tuple


def process_batch(
    all_raw_results: List[Dict[str, Any]],
    config: Dict[str, Any]
) -> List[Tuple[str, Any]]:
    """
    Process and enrich all raw OCR results from AI provider in a single call.
    
    This function should:
    - Extract text content from provider-specific format for all records
    - Parse and structure the data
    - Apply validation rules (including cross-record rules)
    - Enrich with external data (e.g., species lexicon)
    - Handle internal Bob functions (deduplication, backfill, etc.)
    - Return list of (filename, content) tuples for output files
    
    The validator receives complete control over:
    - Processing order of records
    - Maintaining state across records (carry_over, context)
    - Cross-record validation (sudden_change, backfill_species)
    - Internal Bob functions
    
    Args:
        all_raw_results: List of all raw result dicts from AI provider
        config: Configuration dictionary with validation rules and settings
        
    Returns:
        List of tuples where each tuple is (filename, content):
        - filename: Name of the output file (e.g., "results.jsonl", "review.jsonl", "review.csv")
        - content: Data to write (dict for JSONL, list for CSV lines, etc.)
        
    Special Error Reporting:
        Validators can return "problems.jsonl" as a filename to report errors.
        This is a standardized way to log parsing failures, validation errors, etc.
        The "problems.jsonl" output will be written to the problems file (problems_to_deal_with*.jsonl)
        in the state directory, keeping all errors in one centralized location.
        Error records should include: custom_id, batch_id, reason, and relevant details.
    """
    raise NotImplementedError("Validator must implement process_batch")

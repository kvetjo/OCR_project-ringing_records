"""
sum_raw_tokens.py -- token usage of a whole Gemini batch from its raw file.

Gemini reports usageMetadata per request, not per batch. This script sums it
over every request in a raw file (data_output/raw/*_raw.jsonl), so the totals
can be recovered even when the batch is no longer available via the API.

Read only: nothing is written, processed_batches.json is not touched.

Usage: edit RAW_FILES in the __main__ block, then
    python extra_tools/sum_raw_tokens.py

Works only with Gemini batches
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ai_providers.base import parse_raw_content
from ai_providers.gemini_provider import get_tokens_from_records, is_error_record


def sum_raw_file(raw_file: Path) -> None:
    """Print request count, error count and summed tokens of one raw file."""
    records = parse_raw_content(raw_file.read_text(encoding="utf-8"))
    error_count = sum(1 for record in records if is_error_record(record))
    tokens = get_tokens_from_records(records)

    print(f"\n{raw_file.name}")
    print(f"  requests: {len(records)}   errors: {error_count}")

    if tokens is None:
        print("  no usageMetadata in this file")
        return

    for key, value in tokens.items():
        print(f"  {key:<16} {value:>12,}".replace(",", " "))

    # Cross-check against what Gemini itself reports as the total
    reported_total = sum(
        int(((record.get("response") or {}).get("usageMetadata") or {}).get("totalTokenCount") or 0)
        for record in records
    )
    if reported_total != tokens["total_tokens"]:
        print(f"  [WARNING] sum of totalTokenCount is {reported_total}, not {tokens['total_tokens']}")


if __name__ == "__main__":
    raw_dir = PROJECT_ROOT / "data_output" / "raw"

    #GEMINI only! 
    #HERE YOU PUT WHAT YOU WANT TO ANALYZE! 
    RAW_FILES = [
        raw_dir / "s2re3y87ta6vhxtvh4k92bm9vujuarompdtu-ringing-subalpine_warblers-full_raw.jsonl",
    ]

    for raw_file in RAW_FILES:
        sum_raw_file(raw_file)

import os
import sys
import requests
from datetime import datetime, timezone
from pathlib import Path
from google import genai
import json

sys.path.insert(0, str(Path(__file__).parent))

from ai_providers.base import load_env_file

# ==========================================
# CONFIGURATION
# ==========================================
# The API key is read from the .env file (see .env.example), the same one the
# rest of the pipeline uses. Nothing secret is stored in this script.

load_env_file()
API_KEY = os.environ.get("GEMINI_API_KEY", "")

if not API_KEY:
    print("ERROR: GEMINI_API_KEY is not set. Add it to the .env file.")
    sys.exit(1)

# Initialize the Gemini client
client = genai.Client(api_key=API_KEY)

# ==========================================
# IMPLEMENTATION
# ==========================================

def get_filtered_batches(
    batch_id: str = None,
    limit: int = None,
    from_date: str = None
) -> list:
    """
    Retrieves and filters batch jobs from the Gemini API.

    Args:
        batch_id (str, optional): If provided, fetches only this specific batch.
        limit (int, optional): If provided, returns the last X batches.
        from_date (str, optional): If provided, returns batches created on or after this date. Format 'DD-MM-YYYY'.

    Returns:
        list: A list of batch objects matching the criteria.
    """

    # SCENARIO D: Fetch a specific batch by its ID
    if batch_id:
        # The API expects the format "batches/ID". Let's ensure it's formatted correctly.
        name = batch_id if batch_id.startswith("batches/") else f"batches/{batch_id}"
        try:
            batch = client.batches.get(name=name)
            return [batch]
        except Exception as e:
            print(f"Error fetching batch {batch_id}: {e}")
            return []

    # SCENARIOS A, B, C: Fetch all batches and apply filters
    all_batches = []

    try:
        # Retrieve all batches. The SDK handles pagination implicitly
        # when iterating over the returned pager object.
        batches_pager = client.batches.list()

        for batch in batches_pager:
            all_batches.append(batch)

    except Exception as e:
        print(f"Error listing batches: {e}")
        return []

    # Sort batches by create_time in descending order (newest first)
    all_batches.sort(key=lambda b: b.create_time, reverse=True)

    filtered_batches = all_batches

    # SCENARIO C: Filter by starting date
    if from_date:
        try:
            # Parse the string into a datetime object
            parsed_date = datetime.strptime(from_date, "%d-%m-%Y")

            # Make the parsed date timezone-aware (UTC) to match API response
            parsed_date = parsed_date.replace(tzinfo=timezone.utc)

            # Filter the list
            filtered_batches = [b for b in filtered_batches if b.create_time >= parsed_date]

        except ValueError:
            # Handle the case where the user inputs the wrong date format
            print(f"Error: Invalid date format '{from_date}'. Please use 'DD-MM-YYYY'.")
            return []

    # SCENARIO B: Limit the number of returned batches
    if limit is not None and limit > 0:
        filtered_batches = filtered_batches[:limit]

    # SCENARIO A: If no filters were applied, this returns all batches
    return filtered_batches



def get_token_counts_from_output(file_name, api_key):
    """
    Downloads the Batch output JSONL file and calculates token usage,
    including cached content tokens.
    """

    try:
        # Use the official Google GenAI SDK for downloading the file.
        client = genai.Client(api_key=api_key)

        file_content = client.files.download(file=file_name).decode("utf-8")

        total_cached = 0
        total_prompt = 0
        total_candidates = 0
        total_thoughts = 0
        request_count = 0
        cache_hit_requests = 0

        for line_number, line in enumerate(file_content.splitlines(), start=1):
            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  [JSON error on line {line_number}: {e}]")
                print(f"  [First 500 chars of problematic line: {line[:500]}]")
                continue

            response = record.get("response", {})

            usage = response.get("usageMetadata", {})

            prompt = int(usage.get("promptTokenCount", 0) or 0)
            cached = int(usage.get("cachedContentTokenCount", 0) or 0)
            candidates = int(usage.get("candidatesTokenCount", 0) or 0)
            thoughts = int(usage.get("thoughtsTokenCount", 0) or 0)

            total_prompt += prompt
            total_cached += cached
            total_candidates += candidates
            total_thoughts += thoughts

            request_count += 1

            if cached > 0:
                cache_hit_requests += 1

        return {
            "request_count": request_count,
            "total_prompt": total_prompt,
            "total_cached": total_cached,
            "total_candidates": total_candidates,
            "total_thoughts": total_thoughts,
            "cache_hit_requests": cache_hit_requests,
        }

    except Exception as e:
        print(f"  [Error while reading tokens: {e}]")
        return None


# ==========================================
# CONSOLE OUTPUT
# ==========================================

# ANSI colours for the status dot and the state
STATE_COLORS = {
    "JOB_STATE_SUCCEEDED": "\033[32m",  # green
    "JOB_STATE_PENDING": "\033[33m",    # yellow
    "JOB_STATE_RUNNING": "\033[33m",    # yellow
    "JOB_STATE_FAILED": "\033[31m",     # red
    "JOB_STATE_CANCELLED": "\033[31m",  # red
}


def color_by_state(text, state_name):
    color = STATE_COLORS.get(state_name)
    if color is None:
        return text
    return f"{color}{text}\033[0m"


def format_number(value):
    # 19608 -> '19 608'
    return f"{value:,}".replace(",", " ")


def get_progress_text(single_batch):
    # Workaround: Dotaz přímo na REST API kvůli chybějícím metadatům v SDK
    url = f"https://generativelanguage.googleapis.com/v1beta/{single_batch.name}"
    headers = {"x-goog-api-key": API_KEY}

    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()

            #Statistics
            metadata = data.get("metadata", {})
            stats = metadata.get("batchStats", {})

            total = stats.get("requestCount", "0")
            successful = stats.get("successfulRequestCount", "0")
            failed = stats.get("failedRequestCount", "0")

            return f"{successful}/{total} (Failed: {failed})"
        else:
            return f"Nedostupné (REST chyba {response.status_code})"
    except Exception as e:
        return f"Nedostupné (Chyba spojení: {e})"


def print_description_of_batches(single_batch, simple=True, fetch_stats_via_rest=True):
    """
    Vypíše detaily o batch jobu.
    Pokud fetch_stats_via_rest=True, zavolá REST API pro získání přesných počtů requestů.
    """
    if simple:
        #print(f"Name: {single_batch.display_name} | ID: {single_batch.name} | State: {single_batch.state.name} | Created: {single_batch.create_time.strftime('%Y-%m-%d %H:%M:%S')}")
        if fetch_stats_via_rest:
            print(f"Name: {single_batch.display_name} | ID: {single_batch.name} | State: {single_batch.state.name} | Created: {single_batch.create_time.strftime('%Y-%m-%d %H:%M:%S')} | Progress: {get_progress_text(single_batch)}")
        else:
            print(f"Name: {single_batch.display_name} | ID: {single_batch.name} | State: {single_batch.state.name} | Created: {single_batch.create_time.strftime('%Y-%m-%d %H:%M:%S')}")
 
    else:
        state = single_batch.state.name

        print("=" * 60)
        print(f"{color_by_state('●', state)} {single_batch.display_name}")
        print("=" * 60)
        print(f"  ID         {single_batch.name}")
        print(f"  Model      {single_batch.model.removeprefix('models/')}")
        print(f"  State      {color_by_state(state, state)}")
        print(f"  Created    {single_batch.create_time.strftime('%Y-%m-%d %H:%M:%S')}")
        if fetch_stats_via_rest:
            print(f"  Progress   {get_progress_text(single_batch)}")

        if state == "JOB_STATE_SUCCEEDED" and single_batch.dest and single_batch.dest.file_name:
            res = get_token_counts_from_output(single_batch.dest.file_name, API_KEY)
            if res:
                print()
                print("  Tokens")
                print("  " + "-" * 30)
                print(f"  Requests    {format_number(res['request_count']):>10}")
                print(f"  Prompt      {format_number(res['total_prompt']):>10}")
                print(f"  Cached      {format_number(res['total_cached']):>10}   (cache hits: {res['cache_hit_requests']}/{res['request_count']})")
                print(f"  Candidates  {format_number(res['total_candidates']):>10}")
                print(f"  Thoughts    {format_number(res['total_thoughts']):>10}")
        print()


# ==========================================
# USAGE EXAMPLES
# ==========================================
if __name__ == "__main__":

    # print("--- SCENARIO A: ALL BATCHES (WITH REST API STATS) ---")
    # # Získání všech batchů
    # all_jobs = get_filtered_batches(limit=3) # Limit pro ukázku, aby se nevolalo API zbytečně mockrát
    # for b in all_jobs:
    #     # Zde jsme zapnuli rozšířený výpis a stahování statistik přes REST API
    #     print_description_of_batches(b, simple=False, fetch_stats_via_rest=True)


    # # print("\n--- SCENARIO B: LAST 2 BATCHES (SIMPLE PRINT) ---")
    # last_two = get_filtered_batches(limit=2)
    # for b in last_two:
    #     print_description_of_batches(b, simple=True)

    print("\n--- SCENARIO C: BATCHES FROM THE GIVEN DATE ---")
    #Date format: DD-MM-YYYY
    march_batches = get_filtered_batches(from_date="01-10-2026")
    for b in march_batches:
        print_description_of_batches(b, simple=False)

    # print("\n--- SCENARIO D: SPECIFIC BATCH ---")
    # specific_job = get_filtered_batches(batch_id="q5vpseewkhyjdpspcpl0syziaf06vhq7b1ct")
    # if specific_job:
    #     print_description_of_batches(specific_job[0], simple=False)
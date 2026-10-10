"""
post_ai_eval.py

Sends a JSONL dataset to the OpenAI Responses API with the Code Interpreter tool
(the "python tool" - same as uploading a file into ChatGPT) and lets the model look
for silent errors according to PROMPT_RINGING_POST_AI_EVAL in prompts.yaml.
The model writes silent_errors.jsonl into its container; this script downloads it.

Standalone:
    python post_ai_eval.py                      # uses INPUT_JSONL_PATH below

From main.py:
    from post_ai_eval import run_post_ai_eval
    result = run_post_ai_eval(input_path="data.jsonl")
    result = run_post_ai_eval(input_jsonl=jsonl_text)       # str with JSONL
    result = run_post_ai_eval(input_jsonl=list_of_dicts)    # list of records
    result["output_records"]  -> list of dicts from silent_errors.jsonl

Requires: pip install openai pyyaml ; OPENAI_API_KEY set in environment.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path, PurePosixPath

import yaml
from openai import OpenAI
from dotenv import load_dotenv


# ============================================================
# CONFIG
# ============================================================

MODEL = "gpt-6-astra"
REASONING_EFFORT = "max"            # low | medium | high | xhigh | max
CONTAINER_MEMORY_LIMIT = "1g"       # 1g (default) | 4g | 16g | 64g - RAM of the Python sandbox

PROMPTS_PATH = Path(__file__).resolve().parent / "prompts.yaml"
PROMPT_KEY = "PROMPT_RINGING_POST_AI_EVAL"

# Input for standalone run (main.py passes input_path / input_jsonl directly)
INPUT_JSONL_PATH = r"C:\Birdlife_projects\OCR_universal_project\data_output\output\ringing-subalpine_warblers-full\results-ringing-subalpine_warblers-full.jsonl"
UPLOAD_FILENAME = "input_data.jsonl"        # file name inside the container

# Output
SAVE_OUTPUT_TO_DISK = True                  # True = save to OUTPUT_DIR, False = return in variable only
OUTPUT_DIR = Path(__file__).resolve().parent / "output_data_post_eval"
OUTPUT_FILENAME = "silent_errors.jsonl"     # file the model creates (as defined in the prompt)

# Appended to user_prompt so the model knows where the input is and where to save the output
PROMPT_FILE_NOTE = (
    "\n\nInput dataset file: {input_path}\n"
    "Save the output file as /mnt/data/{output_filename}"
)

POLL_INTERVAL_SEC = 15                      # how often to check the running response
HEARTBEAT_SEC = 300                         # log "still running" at most this often

# ============================================================
load_dotenv()
logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "incomplete"}


def load_prompts(prompts_path=None, prompt_key=None):
    """Return (system_prompt, user_prompt) from prompts.yaml (defaults from CONFIG)."""
    prompts_path = prompts_path or PROMPTS_PATH
    prompt_key = prompt_key or PROMPT_KEY
    with open(prompts_path, encoding="utf-8") as f:
        prompts = yaml.safe_load(f) or {}

    block = prompts.get(prompt_key) or {}
    system_prompt = (block.get("system_prompt") or "").strip()
    user_prompt = (block.get("user_prompt") or "").strip()

    if not system_prompt or not user_prompt:
        raise ValueError(f"{prompt_key}: system_prompt or user_prompt is empty in {prompts_path}")
    return system_prompt, user_prompt


def parse_jsonl(text, source_name):
    """Parse JSONL text into a list of dicts. Invalid lines are skipped and logged."""
    records, invalid_lines = [], []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            invalid_lines.append(line_no)

    if invalid_lines:
        logger.warning("%s: %d invalid JSON line(s), first: %s",
                       source_name, len(invalid_lines), invalid_lines[:10])
    return records


def prepare_input(input_path=None, input_jsonl=None):
    """
    Return (jsonl_text, record_count).
    Exactly one of input_path / input_jsonl must be given.
    input_jsonl can be JSONL text (str) or a list of dicts.
    """
    if (input_path is None) == (input_jsonl is None):
        raise ValueError("Provide exactly one of input_path / input_jsonl")

    if input_path is not None:
        jsonl_text = Path(input_path).read_text(encoding="utf-8")
        source_name = str(input_path)
    elif isinstance(input_jsonl, str):
        jsonl_text = input_jsonl
        source_name = "input_jsonl (str)"
    else:
        jsonl_text = "\n".join(json.dumps(r, ensure_ascii=False) for r in input_jsonl) + "\n"
        source_name = "input_jsonl (list)"

    record_count = len(parse_jsonl(jsonl_text, source_name))
    if record_count == 0:
        raise ValueError(f"{source_name}: no JSON records found")

    logger.info("Input: %s - %d JSON records", source_name, record_count)
    return jsonl_text, record_count


def create_container(client):
    container = client.containers.create(
        name=f"post_ai_eval_{datetime.now():%Y%m%d_%H%M%S}",
        memory_limit=CONTAINER_MEMORY_LIMIT,
    )
    logger.info("Container created: %s (memory %s)", container.id, CONTAINER_MEMORY_LIMIT)
    return container.id


def upload_input(client, container_id, jsonl_text):
    """Upload the dataset into the container; return its path inside the container."""
    cfile = client.containers.files.create(
        container_id,
        file=(UPLOAD_FILENAME, jsonl_text.encode("utf-8")),
    )
    logger.info("Input uploaded to container: %s", cfile.path)
    return cfile.path


def run_response(client, container_id, system_prompt, user_prompt):
    """Start the response in background mode and poll until it finishes."""
    response = client.responses.create(
        model=MODEL,
        instructions=system_prompt,
        input=user_prompt,
        tools=[{"type": "code_interpreter", "container": container_id}],
        tool_choice="auto",
        reasoning={"effort": REASONING_EFFORT},
        background=True,
    )
    logger.info("Response started: %s (model %s, reasoning %s)", response.id, MODEL, REASONING_EFFORT)

    start = time.time()
    last_status, last_log = None, start
    while response.status not in TERMINAL_STATUSES:
        now = time.time()
        if response.status != last_status or now - last_log >= HEARTBEAT_SEC:
            logger.info("Status: %s (%.1f min)", response.status, (now - start) / 60)
            last_status, last_log = response.status, now
        time.sleep(POLL_INTERVAL_SEC)
        response = client.responses.retrieve(response.id)

    logger.info("Response finished: %s after %.1f min", response.status, (time.time() - start) / 60)
    if response.status != "completed":
        logger.warning("error=%s | incomplete_details=%s", response.error, response.incomplete_details)
    return response


def log_usage(response):
    """Log and return token usage of the response."""
    usage = response.usage
    if usage is None:
        logger.warning("Token usage not available")
        return {}

    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    stats = {
        "input_tokens": usage.input_tokens,
        "cached_tokens": getattr(input_details, "cached_tokens", 0) or 0,
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": getattr(output_details, "reasoning_tokens", 0) or 0,
        "total_tokens": usage.total_tokens,
    }
    logger.info("Tokens: input %d (cached %d) | output %d (reasoning %d) | total %d",
                stats["input_tokens"], stats["cached_tokens"], stats["output_tokens"],
                stats["reasoning_tokens"], stats["total_tokens"])
    return stats


def download_output(client, container_id):
    """Return content of OUTPUT_FILENAME from the container, or None if it is not there."""
    files = list(client.containers.files.list(container_id, order="desc", limit=100))

    for cfile in files:
        if PurePosixPath(cfile.path).name == OUTPUT_FILENAME:
            logger.info("Output found in container: %s (%s B)", cfile.path, cfile.bytes)
            content = client.containers.files.content.retrieve(cfile.id, container_id=container_id)
            return content.read().decode("utf-8")

    logger.error("%s not found in container. Files present: %s",
                 OUTPUT_FILENAME, [f.path for f in files])
    return None


def save_output(output_text, complete=True):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "" if complete else "_INCOMPLETE"
    output_path = OUTPUT_DIR / f"{Path(OUTPUT_FILENAME).stem}_{datetime.now():%Y%m%d_%H%M%S}{suffix}.jsonl"
    output_path.write_text(output_text, encoding="utf-8")
    logger.info("Output saved: %s", output_path)
    return output_path




def run_post_ai_eval(input_path=None, input_jsonl=None, save_to_disk=None):
    """
    Main entry point (standalone and main.py).

    input_path   - path to input JSONL file
    input_jsonl  - JSONL text (str) or list of dicts (use instead of input_path)
    save_to_disk - None = use SAVE_OUTPUT_TO_DISK from config

    Returns dict:
        output_jsonl    - content of silent_errors.jsonl (str)
        output_records  - parsed records (list of dicts)
        output_path     - Path of saved file, or None
        input_count, output_count, usage, response_id, container_id
    """
    if save_to_disk is None:
        save_to_disk = SAVE_OUTPUT_TO_DISK

    system_prompt, user_prompt = load_prompts()
    jsonl_text, input_count = prepare_input(input_path, input_jsonl)

    client = OpenAI()
    container_id = create_container(client)
    container_input_path = upload_input(client, container_id, jsonl_text)

    user_prompt += PROMPT_FILE_NOTE.format(input_path=container_input_path,
                                           output_filename=OUTPUT_FILENAME)

    response = run_response(client, container_id, system_prompt, user_prompt)
    usage = log_usage(response)

    output_text = download_output(client, container_id)
    if output_text is None:
        logger.error("Model's final message:\n%s", response.output_text)
        raise FileNotFoundError(
            f"{OUTPUT_FILENAME} was not created (response {response.id}, status {response.status})"
        )

    output_records = parse_jsonl(output_text, OUTPUT_FILENAME)
    logger.info("Records: input %d | output %d", input_count, len(output_records))

    if response.status == "completed":
        complete = True
    else:
        complete = False

    if not complete:
        logger.warning("Response ended with status '%s' - output may be incomplete", response.status)

    output_path = save_output(output_text, complete) if save_to_disk else None

    return {
        "output_jsonl": output_text,
        "output_records": output_records,
        "output_path": output_path,
        "input_count": input_count,
        "output_count": len(output_records),
        "usage": usage,
        "response_id": response.id,
        "container_id": container_id,
        "response_status": response.status
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_post_ai_eval(input_path=INPUT_JSONL_PATH)


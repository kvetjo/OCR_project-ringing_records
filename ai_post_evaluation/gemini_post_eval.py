"""
gemini_post_eval.py

Gemini counterpart of openai_post_eval.py. Sends a JSONL dataset to the Gemini
Interactions API with the Code Execution tool (Python sandbox - Gemini's equivalent
of OpenAI's Code Interpreter) and lets the model look for silent errors according to
PROMPT_RINGING_POST_AI_EVAL in prompts.yaml.

Same input and output as the OpenAI version. Differences are on the API side only:
  - The dataset is uploaded through the Files API and attached to the request,
    optionally converted to CSV first by convert_jsonl_to_csv() from the existing
    converter script (UPLOAD_AS_CSV; the converter itself is used unchanged).
    The prompt in prompts.yaml must describe the uploaded format (see PROMPT_KEY).
    Gemini puts the file into the code sandbox AND into the model's context, so the
    whole file counts as input tokens and must fit into the context window.
  - Gemini's sandbox has no downloadable files (no container files API), so the
    output is taken from the jsonl code block of the model's final message
    (the prompt asks for it).

Standalone:
    python gemini_post_eval.py                  # uses INPUT_JSONL_PATH below

From main.py:
    from gemini_post_eval import run_post_ai_eval
    result = run_post_ai_eval(input_path="data.jsonl")
    result = run_post_ai_eval(input_jsonl=jsonl_text)       # str with JSONL
    result = run_post_ai_eval(input_jsonl=list_of_dicts)    # list of records
    result["output_records"]  -> list of dicts from silent_errors.jsonl

Requires: pip install -U "google-genai>=2.3.0" pyyaml python-dotenv
          GEMINI_API_KEY in .env
"""

import io
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv
from google import genai
from google.genai import types


# ============================================================
# CONFIG
# ============================================================

MODEL = "gemini-3.1-pro-preview"          # gemini-3.8-flash | gemini-3.1-pro-preview | ... (new model = new ID here)
THINKING_LEVEL = "high"             # low | medium | high  (3.8 Flash default is medium, "minimal" not supported)
API_KEY_ENV = "GEMINI_API_KEY"      # variable in .env with the API key

# Upload format + the prompt that describes it (the file structure lives in the prompt, not in this script)
UPLOAD_AS_CSV = True                        # True = JSONL -> CSV with the existing converter, False = upload JSONL as is
PROMPTS_PATH = Path(__file__).resolve().parent / "prompts.yaml"
PROMPT_KEY = "PROMPT_RINGING_POST_AI_EVAL_GEMINI" if UPLOAD_AS_CSV else "PROMPT_RINGING_POST_AI_EVAL"
CSV_CONVERTER_PATH = r"C:\Birdlife_projects\OCR_universal_project\extra_tools\json2csv_v4.py"  # file with convert_jsonl_to_csv()

# Input for standalone run (main.py passes input_path / input_jsonl directly)
INPUT_JSONL_PATH = r"C:\Birdlife_projects\OCR_universal_project\data_output\output\ringing-subalpine_warblers-full\results-ringing-subalpine_warblers-full.jsonl"
UPLOAD_FILENAME = "input_data"              # display name in the Files API (.csv / .jsonl is added)
UPLOAD_MIME_TYPE = "text/csv"               # Interactions API documents list application/pdf and text/csv;
                                            # with UPLOAD_AS_CSV = False the content stays JSONL (the prompt says so)

# Output
SAVE_OUTPUT_TO_DISK = True                  # True = save to OUTPUT_DIR, False = return in variable only
OUTPUT_DIR = Path(__file__).resolve().parent / "output_data_post_eval"
OUTPUT_FILENAME = "silent_errors.jsonl"     # name of the saved output (model + timestamp are added)

POLL_INTERVAL_SEC = 15                      # how often to check the running interaction
HEARTBEAT_SEC = 300                         # log "still running" at most this often

RETRY_ATTEMPTS = 5                          # SDK retries of API calls on 408/409/429/5xx (upload, start, polling)
RETRY_INITIAL_DELAY_SEC = 5
RETRY_MAX_DELAY_SEC = 60

# ============================================================
load_dotenv()
logger = logging.getLogger(__name__)

RUNNING_STATUSES = {"in_progress", "queued"}    # anything else is final: completed | failed | cancelled | incomplete | ...


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


def convert_to_csv(jsonl_text):
    """JSONL text -> CSV bytes via convert_jsonl_to_csv() from CSV_CONVERTER_PATH (unchanged, no review file)."""
    import importlib.util
    import tempfile

    spec = importlib.util.spec_from_file_location("jsonl_csv_converter", CSV_CONVERTER_PATH)
    converter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(converter)

    # The converter works with file paths -> round trip through a temp folder (covers input_jsonl from main.py too)
    with tempfile.TemporaryDirectory() as tmp_dir:
        jsonl_path = Path(tmp_dir) / "input.jsonl"
        csv_path = Path(tmp_dir) / "input.csv"
        jsonl_path.write_text(jsonl_text, encoding="utf-8")
        converter.convert_jsonl_to_csv(input_filepath=jsonl_path, output_filepath=csv_path, data_type="ringing")
        csv_bytes = csv_path.read_bytes()

    logger.info("Converted to CSV: %d B (JSONL %d B)", len(csv_bytes), len(jsonl_text.encode("utf-8")))
    return csv_bytes


def create_client():
    """Gemini client - key from API_KEY_ENV (.env), backoff retries for rate limits / server errors."""
    return genai.Client(
        api_key=os.getenv(API_KEY_ENV),     # None -> SDK falls back to GOOGLE_API_KEY / GEMINI_API_KEY
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(
                attempts=RETRY_ATTEMPTS,
                initial_delay=RETRY_INITIAL_DELAY_SEC,
                max_delay=RETRY_MAX_DELAY_SEC,
            )
        ),
    )


def upload_input(client, data, display_name):
    """Upload the dataset (bytes) through the Files API (kept 48 h, then deleted automatically); return the File."""
    uploaded = client.files.upload(
        file=io.BytesIO(data),
        config=types.UploadFileConfig(mime_type=UPLOAD_MIME_TYPE, display_name=display_name),
    )
    while uploaded.state == types.FileState.PROCESSING:
        time.sleep(2)
        uploaded = client.files.get(name=uploaded.name)
    if uploaded.state == types.FileState.FAILED:
        raise RuntimeError(f"Upload of {display_name} failed: {uploaded.error}")

    logger.info("Input uploaded: %s (%s B, %s)", uploaded.name, uploaded.size_bytes, uploaded.mime_type)
    return uploaded


def run_interaction(client, uploaded_file, system_prompt, user_prompt):
    """Start the interaction in background mode and poll until it finishes."""
    interaction = client.interactions.create(
        model=MODEL,
        system_instruction=system_prompt,
        input=[
            {"type": "document", "uri": uploaded_file.uri, "mime_type": uploaded_file.mime_type},
            {"type": "text", "text": user_prompt},
        ],
        tools=[{"type": "code_execution"}],
        generation_config={"thinking_level": THINKING_LEVEL},
        background=True,
    )
    logger.info("Interaction started: %s (model %s, thinking %s)", interaction.id, MODEL, THINKING_LEVEL)

    start = time.time()
    last_status, last_log = None, start
    while interaction.status in RUNNING_STATUSES:
        now = time.time()
        if interaction.status != last_status or now - last_log >= HEARTBEAT_SEC:
            logger.info("Status: %s (%.1f min)", interaction.status, (now - start) / 60)
            last_status, last_log = interaction.status, now
        time.sleep(POLL_INTERVAL_SEC)
        interaction = client.interactions.get(id=interaction.id)

    logger.info("Interaction finished: %s after %.1f min", interaction.status, (time.time() - start) / 60)
    if interaction.status != "completed":
        logger.warning("errors=%s", interaction.errors)
    return interaction


def log_usage(interaction):
    """Log and return token usage (same keys as the OpenAI version + tool_use_tokens)."""
    usage = interaction.usage
    if usage is None:
        logger.warning("Token usage not available")
        return {}

    thought_tokens = usage.total_thought_tokens or 0
    stats = {
        "input_tokens": usage.total_input_tokens or 0,
        "cached_tokens": usage.total_cached_tokens or 0,
        "tool_use_tokens": usage.total_tool_use_tokens or 0,
        # Gemini counts thinking separately; added here so output_tokens means the same as at OpenAI
        "output_tokens": (usage.total_output_tokens or 0) + thought_tokens,
        "reasoning_tokens": thought_tokens,
        "total_tokens": usage.total_tokens or 0,
    }
    logger.info("Tokens: input %d (cached %d, tool use %d) | output %d (reasoning %d) | total %d",
                stats["input_tokens"], stats["cached_tokens"], stats["tool_use_tokens"],
                stats["output_tokens"], stats["reasoning_tokens"], stats["total_tokens"])
    return stats


def log_code_execution(interaction):
    """Log how often the model ran code - the data is also in its context, so it could skip the sandbox."""
    steps = interaction.steps or []
    runs = sum(step.type == "code_execution_call" for step in steps)
    errors = sum(step.type == "code_execution_result" and bool(step.is_error) for step in steps)
    logger.info("Code executions: %d (with error: %d)", runs, errors)
    if runs == 0:
        logger.warning("The model did not use code execution")


def extract_jsonl_block(text):
    """Last ```jsonl / ```json / ``` block in text -> (content, closed); (None, False) if there is none."""
    blocks, lines, lang = [], None, None
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if lines is None:                           # opening fence
                lang, lines = line.strip()[3:].strip().lower(), []
            else:                                       # closing fence
                blocks.append((lang, lines, True))
                lines = None
        elif lines is not None:
            lines.append(line.strip())
    if lines is not None:
        blocks.append((lang, lines, False))             # never closed - the answer was cut off

    blocks = [block for block in blocks if block[0] in ("jsonl", "json", "ndjson", "")]
    if not blocks:
        return None, False
    _, lines, closed = blocks[-1]
    lines = [line for line in lines if line]
    return ("\n".join(lines) + "\n" if lines else ""), closed


def extract_output(interaction):
    """Output lines from the jsonl code block of the final message (falls back to earlier model texts)."""
    texts = [interaction.output_text or ""] + [
        "".join(c.text or "" for c in step.content or [] if c.type == "text")
        for step in reversed(interaction.steps or []) if step.type == "model_output"
    ]
    for text in texts:
        content, closed = extract_jsonl_block(text)
        if content is not None:
            return content, closed
    return None, False


def save_output(output_text, complete=True):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "" if complete else "_INCOMPLETE"
    # model in the file name so runs of different models / providers don't mix in OUTPUT_DIR
    output_path = OUTPUT_DIR / f"{Path(OUTPUT_FILENAME).stem}_{MODEL}_{datetime.now():%Y%m%d_%H%M%S}{suffix}.jsonl"
    output_path.write_text(output_text, encoding="utf-8")
    logger.info("Output saved: %s", output_path)
    return output_path


def run_post_ai_eval(input_path=None, input_jsonl=None, save_to_disk=None):
    """
    Main entry point (standalone and main.py) - same interface as the OpenAI version.

    input_path   - path to input JSONL file
    input_jsonl  - JSONL text (str) or list of dicts (use instead of input_path)
    save_to_disk - None = use SAVE_OUTPUT_TO_DISK from config

    Returns dict:
        output_jsonl    - content of silent_errors.jsonl (str)
        output_records  - parsed records (list of dicts)
        output_path     - Path of saved file, or None
        input_count, output_count, usage, response_id, container_id, response_status
        (response_id = Gemini interaction ID; container_id = None, Gemini has no container)
    """
    if save_to_disk is None:
        save_to_disk = SAVE_OUTPUT_TO_DISK

    system_prompt, user_prompt = load_prompts()
    jsonl_text, input_count = prepare_input(input_path, input_jsonl)

    if UPLOAD_AS_CSV:
        upload_data, upload_name = convert_to_csv(jsonl_text), f"{UPLOAD_FILENAME}.csv"
    else:
        upload_data, upload_name = jsonl_text.encode("utf-8"), f"{UPLOAD_FILENAME}.jsonl"

    client = create_client()
    uploaded_file = upload_input(client, upload_data, upload_name)

    interaction = run_interaction(client, uploaded_file, system_prompt, user_prompt)
    usage = log_usage(interaction)
    log_code_execution(interaction)

    output_text, block_closed = extract_output(interaction)
    if output_text is None:
        logger.error("Model's final message:\n%s", interaction.output_text)
        # same exception as the OpenAI version, so main.py can handle both the same way
        raise FileNotFoundError(
            f"No jsonl code block with {OUTPUT_FILENAME} lines in the model's answer "
            f"(interaction {interaction.id}, status {interaction.status})"
        )

    output_records = parse_jsonl(output_text, OUTPUT_FILENAME)
    logger.info("Records: input %d | output %d", input_count, len(output_records))

    complete = interaction.status == "completed" and block_closed
    if not complete:
        logger.warning("Interaction ended with status '%s'%s - output may be incomplete",
                       interaction.status, "" if block_closed else " and the code block is not closed")

    output_path = save_output(output_text, complete) if save_to_disk else None

    return {
        "output_jsonl": output_text,
        "output_records": output_records,
        "output_path": output_path,
        "input_count": input_count,
        "output_count": len(output_records),
        "usage": usage,
        "response_id": interaction.id,
        "container_id": None,
        "response_status": interaction.status,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_post_ai_eval(input_path=INPUT_JSONL_PATH)
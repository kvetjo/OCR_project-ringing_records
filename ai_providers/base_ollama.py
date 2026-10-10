"""
Ollama Provider Base
====================
Everything Ollama needs that is NOT specific to one model.

Ollama has no batch API. Instead of uploading a JSONL file and waiting for a
remote job, make_OCR_batch.py sends the images one by one to an Ollama server
and writes the finished "batch" straight into directories.raw_dir_base. From
there get_OCR_results.py reads it with its existing mode='process_only' path,
so the validation half of the pipeline does not change at all.

Two connection modes, chosen in config.yaml by run_settings.active_provider:
    "ollama"      - the server runs on this machine
    "ollama-ssh"  - the server runs on a remote host, reached through an SSH
                    tunnel opened with paramiko

Everything configurable lives in config-ollama.yaml (connection, model ->
provider mapping, defaults). Everything specific to ONE model lives in that
model's own module in ai_providers/, for example glm_ocr_provider.py: its
prompt, its Ollama options and postprocess_output(), which turns the model's
raw answer into the JSON string the validator expects.

A model without such a module still runs - its answer is written to the raw
file unchanged and a [WARN] explains what is missing.
"""

import importlib
import json
import select
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
import yaml

# Shared, provider-independent helpers.
from ai_providers.base import (
    PROJECT_ROOT,
    build_custom_id,
    build_raw_file_path,
    extract_batch_page_ids,
    find_jsonl_files_in_output,
    is_in_allowed_batch,
    load_config,
    load_processed_batches,
    load_prompts,
    log_problem,
    normalize_batch_name,
    parse_raw_content,
    save_processed_batches,
)
from ai_providers.base import parse_error_records as base_parse_error_records

try:
    import paramiko
except ImportError:
    paramiko = None


# ============================================================================
# CONSTANTS
# ============================================================================

# Provider names in config.yaml that are served by this module.
OLLAMA_PROVIDER_NAMES = ("ollama", "ollama-ssh")

# The one that goes through an SSH tunnel.
SSH_PROVIDER_NAME = "ollama-ssh"

DEFAULT_OLLAMA_CONFIG_PATH = PROJECT_ROOT / "config-ollama.yaml"

# Used when config-ollama.yaml has no 'local' section.
DEFAULT_LOCAL_CONNECTION = {"scheme": "http", "host": "localhost", "port": 11434}

DEFAULT_BATCH_ID_PREFIX = "ollama"

# Last line of defence: used when a setting is neither in the model provider
# module nor in config-ollama.yaml -> defaults.
HARD_DEFAULTS: Dict[str, Any] = {
    "ocr_prompt": None,
    "ollama_options": {},
    "ollama_format": None,
    "keep_alive": "5m",
    "request_timeout": 600,
    "max_retries": 2,
    "retry_delay": 5,
    "check_vision_capability": True,
}

# Settings a model provider module may override, as MODULE-LEVEL CONSTANTS in
# upper case (OCR_PROMPT, OLLAMA_OPTIONS, ...). A constant that is missing or
# set to None falls through to config-ollama.yaml.
OVERRIDABLE_SETTINGS = tuple(HARD_DEFAULTS.keys())

# Modules in ai_providers/ that are never a model provider.
NON_MODEL_PROVIDER_MODULES = {
    "base",
    "base_ollama",
    "gemini_provider",
    "openai_provider",
    "ollama",
    "provider_interface",
    "provider_selector",
}

TUNNEL_BUFFER = 32768


# ============================================================================
# MODEL PROVIDER DEFAULTS
# ============================================================================
# These are the values used when a model has no module of its own. A model
# provider module overrides them simply by defining a constant / function with
# the same name.

DEFAULT_OCR_PROMPT = None
DEFAULT_OLLAMA_OPTIONS = None


def postprocess_output(raw_text: str, config: Dict[str, Any]) -> str:
    """
    Turn the raw answer of the model into the text handed to the validator.

    The default is the identity: whatever the model said is written to the raw
    file unchanged. A model provider module overrides this with its own
    postprocess_output(raw_text, config) - that is where an HTML table gets
    converted into the JSON array the validator expects.
    """
    return raw_text


# ============================================================================
# CONFIG FILE
# ============================================================================

def load_ollama_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Read config-ollama.yaml.

    Loaded lazily (only by run_local_batch), so get_OCR_results.py can use this
    provider without the file being present.
    """
    path = Path(config_path) if config_path else DEFAULT_OLLAMA_CONFIG_PATH

    if not path.exists():
        print(f"ERROR: Ollama config file '{path}' does not exist.")
        print("ERROR: It holds the connection settings and the model -> provider mapping.")
        print("ERROR: Create it next to config.yaml (see docs/CONFIGURATION.md).")
        sys.exit(1)

    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except Exception as exc:
        print(f"ERROR: Failed to read '{path}': {exc}")
        sys.exit(1)


def build_settings(
    model_provider: Optional[Any],
    ollama_config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Resolve every request setting, strongest source first:

        1. constant in the model provider module   (OCR_PROMPT, OLLAMA_OPTIONS, ...)
        2. config-ollama.yaml -> defaults
        3. HARD_DEFAULTS

    A source that has the value as None is treated as "not set" and the next
    one is used. False is a real value and wins.
    """
    defaults = ollama_config.get("defaults") or {}
    settings: Dict[str, Any] = {}

    for name in OVERRIDABLE_SETTINGS:
        value = getattr(model_provider, name.upper(), None) if model_provider else None

        if value is None:
            value = defaults.get(name)

        if value is None:
            value = HARD_DEFAULTS.get(name)

        settings[name] = value

    return settings


# ============================================================================
# MODEL PROVIDER LOOKUP
# ============================================================================

def list_model_provider_modules() -> List[str]:
    """Module names in ai_providers/ that can be used as a model provider."""
    package_dir = Path(__file__).resolve().parent

    return sorted(
        module_path.stem
        for module_path in package_dir.glob("*.py")
        if module_path.stem not in NON_MODEL_PROVIDER_MODULES
        and not module_path.stem.startswith("_")
    )


def resolve_model_provider(
    model_name: str,
    installed_name: Optional[str],
    ollama_config: Dict[str, Any],
    installed_models: Optional[List[str]] = None
) -> Optional[Any]:
    """
    Find the module that knows how to talk to this model.

    The mapping is explicit - config-ollama.yaml -> model_providers. Nothing is
    guessed from the model name. It is looked up first under the name written
    in config.yaml, then under the name Ollama itself reports, so 'glm-ocr'
    also matches an entry written as 'glm-ocr:latest'.

    Returns the imported module, or None when the model has no entry - the run
    then continues with the defaults of this file.
    """
    mapping = ollama_config.get("model_providers") or {}

    module_name = None
    for key in (model_name, installed_name):
        if key and key in mapping:
            module_name = mapping[key]
            break

    if not module_name:
        available_modules = list_model_provider_modules()

        print(f"[WARN] Model '{model_name}' has no entry in config-ollama.yaml -> model_providers.")
        print("[WARN] Its output will NOT be postprocessed - base_ollama.py defaults are used.")
        print(f"[WARN]   model_name in config.yaml  : {model_name}")
        print(f"[WARN]   name reported by Ollama    : {installed_name or '-'}")
        print(f"[WARN]   models installed in Ollama : {', '.join(sorted(installed_models or [])) or '-'}")
        print(f"[WARN]   model_providers mapping    : {mapping or '{}'}")
        print(f"[WARN]   modules in ai_providers/   : {', '.join(available_modules) or '-'}")
        print("[WARN] Add a line to model_providers to use one of them.")

        return None

    try:
        return importlib.import_module(f"ai_providers.{module_name}")
    except ImportError as exc:
        print(f"ERROR: config-ollama.yaml maps model '{model_name}' to module '{module_name}',")
        print(f"ERROR: but it cannot be imported: {exc}")
        print(f"ERROR: Modules available in ai_providers/: {', '.join(list_model_provider_modules()) or '-'}")
        sys.exit(1)


def apply_postprocess(
    model_provider: Optional[Any],
    raw_text: str,
    config: Dict[str, Any]
) -> Tuple[str, Optional[str]]:
    """
    Run the model provider's postprocess_output on one answer.

    Returns (text, error_message). A postprocessor that raises never costs the
    answer: the untouched text is used and the error is recorded next to it, so
    it can be fixed and the raw file reprocessed instead of re-running the OCR.
    """
    postprocess_fn = getattr(model_provider, "postprocess_output", None) if model_provider else None

    if postprocess_fn is None:
        postprocess_fn = postprocess_output

    try:
        return postprocess_fn(raw_text, config), None
    except Exception as exc:
        module_name = getattr(model_provider, "__name__", "base_ollama")
        print(f"[WARN] postprocess_output() in {module_name} failed: {exc}")
        print("[WARN] The untouched model output is used instead.")

        return raw_text, str(exc)


# ============================================================================
# SSH TUNNEL (local port -> remote Ollama port)
# ============================================================================

def _pump_socket_channel(client_socket, channel) -> None:
    """Copy data both ways between a local socket and an SSH channel."""
    try:
        while True:
            readable, _, _ = select.select([client_socket, channel], [], [], 1.0)

            if client_socket in readable:
                data = client_socket.recv(TUNNEL_BUFFER)
                if not data:
                    break
                channel.sendall(data)

            if channel in readable:
                data = channel.recv(TUNNEL_BUFFER)
                if not data:
                    break
                client_socket.sendall(data)
    except Exception:
        pass
    finally:
        try:
            channel.close()
        except Exception:
            pass
        try:
            client_socket.close()
        except Exception:
            pass


def _tunnel_accept_loop(server_socket, transport, remote_host, remote_port, stop_event) -> None:
    """Accept local connections and forward each one over the SSH transport."""
    while not stop_event.is_set():
        try:
            client_socket, _ = server_socket.accept()
        except OSError:
            break

        try:
            channel = transport.open_channel(
                "direct-tcpip",
                (remote_host, remote_port),
                client_socket.getpeername(),
            )
        except Exception as exc:
            print(f"[ERROR] SSH tunnel: failed to open channel: {exc}")
            client_socket.close()
            continue

        threading.Thread(
            target=_pump_socket_channel,
            args=(client_socket, channel),
            daemon=True,
        ).start()


def open_ssh_tunnel(ssh_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Connect over SSH and forward a local port to the remote Ollama port.

    Args:
        ssh_config: the 'ssh' section of config-ollama.yaml

    Returns a dict with the tunnel handles (including 'local_port'), or None
    when the tunnel could not be opened.
    """
    if paramiko is None:
        print("[ERROR] Paramiko is not installed, so 'ollama-ssh' cannot be used.")
        print("[ERROR] Run: pip install paramiko")
        return None

    ssh_host = ssh_config.get("host")
    ssh_port = ssh_config.get("port", 22)
    ssh_username = ssh_config.get("username")

    if not ssh_host or not ssh_username:
        print("[ERROR] config-ollama.yaml -> ssh: 'host' and 'username' must be filled in.")
        return None

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs: Dict[str, Any] = {
        "hostname": ssh_host,
        "port": ssh_port,
        "username": ssh_username,
        "timeout": ssh_config.get("connect_timeout", 20),
    }

    key_path = ssh_config.get("key_path")
    password = ssh_config.get("password")

    if key_path:
        connect_kwargs["key_filename"] = key_path

        if ssh_config.get("key_passphrase"):
            connect_kwargs["passphrase"] = ssh_config["key_passphrase"]

    elif password:
        connect_kwargs["password"] = password
        connect_kwargs["look_for_keys"] = False

    try:
        client.connect(**connect_kwargs)
    except Exception as exc:
        print(f"[ERROR] SSH connection to {ssh_host}:{ssh_port} failed: {exc}")
        return None

    transport = client.get_transport()
    transport.set_keepalive(30)

    remote_host = ssh_config.get("remote_ollama_host", "127.0.0.1")
    remote_port = ssh_config.get("remote_ollama_port", 11434)

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server_socket.bind((
            ssh_config.get("local_bind_host", "127.0.0.1"),
            ssh_config.get("local_bind_port", 0),
        ))
        server_socket.listen(8)
    except OSError as exc:
        print(f"[ERROR] Failed to bind the local tunnel port: {exc}")
        server_socket.close()
        client.close()
        return None

    local_port = server_socket.getsockname()[1]
    stop_event = threading.Event()

    threading.Thread(
        target=_tunnel_accept_loop,
        args=(server_socket, transport, remote_host, remote_port, stop_event),
        daemon=True,
    ).start()

    print(
        f"[INFO] SSH tunnel open: 127.0.0.1:{local_port} -> "
        f"{ssh_host}:{ssh_port} -> {remote_host}:{remote_port}"
    )

    return {
        "client": client,
        "server_socket": server_socket,
        "stop_event": stop_event,
        "local_port": local_port,
    }


def close_ssh_tunnel(tunnel: Optional[Dict[str, Any]]) -> None:
    """Shut down a tunnel opened by open_ssh_tunnel(). Safe to call with None."""
    if not tunnel:
        return

    tunnel["stop_event"].set()

    try:
        tunnel["server_socket"].close()
    except Exception:
        pass

    try:
        tunnel["client"].close()
    except Exception:
        pass

    print("[INFO] SSH tunnel closed.")


# ============================================================================
# OLLAMA API
# ============================================================================

def build_base_url(host: str, port: Any, scheme: str = "http") -> str:
    """Return the base URL of the Ollama server without a trailing slash."""
    return f"{scheme}://{host}:{port}".rstrip("/")


def list_installed_models(base_url: str) -> Optional[List[str]]:
    """
    Names of the models available on the Ollama server.

    Returns None when the server cannot be reached - that is a different
    problem from "the server is there but has no models".
    """
    try:
        response = requests.get(f"{base_url}/api/tags", timeout=30)
        response.raise_for_status()
    except Exception as exc:
        print(f"[ERROR] Cannot reach Ollama at {base_url}: {exc}")
        return None

    payload = response.json() or {}

    return [entry.get("name", "") for entry in payload.get("models", []) if entry.get("name")]


def _normalize_model_name(name: str) -> str:
    """Add the implicit ':latest' tag so names can be compared reliably."""
    name = str(name).strip()

    return name if ":" in name else f"{name}:latest"


def resolve_model_name(requested: str, installed_models: List[str]) -> Optional[str]:
    """Return the exact installed model name matching 'requested', or None."""
    target = _normalize_model_name(requested)

    for name in installed_models:
        if _normalize_model_name(name) == target:
            return name

    return None


def model_supports_vision(base_url: str, model_name: str) -> Optional[bool]:
    """True/False when the server reports capabilities, None when it does not."""
    try:
        response = requests.post(f"{base_url}/api/show", json={"model": model_name}, timeout=60)
        response.raise_for_status()
    except Exception:
        return None

    capabilities = (response.json() or {}).get("capabilities")

    if not capabilities:
        return None

    return "vision" in capabilities


def ocr_image(
    base_url: str,
    model_name: str,
    image_b64: str,
    prompt: str,
    system_prompt: Optional[str],
    settings: Dict[str, Any]
) -> Tuple[Optional[str], Optional[str]]:
    """
    Send one image to Ollama.

    Returns (text, error_message) - exactly one of them is None. Failures are
    retried settings['max_retries'] times before giving up.
    """
    payload: Dict[str, Any] = {
        "model": model_name,
        "prompt": prompt,
        "images": [image_b64],
        "stream": False,
        "keep_alive": settings.get("keep_alive"),
        "options": settings.get("ollama_options") or {},
    }

    if system_prompt:
        payload["system"] = system_prompt

    if settings.get("ollama_format"):
        payload["format"] = settings["ollama_format"]

    max_retries = int(settings.get("max_retries") or 0)
    retry_delay = settings.get("retry_delay", 5)
    request_timeout = settings.get("request_timeout", 600)

    last_error = None

    for attempt in range(1, max_retries + 2):
        try:
            response = requests.post(
                f"{base_url}/api/generate",
                json=payload,
                timeout=request_timeout,
            )
            response.raise_for_status()

            return (response.json() or {}).get("response", "").strip(), None

        except Exception as exc:
            last_error = exc

            if attempt <= max_retries:
                print(f"[WARN]   attempt {attempt} failed ({exc}); retrying in {retry_delay}s")
                time.sleep(retry_delay)

    return None, str(last_error)


# ============================================================================
# IMAGE ADAPTER
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
# LOCAL BATCH RUNNER
# ============================================================================

def _utc_now() -> str:
    """Timestamp in the same shape as the one used in the problem log."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_done_custom_ids(raw_file_path: Path) -> Set[str]:
    """Request ids already present in a raw file (used when resuming a run)."""
    if not raw_file_path.exists():
        return set()

    done: Set[str] = set()

    for record in parse_raw_content(raw_file_path.read_text(encoding="utf-8")):
        custom_id = record.get("custom_id") or record.get("id")

        if custom_id:
            done.add(custom_id)

    return done


def _report_ignored_settings(config: Dict[str, Any]) -> None:
    """
    Say out loud which parts of config.yaml do not apply to Ollama.

    There is no upload and no remote job, so the whole batch_generation section
    is dead here. Printing it beats silently doing something else than the
    config says.
    """
    batch_config = config.get('batch_generation', {}) or {}

    upload_method = batch_config.get('image_upload_method')
    if upload_method and str(upload_method).lower() != 'base64':
        print(
            f"[INFO] batch_generation.image_upload_method='{upload_method}' is ignored for ollama"
            " - images are always sent as base64."
        )

    operation_mode = batch_config.get('batch_operation_mode')
    if operation_mode:
        print(
            f"[INFO] batch_generation.batch_operation_mode='{operation_mode}' is ignored for ollama"
            " - the images are processed locally and written straight to raw_dir_base."
        )


def build_prompt(
    settings: Dict[str, Any],
    config: Dict[str, Any]
) -> Tuple[Optional[str], str]:
    """
    Decide which prompt the model gets.

    An OCR_PROMPT coming from the model provider (or from config-ollama.yaml ->
    defaults.ocr_prompt) wins over run_settings.active_prompt_key, because a
    local model usually needs its own short instruction rather than the long
    schema prompt written for the big providers.

    Returns (system_prompt, user_prompt); system_prompt may be None.
    Ollama /api/generate takes them as its "system" and "prompt" fields.
    """
    ocr_prompt = settings.get('ocr_prompt')

    if ocr_prompt:
        print("[INFO] Using the model provider prompt (prompts.yaml is ignored for this model).")
        return None, str(ocr_prompt)

    run_settings = config.get('run_settings', {})
    prompts_file_path = run_settings.get('prompts_file_path', 'prompts.yaml')
    active_prompt_key = run_settings.get('active_prompt_key', 'PROMPT_2')

    print(f"[INFO] Using prompt '{active_prompt_key}' from {prompts_file_path}")

    return load_prompts(prompts_file_path, active_prompt_key)


def run_local_batch(
    image_paths: List[Path],
    config: Dict[str, Any],
    model_name: str,
    ollama_config_path: Optional[str] = None
) -> Tuple[Optional[str], Optional[Path]]:
    """
    Run every image through Ollama and write the finished batch to raw_dir_base.

    This replaces "generate JSONL -> upload -> create batch job" for a provider
    that has no batch API. The images have already been collected and filtered
    by make_OCR_batch.py, so image_management and processing behave exactly as
    they do for gemini / openai.

    Each result is written and flushed immediately, so a run that dies half way
    leaves a raw file that can be processed as it is, or continued later with
    config-ollama.yaml -> run.resume_batch_id.

    Returns (batch_id, raw_file_path), or (None, None) when nothing was produced.
    """
    ollama_config = load_ollama_config(ollama_config_path)

    run_settings = config.get('run_settings', {})
    active_provider = str(run_settings.get('active_provider', '')).strip().lower()

    directories = config.get('directories', {})
    raw_dir = Path(directories.get('raw_dir_base', 'data_output/raw'))
    name_suffix = directories.get('name_suffix', '')

    _report_ignored_settings(config)

    if not model_name:
        print("ERROR: run_settings.model_name is empty.")
        print("ERROR: Set it to the exact name of a model installed in Ollama (see 'ollama list').")
        return None, None

    if not image_paths:
        print("[ERROR] No images left after filtering, so there is nothing to send to Ollama.")
        print("[ERROR] Check processing.mode and the allowed_batches / allowed_images settings.")
        return None, None

    tunnel = None

    try:
        # --- connection -----------------------------------------------------
        if active_provider == SSH_PROVIDER_NAME:
            tunnel = open_ssh_tunnel(ollama_config.get('ssh') or {})

            if not tunnel:
                return None, None

            base_url = build_base_url("127.0.0.1", tunnel["local_port"], "http")

        else:
            local_config = ollama_config.get('local') or {}
            base_url = build_base_url(
                local_config.get('host', DEFAULT_LOCAL_CONNECTION['host']),
                local_config.get('port', DEFAULT_LOCAL_CONNECTION['port']),
                local_config.get('scheme', DEFAULT_LOCAL_CONNECTION['scheme']),
            )

        # --- model ----------------------------------------------------------
        installed_models = list_installed_models(base_url)

        if installed_models is None:
            print("[ERROR] Is the Ollama server running and reachable?")
            return None, None

        installed_name = resolve_model_name(model_name, installed_models)

        if not installed_name:
            print(f"ERROR: Model '{model_name}' is not installed on the Ollama server at {base_url}.")

            if installed_models:
                print("ERROR: Models available there:")
                for name in sorted(installed_models):
                    print(f"         - {name}")
            else:
                print("ERROR: No models are installed there at all.")

            print(f"ERROR: Install it with:  ollama pull {model_name}")
            return None, None

        model_provider = resolve_model_provider(
            model_name, installed_name, ollama_config, installed_models
        )
        settings = build_settings(model_provider, ollama_config)

        if settings.get('check_vision_capability'):
            vision = model_supports_vision(base_url, installed_name)

            if vision is False:
                print(f"[WARN] Model '{installed_name}' does not report vision support; OCR will most likely fail.")

        system_prompt, user_prompt = build_prompt(settings, config)

        # --- batch id and raw file ------------------------------------------
        run_config = ollama_config.get('run') or {}
        batch_id_prefix = run_config.get('batch_id_prefix') or DEFAULT_BATCH_ID_PREFIX
        save_raw_response = run_config.get('save_raw_response', True)
        resume_batch_id = run_config.get('resume_batch_id')

        raw_dir.mkdir(parents=True, exist_ok=True)

        batch_id = str(resume_batch_id) if resume_batch_id else f"{batch_id_prefix}-{uuid.uuid4().hex}"
        raw_file_path = build_raw_file_path(raw_dir, batch_id, name_suffix)

        already_done: Set[str] = set()

        if resume_batch_id:
            already_done = read_done_custom_ids(raw_file_path)
            print(f"[INFO] Resuming batch {batch_id}: {len(already_done)} request(s) already in {raw_file_path.name}")

        # --- the run --------------------------------------------------------
        image_adapter = get_image_adapter(config)
        get_image_base64_and_mime_fn = image_adapter['get_image_base64_and_mime']

        total = len(image_paths)
        succeeded = 0
        failed = 0
        skipped = 0

        print(f"[INFO] Ollama batch {batch_id}")
        print(f"[INFO] Model '{installed_name}' at {base_url} ({total} image(s))")
        print(f"[INFO] Raw output: {raw_file_path}")

        with raw_file_path.open("a", encoding="utf-8") as raw_file:
            for index, image_path in enumerate(image_paths, start=1):
                image_path = Path(image_path)

                try:
                    batch_part, page_part = extract_batch_page_ids(image_path, config)
                    custom_id = build_custom_id(batch_part, page_part)
                except Exception as exc:
                    print(f"[WARN] [{index}/{total}] Cannot build custom_id from path {image_path}: {exc}")
                    skipped += 1
                    continue

                if custom_id in already_done:
                    print(f"[INFO] [{index}/{total}] {custom_id} is already in the raw file, skipping")
                    skipped += 1
                    continue

                if image_path.suffix.lower() == ".pdf":
                    print(f"[WARN] [{index}/{total}] {custom_id}: Ollama cannot read PDF, skipping {image_path.name}")
                    skipped += 1
                    continue

                try:
                    image_b64, _mime_type = get_image_base64_and_mime_fn(image_path, config)
                except Exception as exc:
                    print(f"[WARN] [{index}/{total}] {custom_id}: cannot read the image ({exc}), skipping")
                    skipped += 1
                    continue

                print(f"[INFO] [{index}/{total}] {custom_id} -> {installed_name}")

                started = time.perf_counter()
                text, error = ocr_image(
                    base_url, installed_name, image_b64, user_prompt, system_prompt, settings
                )
                elapsed = round(time.perf_counter() - started, 2)

                record: Dict[str, Any] = {
                    "id": custom_id,
                    "model": installed_name,
                    "image": str(image_path),
                    "seconds": elapsed,
                    "created_at": _utc_now(),
                }

                if error is not None:
                    record["error"] = {"code": "OLLAMA_REQUEST_FAILED", "message": error}
                    failed += 1
                    print(f"[ERROR]  {custom_id} failed after {elapsed}s: {error}")

                else:
                    processed_text, postprocess_error = apply_postprocess(model_provider, text, config)
                    record["text"] = processed_text

                    if postprocess_error:
                        record["postprocess_error"] = postprocess_error

                    if save_raw_response:
                        record["raw_response"] = text

                    succeeded += 1
                    print(f"[INFO]   done in {elapsed}s ({len(processed_text or '')} chars)")

                raw_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                raw_file.flush()

    finally:
        close_ssh_tunnel(tunnel)

    print(f"[INFO] Ollama batch finished: {succeeded} ok, {failed} failed, {skipped} skipped (of {total})")

    # An empty raw file would later be picked up by get_OCR_results.py and
    # logged as COMPLETED_NO_OUTPUT, which hides the real reason. Remove it.
    if raw_file_path.exists() and raw_file_path.stat().st_size == 0:
        raw_file_path.unlink()
        print("[WARN] No request produced a result - the empty raw file was removed.")
        return None, None

    return batch_id, raw_file_path


# ============================================================================
# PROVIDER INTERFACE - RESULT SIDE
# ============================================================================

def get_request_custom_id(request: Dict[str, Any]) -> Optional[str]:
    """
    Request id of one line of a raw Ollama batch file.

    The lines this provider writes carry the id in 'id', the same key Gemini
    uses, so make_OCR_batch.log_batch_requests can read them back unchanged.
    """
    return request.get("id")


def extract_text_from_record(record: Dict[str, Any]) -> Optional[str]:
    """
    Text handed to the validator.

    It was already postprocessed by the model provider at the time the raw file
    was written, so there is nothing to unwrap here. 'raw_response', when
    present, is the untouched model answer and is deliberately not used.
    """
    if record.get("error"):
        return None

    return record.get("text")


def is_error_record(record: Dict[str, Any]) -> bool:
    """A record whose request failed carries an 'error' object instead of text."""
    return bool(record.get("error"))


def parse_error_records(
    all_records: List[Dict[str, Any]],
    store_error_custom_ids: bool
) -> Tuple[int, List[str]]:
    """Count the failed requests of a batch and collect their ids."""
    return base_parse_error_records(all_records, store_error_custom_ids, is_error_record)


def get_tokens_from_records(records: List[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    """
    Ollama raw records carry no token counts, so there is nothing to sum.

    Unlike the batch API stubs below this one is called in the Ollama flow
    (get_OCR_results.py, mode='process_only'), so it must not raise.
    """
    return None


# ============================================================================
# PROVIDER INTERFACE - BATCH API (not available)
# ============================================================================
# Ollama has no batch API. These exist because provider_selector checks the
# whole contract up front; nothing in the Ollama flow ever calls them.

def _no_batch_api(function_name: str):
    """Build a stub that explains where the Ollama results really come from."""
    def stub(*args, **kwargs):
        raise NotImplementedError(
            f"'{function_name}' is not available for Ollama - it has no batch API. "
            "make_OCR_batch.py writes the finished batch straight into "
            "directories.raw_dir_base and get_OCR_results.py reads it from there "
            "(mode='process_only')."
        )

    stub.__name__ = function_name

    return stub


make_jsonl_line = _no_batch_api("make_jsonl_line")
build_jsonl_batches = _no_batch_api("build_jsonl_batches")
upload_image_to_provider = _no_batch_api("upload_image_to_provider")
upload_file_to_provider = _no_batch_api("upload_file_to_provider")
create_batch_job = _no_batch_api("create_batch_job")
list_batches = _no_batch_api("list_batches")
get_batch = _no_batch_api("get_batch")
download_file = _no_batch_api("download_file")
parse_batch_results = _no_batch_api("parse_batch_results")
get_batch_status = _no_batch_api("get_batch_status")
get_batch_id = _no_batch_api("get_batch_id")
batch_created_after = _no_batch_api("batch_created_after")
download_raw_batch = _no_batch_api("download_raw_batch")
get_batch_tokens = _no_batch_api("get_batch_tokens")
get_processing_time = _no_batch_api("get_processing_time")
get_model_name = _no_batch_api("get_model_name")
get_batch_created_time = _no_batch_api("get_batch_created_time")
get_batch_completed_time = _no_batch_api("get_batch_completed_time")
get_request_counts = _no_batch_api("get_request_counts")

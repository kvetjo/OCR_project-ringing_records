# Local models with Ollama

This page explains how to run the pipeline with a **free AI model on your own computer**, or on a server you reach over SSH. It covers:

- what Ollama is and how it changes the workflow,
- finding, downloading and testing a model,
- why specialised OCR models need special prompts,
- all settings, and a complete local run,
- the SSH mode,
- the example model module `glm_ocr_provider.py`,
- how to add a new local model yourself.

**On this page**

1. [What Ollama is](#what-ollama-is)
2. [How a local run differs from a cloud run](#how-a-local-run-differs-from-a-cloud-run)
3. [Install Ollama](#install-ollama)
4. [Find a model](#find-a-model)
5. [Download and test a model](#download-and-test-a-model)
6. [Specialised OCR models accept only certain prompts](#specialised-ocr-models-accept-only-certain-prompts)
7. [Settings in config.yaml](#settings-in-configyaml)
8. [config-ollama.yaml](#config-ollamayaml)
9. [Running the whole workflow locally](#running-the-whole-workflow-locally)
10. [Running the model on a server over SSH](#running-the-model-on-a-server-over-ssh)
11. [Example: glm_ocr_provider.py](#example-glm_ocr_providerpy)
12. [Adding a new local model](#adding-a-new-local-model)
13. [Naming rules](#naming-rules)
14. [The raw file of a local run](#the-raw-file-of-a-local-run)
15. [Troubleshooting local models](#troubleshooting-local-models)
16. [Further reading](#further-reading)

---

## What Ollama is

[Ollama](https://ollama.com) is a free program that downloads AI models and runs them on your own computer. It offers a small web service (an *API*) at `http://localhost:11434`, and the pipeline sends the images to that address.

| Advantages | Disadvantages |
|---|---|
| No cost per page | Needs a strong computer; a graphics card (GPU) with enough memory helps a lot |
| The scans never leave your computer | Much slower. In this project's test run, `glm-ocr` needed about **7 minutes per ringing sheet** |
| Works offline, once the model is downloaded | Small local models usually read handwriting worse than the big cloud models |

The two ways to run it:

| `active_provider` | Where the model runs |
|---|---|
| `ollama` | on this computer |
| `ollama-ssh` | on another computer (a server), reached through an encrypted SSH connection |

---

## How a local run differs from a cloud run

```
CLOUD (gemini / openai)                         LOCAL (ollama / ollama-ssh)
---------------------------------               -----------------------------------------
make_OCR_batch.py                               make_OCR_batch.py
  builds a batch file and uploads it              sends image 1 to the model, waits, saves the answer
  -> the provider runs a batch job                sends image 2 to the model, waits, saves the answer
     (minutes to hours)                           ...
                                                  -> data_output/raw/ollama-<id><suffix>_raw.jsonl

get_OCR_results.py                              get_OCR_results.py
  downloads the answers, validates them           reads that raw file (process_only), validates it
```

**Stays the same:**

- `image_management` (local folder or Cloudflare),
- `processing` (all filters, `path_parsing`),
- `prompts.yaml` (unless the model module sets its own prompt),
- `validation`, the output files and the state files.

**Is different:**

- No API key is needed.
- The whole `batch_generation` section is ignored; a note is printed.
- Images are always sent directly (as base64). `.pdf` files are skipped.
- All the AI work happens inside `make_OCR_batch.py`. The terminal must stay open until it ends, which can take hours for many images.
- The batch id looks like `ollama-74144dd4e1d242e1b97dba413d88580a`.

---

## Install Ollama

1. Download Ollama from <https://ollama.com/download> (Windows, macOS, Linux) and install it.
2. Start it:
   - **Windows / macOS:** after the installation it runs in the background, with an icon in the taskbar or menu bar.
   - **Linux:** if it is not running as a service, start it with `ollama serve`.
3. Check that it works:
   - `ollama --version` prints the version.
   - <http://localhost:11434> opened in a browser shows *"Ollama is running"*.

On the Python side:

- `ollama` mode needs nothing extra; the `requests` package is enough.
- `ollama-ssh` needs `paramiko`. It is already in `requirements.txt`; otherwise run `pip install paramiko`.

---

## Find a model

You need a **vision model**, which is a model that can look at images. It should be good at reading documents or handwriting, and it must fit your hardware.

### Option A – the Ollama library

<https://ollama.com/library> lists ready-to-use models. Search for *ocr* or *vision*. Each model page shows the sizes and the command to download it.

The model used in this project is **glm-ocr**, a specialised OCR model of about 2.2 GB: <https://ollama.com/library/glm-ocr>.

### Option B – Hugging Face

[Hugging Face](https://huggingface.co/models) is the largest collection of AI models. Ollama can run models published there in the **GGUF** format directly.

1. On <https://huggingface.co/models>, filter by *Libraries → GGUF*. For image models, also filter by *Tasks → Image-Text-to-Text*.
2. Open a model. The button **"Use this model" → Ollama** shows the command. If Ollama is not listed, enable it under *Local Apps* in your Hugging Face settings.
3. The command looks like this:
   ```bash
   ollama pull hf.co/<user>/<repository>
   ollama pull hf.co/<user>/<repository>:Q4_K_M
   ```
   The part after `:` chooses the **quantisation**. A quantised model is a compressed version: smaller and faster, but slightly less accurate. `Q4_K_M` is the usual default; `Q8_0` is bigger and more accurate.
4. Not every GGUF model can see images in Ollama. After downloading, check with `ollama show <model>` that the capabilities include **vision**.

### Which model size can my computer run?

As a rule of thumb, the download size should fit into the memory of your graphics card (VRAM) for good speed. A model that only fits into normal memory (RAM) runs on the processor and is much slower. Start with a small model.

Always read the **model card** (the description page). It tells you what the model is good at, **which prompts it expects**, what it returns, and its licence.

---

## Download and test a model

| Command | What it does |
|---|---|
| `ollama pull glm-ocr` | download a model |
| `ollama list` (short: `ollama ls`) | list the installed models with their **exact names**, e.g. `glm-ocr:latest` |
| `ollama show glm-ocr` | show details of a model, including its capabilities (look for `vision`) |
| `ollama run glm-ocr "Table Recognition: C:\scans\batch_1\page_1.jpg"` | a quick test: put the image path into the prompt |
| `ollama ps` | show which models are loaded in memory right now |
| `ollama stop glm-ocr` | unload a model from memory |
| `ollama rm glm-ocr` | delete a model |

`glm-ocr` and `glm-ocr:latest` are the same model: `:latest` is the default tag, and the pipeline accepts both.

Test the model by hand on one or two scans before you start the pipeline. It is the fastest way to see what the model can do and what its answers look like.

---

## Specialised OCR models accept only certain prompts

There are two kinds of vision models:

- **General vision models** understand free instructions. You can send them the normal prompts from `prompts.yaml`, e.g. `PROMPT_4`, which describes the exact JSON to return.
- **Specialised OCR models** were trained on a **few fixed instructions**. Other instructions are ignored or give poor or empty results. Their answer comes in their own format (plain text, Markdown or an HTML table), not in the JSON the validators expect.

**GLM-OCR is a specialised model.** According to its model card (<https://huggingface.co/zai-org/GLM-OCR>) it supports these prompts:

| Prompt | Result |
|---|---|
| `Text Recognition:` | the text of the image |
| `Table Recognition:` | the tables of the image; in practice, glm-ocr answers with an HTML `<table>` |
| `Formula Recognition:` | mathematical formulas |
| a prompt written as a strict JSON schema | information extraction into that JSON shape |

The long `PROMPT_4` from `prompts.yaml` is none of these, so glm-ocr cannot use it. This is why the pipeline has **model modules**. A model module can:

1. send the model the prompt it understands (`OCR_PROMPT = "Table Recognition:"`), and
2. convert the answer into the JSON the validator expects (`postprocess_output()`).

**Before you use a new model, find out from its model card which prompts it accepts and what it returns.**

---

## Settings in config.yaml

```yaml
run_settings:
  active_provider: "ollama"            # or "ollama-ssh"
  model_name: "glm-ocr:latest"         # exactly as 'ollama list' shows it
  active_prompt_key: "PROMPT_4"        # only used when the model module has no OCR_PROMPT
  run_name: "ringing-ollama-test"

# api_keys:          not needed
# image_management:  as usual (local or cloudflare)
# batch_generation:  ignored (a note is printed)

processing:
  mode: "explicit"                     # start with 1-2 images
  allowed_images: ["img_1_1", "img_1_2"]

validation:
  active_validator: "ocr_ringing"
  ocr_ringing:
    backfill_species: "Spanish Sparrow"   # needed for glm-ocr, see below
    # ... the other ocr_ringing settings as usual
```

- `model_name` must be a model that is installed in Ollama. If it isn't, the run stops and lists the installed models.
- With **glm-ocr**, set `backfill_species` to the species of the sheets. The species name is printed above the table, and a table-reading model never sees it, so every row arrives with `species: null`.

---

## config-ollama.yaml

This file sits in the project folder, next to `config.yaml`. It is only read when `active_provider` is `ollama` or `ollama-ssh`, and it is set up once per computer or server.

```yaml
local:                          # used by active_provider "ollama"
  scheme: "http"
  host: "localhost"
  port: 11434

ssh:                            # used by active_provider "ollama-ssh" (see the SSH section)
  host: "192.168.1.10"
  port: 22
  username: "user"
  password: null
  key_path: null
  key_passphrase: null
  connect_timeout: 20
  remote_ollama_host: "127.0.0.1"
  remote_ollama_port: 11434
  local_bind_host: "127.0.0.1"
  local_bind_port: 0

model_providers:                # model name -> model module in ai_providers/
  "glm-ocr": "glm_ocr_provider"
  "glm-ocr:latest": "glm_ocr_provider"

defaults:                       # used when the model module does not set a value
  ocr_prompt: null
  ollama_options:
    temperature: 0
    num_ctx: 8192
  ollama_format: null
  keep_alive: "5m"
  request_timeout: 600
  max_retries: 2
  retry_delay: 5
  check_vision_capability: true

run:
  batch_id_prefix: "ollama"
  save_raw_response: true
  resume_batch_id: null
```

⚠ Two things are different from `config.yaml`:

- `${...}` placeholders are **not** replaced in this file.
- The file is **not** excluded from git. Never write a password into it; use an SSH key file (`key_path`).

### local

| Key | Default | Meaning |
|---|---|---|
| `scheme` | `http` | protocol of the Ollama address |
| `host` | `localhost` | computer where Ollama runs |
| `port` | `11434` | Ollama's port (the default port of Ollama) |

### model_providers

The link between a model and its model module.

- **Left side:** the model name exactly as in `run_settings.model_name`, or as Ollama reports it (with the tag, e.g. `glm-ocr:latest`). Put it in quotes, because model names contain `:` and sometimes `/`.
- **Right side:** the file name of the module in `ai_providers/`, **without** `.py`.
- Nothing is guessed from names. A model without an entry still runs, but its answers are written **unchanged**. A `[WARN]` then lists the installed models, the current mapping and the available modules.

### defaults, and the order of priority

Every request setting is taken from the first place where it is set:

1. a **constant in the model module** (e.g. `OCR_PROMPT` in `glm_ocr_provider.py`),
2. **`defaults`** in `config-ollama.yaml`,
3. the **built-in fallback** in `ai_providers/base_ollama.py`.

A constant that is missing or `None` falls through to the next level. `False` and `0` count as real values.

| Setting in `defaults` | Constant in a model module | Built-in fallback | Meaning |
|---|---|---|---|
| `ocr_prompt` | `OCR_PROMPT` | none | When set, this text is sent as the prompt, `prompts.yaml` is ignored and no system prompt is sent. When empty, the `system_prompt` and `user_prompt` of `active_prompt_key` are sent. |
| `ollama_options` | `OLLAMA_OPTIONS` | `{}` | Options passed to the model. `temperature` 0 gives the most predictable answers. `num_ctx` is the *context window*: how much text (prompt, image and answer together) the model can handle at once. Raise it if answers are cut off. |
| `ollama_format` | `OLLAMA_FORMAT` | none | `"json"` forces the model to answer with valid JSON; a JSON schema forces a given shape. |
| `keep_alive` | `KEEP_ALIVE` | `"5m"` | How long the model stays in memory after the last image. |
| `request_timeout` | `REQUEST_TIMEOUT` | `600` | Seconds to wait for the answer to one image. |
| `max_retries` | `MAX_RETRIES` | `2` | Extra attempts after a failed request. |
| `retry_delay` | `RETRY_DELAY` | `5` | Seconds between attempts. |
| `check_vision_capability` | `CHECK_VISION_CAPABILITY` | `True` | Warn if the model does not report image support. |

### run

| Key | Default | Meaning |
|---|---|---|
| `batch_id_prefix` | `ollama` | The batch id is `<prefix>-<32 random characters>`. |
| `save_raw_response` | `true` | Also keep the model's untouched answer (`raw_response`) next to the converted text. Costs disk space, but you can always see what the model really said. |
| `resume_batch_id` | `null` | Continue an interrupted run; see below. |

---

## Running the whole workflow locally

1. **Start Ollama.** Run `ollama list` and check that your model is there.
2. **Set up `config.yaml` and `config-ollama.yaml`** as described above. Start with 1–2 images (`processing.mode: "explicit"`).
3. **Run** `python make_OCR_batch.py`. Example output:
   ```
   [INFO] Run name: ringing-ollama-test
   [INFO] Found 1150 images before filtering
   [INFO] Processing 2 images after filtering
   [INFO] batch_generation.image_upload_method='file_api' is ignored for ollama - images are always sent as base64.
   [INFO] batch_generation.batch_operation_mode='generate_and_upload' is ignored for ollama - the images are processed locally and written straight to raw_dir_base.
   [INFO] Using the model provider prompt (prompts.yaml is ignored for this model).
   [INFO] Ollama batch ollama-74144dd4e1d242e1b97dba413d88580a
   [INFO] Model 'glm-ocr:latest' at http://localhost:11434 (2 image(s))
   [INFO] Raw output: data_output\raw\ollama-74144dd4e1d242e1b97dba413d88580a-ringing-ollama-test_raw.jsonl
   [INFO] [1/2] img_1_1 -> glm-ocr:latest
   [INFO]   done in 423.22s (... chars)
   [INFO] [2/2] img_1_2 -> glm-ocr:latest
   [INFO]   done in 417.59s (... chars)
   [INFO] Ollama batch finished: 2 ok, 0 failed, 0 skipped (of 2)
   [INFO] Logged 2 requests from ollama-74144dd4e1d242e1b97dba413d88580a-ringing-ollama-test_raw.jsonl to processed_items.txt
   [SUCCESS] Local batch finished: ollama-74144dd4e1d242e1b97dba413d88580a
   [INFO] Raw results: data_output\raw\ollama-74144dd4e1d242e1b97dba413d88580a-ringing-ollama-test_raw.jsonl
   [INFO] Next step: in get_OCR_results.py run main(batch_ids=["ollama-74144dd4e1d242e1b97dba413d88580a"])
   ```
   Every answer is saved as soon as it arrives. If the run stops half way, nothing is lost.
4. **Put the printed id into `get_OCR_results.py`:**
   ```python
   main(batch_ids=["ollama-74144dd4e1d242e1b97dba413d88580a"])
   ```
   The `mode` doesn't matter; it is switched to `process_only` automatically.
5. **Run** `python get_OCR_results.py`. The results and review files appear as usual in `data_output/output/<run_name>/`.

### Continuing an interrupted run

1. Find the batch id. It is the start of the raw file name in `data_output/raw/` (`ollama-<32 characters>`), and it is also printed at the start of the run.
2. Set it in `config-ollama.yaml`:
   ```yaml
   run:
     resume_batch_id: "ollama-74144dd4e1d242e1b97dba413d88580a"
   ```
3. Run `make_OCR_batch.py` again with the **same `run_name`** and the same filters. Images that are already in the raw file are skipped, and new answers are added to the same file.
4. **Set `resume_batch_id` back to `null` afterwards.** Otherwise every later run adds to the same batch.

Notes:

- With a different `run_name`, a second raw file with the same batch id is created. `get_OCR_results.py` then refuses to choose between the two files.
- Images that **failed** are also in the raw file (with an `error`), so a resumed run skips them too. Send failed images again in a new run (`resume_batch_id: null`, `mode: "explicit"`).

---

## Running the model on a server over SSH

Use `active_provider: "ollama-ssh"` when the model runs on another computer, for example a server with a strong graphics card.

```
YOUR COMPUTER                                            SERVER
-------------                                            ------
make_OCR_batch.py                                        Ollama, listening on 127.0.0.1:11434
   |                                                          ^
   |  http://127.0.0.1:<free local port>                      |
   v                                                          |
SSH tunnel (paramiko) ======== encrypted SSH, port 22 =====> SSH server
```

- The script opens an **SSH tunnel**: a private, encrypted connection that forwards a port on your computer to the Ollama port on the server.
- The scans are read on **your** computer (local folder or Cloudflare) and sent through the tunnel. The answers are written on **your** computer.
- Ollama on the server can keep its default setting (listening only on `127.0.0.1`), because the tunnel reaches it from inside the server.
- ⚠ The comment in `config.yaml` marks this mode as *not tested properly*. Test it with 1–2 images first.

### What you need

1. SSH access to the server: a user name plus a key file or a password. Test it first with `ssh user@192.168.1.10`.
2. On the server: Ollama installed and running, and the model downloaded **there** (`ollama pull glm-ocr`). `ollama list` on the server must show it.
3. On your computer: the `paramiko` package (it is in `requirements.txt`).

### The ssh section of config-ollama.yaml

| Key | Default | Meaning |
|---|---|---|
| `host` | – (required) | the server's name or IP address |
| `port` | `22` | the SSH port |
| `username` | – (required) | your user name on the server |
| `key_path` | `null` | the path to your private SSH key file, e.g. `"C:\\Users\\me\\.ssh\\id_ed25519"`. **Recommended.** |
| `key_passphrase` | `null` | the passphrase of the key file, if it has one |
| `password` | `null` | a password instead of a key. It is stored in plain text, so avoid it. If `key_path` is set, the key file is used. |
| `connect_timeout` | `20` | seconds to wait for the connection |
| `remote_ollama_host` | `127.0.0.1` | where Ollama listens **on the server** |
| `remote_ollama_port` | `11434` | Ollama's port on the server |
| `local_bind_host` | `127.0.0.1` | your end of the tunnel |
| `local_bind_port` | `0` | `0` = pick any free port automatically |

The server's identity (its *host key*) is accepted automatically the first time you connect.

In `config.yaml`, set `active_provider: "ollama-ssh"` and use the model name as `ollama list` shows it **on the server**. The run then prints:

```
[INFO] SSH tunnel open: 127.0.0.1:53211 -> 192.168.1.10:22 -> 127.0.0.1:11434
...
[INFO] SSH tunnel closed.
```

`get_OCR_results.py` works exactly as for a local run. It does not connect to the server at all, because the answers are already on your computer.

---

## Example: glm_ocr_provider.py

`ai_providers/glm_ocr_provider.py` is the model module for **glm-ocr**. It is linked in `config-ollama.yaml` by `"glm-ocr:latest": "glm_ocr_provider"`.

What happens with one ringing sheet:

```
scan ---- prompt "Table Recognition:" ----> glm-ocr

glm-ocr answers with an HTML table:
  <table><tr><th>SERIAL</th><th>RING NO.</th>...</tr><tr><td>8</td><td>15.656</td>...</tr>...</table>

postprocess_output() turns it into the JSON of PROMPT_4:
  [{"id_row": 1, "serial": "8", "ring_no": "15.656", "species": null,
    "ringing": [{"date": "12-07-1994", "age": "4", ...}]}, ...]

the raw file stores both:
  "text"         = the JSON          -> this is what the validator gets
  "raw_response" = the original HTML -> kept for checking (save_raw_response: true)
```

The parts of the file:

1. **Request settings.**
   - `OCR_PROMPT = "Table Recognition:"` is one of the prompts GLM-OCR understands. It replaces `active_prompt_key`.
   - `OLLAMA_OPTIONS = {"temperature": 0, "num_ctx": 8192}`.
   - All other constants are `None`, so the values from `config-ollama.yaml → defaults` are used.
2. **`HEADER_ALIASES`**: which column header of the sheet belongs to which field.
   - Headers are compared without spaces or punctuation and in lower case, so `RING NO.` becomes `ringno` and then `ring_no`.
   - Columns that are not in the list (BILL, TARS, TAIL, TRAP, Rec, A.T., G.T.) are dropped, just as `PROMPT_4` asks.
   - To support a form with other headers, add aliases here.
3. **Reading the HTML table** (`_TableExtractor`, `expand_rows`, `parse_html_tables`).
   - All tables are read, and broken HTML is tolerated.
   - Merged cells (`colspan`, `rowspan`) are expanded into a regular grid. This matters because the ringing form has a two-level header (MEASUREMENTS above WING, BILL, …); otherwise every column after it would be shifted by one.
4. **Cleaning the cells.**
   - Empty cells and ditto marks (`"`, `''`, `„`, `//`, `do`, `ditto`, …) become `null`. A single `-` is kept, because it is real data.
   - Sex symbols become `male` / `female`.
   - Dates like `08.08.74` become `08-08-1974`; two-digit years are read as 19xx.
   - The `Re` column becomes `true` or `false`.
5. **Building the rows** (`rows_to_ringing_records`).
   - `species` is always `null`, so use `backfill_species`.
   - Blank rows are kept, because the validator checks that there are 23 rows.
6. **`CONVERTERS`**: the validators this module can build JSON for.
   - Only `ocr_ringing` (and `ocr_ringing-STORM_PETREL`).
   - With `ocr_retrap` or `ocr_sightings`, the HTML is passed on unchanged, so the validator cannot read it and reports `JSON_PARSE_ERROR`. To use glm-ocr for another form, write a converter function and add it here.
7. **`postprocess_output(raw_text, config)`**, the entry point:
   1. removes a ```` ``` ```` block around the answer;
   2. if the answer already is JSON (it starts with `[` or `{`), returns it as it is;
   3. picks the converter for `validation.active_validator`;
   4. takes the biggest table on the page and converts it;
   5. returns the JSON text.

   Anything that cannot be converted is returned unchanged, with a `[WARN]`, so nothing is lost.

If `postprocess_output()` crashes with a Python error, the untouched answer is used, the error is saved in the raw file as `postprocess_error`, and the run continues.

---

## Adding a new local model

### Step by step

1. **Download and test the model** in Ollama (see [above](#download-and-test-a-model)). Check that `ollama show <model>` lists `vision`.
2. **Read the model card:** which prompts does it accept, and what does it return?
3. **Do a first run without a model module.**
   1. Set `model_name`, but don't add the model to `model_providers` yet.
   2. Run `make_OCR_batch.py` on 1–2 images. You get a `[WARN]` (no module), and the answers are written unchanged into the raw file (field `text`).
   3. Look at them. Are they already the JSON the validator expects?
   4. Run `get_OCR_results.py` with the printed id. `JSON_PARSE_ERROR` lines in the problems file mean the answers must be converted.

   A general vision model that follows the `prompts.yaml` prompt well may not need any conversion. Add a module anyway if you want other options.
4. **Create the module**, `ai_providers/<name>_provider.py`. Use the template below and follow the [naming rules](#naming-rules).
5. **Link it** in `config-ollama.yaml`:
   ```yaml
   model_providers:
     "my-model:latest": "my_model_provider"
   ```
6. **Test again** on 1–2 images:
   - Compare `text` and `raw_response` in the raw file.
   - Run `get_OCR_results.py` and read the review file.
7. **Repeat** until the results are good.
   - Fixing `postprocess_output()` does **not** change raw files that were already written, and there is no ready-made command to convert an old raw file again.
   - Run the test images again (with `resume_batch_id: null`), or write a small script that applies your function to the `raw_response` of the old file.
8. **Scale up:** switch `processing.mode` to `"batches"` or `"all"`.

### Template

Everything in this file is optional. Delete what you don't need.

```python
"""
Model module for <model name> (Ollama).
Linked in config-ollama.yaml -> model_providers.
"""
import json
from typing import Any, Dict

# ----------------------------------------------------------------------
# Request settings. None = use config-ollama.yaml -> defaults.
# ----------------------------------------------------------------------
OCR_PROMPT = None             # e.g. "Text Recognition:" for a specialised model;
                              # None = use the prompt from prompts.yaml
OLLAMA_OPTIONS = {"temperature": 0, "num_ctx": 8192}
OLLAMA_FORMAT = None          # "json" = force the model to answer with JSON
KEEP_ALIVE = None
REQUEST_TIMEOUT = None
MAX_RETRIES = None
RETRY_DELAY = None
CHECK_VISION_CAPABILITY = None


def postprocess_output(raw_text: str, config: Dict[str, Any]) -> str:
    """
    Turn the model's answer into the text the validator expects
    (usually a JSON list or object, as text).

    'config' is the whole config.yaml, e.g.
    config["validation"]["active_validator"] tells you which form is processed.
    Return raw_text unchanged when you cannot convert it - nothing is lost then.
    """
    lines = raw_text.strip().splitlines()

    # Remove a ```json ... ``` block around the answer
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    text = "\n".join(lines).strip()

    # Only hand over valid JSON; otherwise keep the original answer
    try:
        json.loads(text)
    except ValueError:
        return raw_text

    return text
```

For a model that answers in its own format, like an HTML or Markdown table, write the conversion in `postprocess_output()`. `glm_ocr_provider.py` is a complete example of this.

**Tip – one model, different prompts per document type.** `OCR_PROMPT` is one fixed text. If you need different special prompts for different forms, leave `OCR_PROMPT = None` and add prompts to `prompts.yaml` instead:

```yaml
PROMPT_GLM_TABLE:
  system_prompt: ""
  user_prompt: "Table Recognition:"
```

Then choose one with `active_prompt_key`. An empty system prompt is not sent; a warning about it is printed, but the run works.

---

## Naming rules

**Does the file name of the module have to match the model name?** No. Nothing is guessed from names: the line in `model_providers` is the only link between a model and its module. `glm-ocr` → `glm_ocr_provider` is just a convention.

These rules do matter:

| Rule | Why |
|---|---|
| The module file must be in `ai_providers/`. | Modules are loaded as `ai_providers.<name>`. |
| In `model_providers`, write the module name **without** `.py`. | `"glm_ocr_provider"`, not `"glm_ocr_provider.py"`. |
| Use only lower-case letters, digits and underscores, e.g. `nanonets_ocr_provider.py`. No hyphens, spaces or dots. | This is a valid Python module name. A hyphen breaks normal `import` lines. |
| Don't start the name with `_`. | Such files are hidden from the list of available modules. |
| Don't use a reserved name: `base`, `base_ollama`, `gemini_provider`, `openai_provider`, `ollama`, `provider_interface`, `provider_selector`. | These are parts of the pipeline itself (`ollama` was an old script) and are never treated as model modules. |
| Suggested pattern: `<model>_provider.py`. | Easy to recognise. |
| The key in `model_providers` is the exact model name, in quotes, e.g. `"glm-ocr:latest"` or `"hf.co/user/repository:Q4_K_M"`. | It is compared with `run_settings.model_name`, and with the name Ollama reports. |
| Don't rename the raw files in `data_output/raw/`. | `get_OCR_results.py` finds a raw file by looking for the batch id in its name. If two files contain the same id, the batch is skipped. |

---

## The raw file of a local run

**Location:** `data_output/raw/<batch id><name_suffix>_raw.jsonl`, e.g. `ollama-74144dd4e1d242e1b97dba413d88580a-ringing-ollama-test_raw.jsonl`.

It has one line per image:

| Field | Meaning |
|---|---|
| `id` | the image id, e.g. `img_1_1` |
| `model` | the model name as installed |
| `image` | the path of the scan |
| `seconds` | how long the model needed |
| `created_at` | the time (UTC) |
| `text` | the answer after `postprocess_output()`. **This is what the validator gets.** |
| `raw_response` | the untouched answer (only with `save_raw_response: true`) |
| `postprocess_error` | only present if `postprocess_output()` crashed |
| `error` | only present if the request failed: `{"code": "OLLAMA_REQUEST_FAILED", "message": "..."}`. There is no `text` then. |

Example (shortened):

```json
{"id": "img_1_1", "model": "glm-ocr:latest", "image": "ringing\\batch_1\\page_1.jpg", "seconds": 423.22,
 "created_at": "2026-08-28T08:41:03.638133Z",
 "text": "[{\"id_row\": 1, \"serial\": \"8\", \"ring_no\": \"15.656\", \"species\": null, ...}]",
 "raw_response": "<table>...</table>"}
```

If not a single line was written, because every image was skipped, the empty raw file is deleted and the run reports `[WARN] No request produced a result`. Images that failed are written as lines with an `error`, so they do not count as skipped.

---

## Troubleshooting local models

| Message | What to do |
|---|---|
| `[ERROR] Cannot reach Ollama at http://localhost:11434: ...` and `Is the Ollama server running and reachable?` | Start Ollama. Check `host` and `port` in the `local` section of `config-ollama.yaml`. |
| `ERROR: Model 'x' is not installed on the Ollama server at ...` (followed by a list) | Run `ollama pull x` (on the server, for SSH), or correct `model_name` to a name from the list. |
| `ERROR: run_settings.model_name is empty.` | Fill in `model_name`. |
| `[WARN] Model 'x' has no entry in config-ollama.yaml -> model_providers.` | Add the model to `model_providers`. You can ignore the warning if unchanged answers are fine. |
| `ERROR: config-ollama.yaml maps model 'x' to module 'y', but it cannot be imported: ...` | The file is missing, in the wrong folder or wrongly named, or it contains a Python error (shown in the message). |
| `ERROR: Ollama config file '...' does not exist.` | Create `config-ollama.yaml` next to `config.yaml`. |
| `[WARN] Model 'x' does not report vision support; OCR will most likely fail.` | Choose a model with the `vision` capability. |
| `[WARN] [3/10] img_1_3: Ollama cannot read PDF, skipping page_3.pdf` | Convert PDF files to JPEG ([EXTRA_TOOLS.md](EXTRA_TOOLS.md#pdf-to-jpeg-converter)). |
| `[WARN]   attempt 1 failed (...); retrying in 5s` | Usually a timeout. Raise `request_timeout`, use a smaller model or a GPU. |
| `[ERROR]  img_1_2 failed after 600.0s: ...` | The image is saved with an `error`, and the validator skips it. Send it again in a new run. |
| `[WARN] No request produced a result - the empty raw file was removed.` | Every image was skipped (not a valid image id, a PDF, or unreadable). Read the messages above it. |
| `[WARN] glm_ocr_provider: no HTML -> JSON converter for validator 'ocr_retrap', ...` | glm-ocr only converts ringing sheets; see [the example](#example-glm_ocr_providerpy). |
| `[WARN] glm_ocr_provider: no <table> in the model output ...` or `... no recognisable header row ...` | The model did not return a usable table. Look at `raw_response` in the raw file. |
| `[WARN] postprocess_output() in ... failed: ...` | There is a bug in your model module. The untouched answer was used; fix the module and run the images again. |
| `[ERROR] Paramiko is not installed, so 'ollama-ssh' cannot be used.` | `pip install paramiko` |
| `[ERROR] config-ollama.yaml -> ssh: 'host' and 'username' must be filled in.` | Fill in the `ssh` section. |
| `[ERROR] SSH connection to ...:22 failed: ...` | Check host, user name and key. Test the connection with the `ssh` command first. |
| `[ERROR] Missing 'batch_ids'. Pass the id printed by make_OCR_batch.py ...` (from `get_OCR_results.py`) | Put the `ollama-...` id into the last lines of `get_OCR_results.py`. |
| `[ERROR] 'process_from' cannot be used with a local provider ...` | Use `batch_ids` instead of `process_from`. |
| The answers are cut off, or the JSON is incomplete | Raise `num_ctx` in `ollama_options`. |
| Everything is very slow | Normal on a computer without a strong GPU. Use a smaller model or a server with a GPU (SSH mode). |

---

## Further reading

- Ollama: <https://ollama.com>, documentation: <https://docs.ollama.com>, command reference: <https://docs.ollama.com/cli>
- Ollama model library: <https://ollama.com/library>, and glm-ocr in particular: <https://ollama.com/library/glm-ocr>
- Running Hugging Face GGUF models with Ollama: <https://huggingface.co/docs/hub/ollama>
- The GLM-OCR model card (supported prompts): <https://huggingface.co/zai-org/GLM-OCR>
- Paramiko (the SSH package): <https://www.paramiko.org>
- The code: `ai_providers/base_ollama.py` (connection, tunnel, run, raw file) and `ai_providers/glm_ocr_provider.py` (a complete model module)

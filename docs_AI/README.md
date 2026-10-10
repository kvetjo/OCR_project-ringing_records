# OCR Universal Project – Documentation

> **Do you know how to read MARKDOWN file?** For all the Markdown files you can use VS Code shortcut `ctrl + shift + v` which will display the content nicely formated. Or you can use online tool to see Markdown.

This project turns **scanned paper bird records** into structured data. It handles ringing sheets, retrap cards and daily sightings sheets.

An AI model reads every scan and returns the content as JSON. The project's own **validators** then do four things:

- check the answer,
- repair what can be repaired,
- match names against reference lists,
- flag every row that a person should look at.

> **New here?** Start with [GETTING_STARTED.md](GETTING_STARTED.md). It walks you through your first complete run, step by step.

---

## Documentation contents

| Page | What it covers | Read it when… |
|---|---|---|
| [README.md](README.md) (this page) | Overview, how the pieces fit together, quick start, basic FAQ, key terms | you want the big picture |
| [GETTING_STARTED.md](GETTING_STARTED.md) | Installation and your first complete run, step by step; a checklist for every new run | you are new, or you set up a new computer |
| [CONFIGURATION.md](CONFIGURATION.md) | Every setting in `config.yaml`, `.env` and `prompts.yaml`: allowed values, defaults and what they do | you need to change a setting or don't understand one |
| [SCRIPTS.md](SCRIPTS.md) | The main scripts: what they do, their modes, the files they write, typical workflows (retry failed images, re-validate for free, …) | you run the pipeline, or you wonder what a file in `data_output/` is |
| [VALIDATORS.md](VALIDATORS.md) | The three built-in validators: every rule, every setting, the meaning of every review reason, the lexicon files | you read a review file or tune the validation rules |
| [OLLAMA_LOCAL_MODELS.md](OLLAMA_LOCAL_MODELS.md) | Free local AI models with Ollama (on this computer or on a server over SSH). Covers finding and installing models, `glm_ocr_provider.py`, and adding a new local model | you want to work without a cloud provider |
| [ADDING_PROVIDERS.md](ADDING_PROVIDERS.md) | Adding a new cloud AI provider, with a complete example for Claude | you want to use an AI service that is not built in |
| [ADDING_VALIDATORS.md](ADDING_VALIDATORS.md) | Writing a validator for a new type of paper form | you want to digitise a new kind of document |
| [EXTRA_TOOLS.md](EXTRA_TOOLS.md) | Helper scripts outside the main workflow: PDF → JPEG converter, JSONL → CSV converter, silent-error detector, Storm Petrel ring-range expander | you prepare scans or post-process results |
| [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) | What every folder and file is for, and how the code fits together | you are looking for a file or want to change the code |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Error messages and their fixes, problem-file reasons, known limitations of the current code | something went wrong |
| `README_Jonas.txt` | The author's short personal notes (a quick start in note form); may be less up to date than these pages | you want a very short summary |

---

## How it works

```
 1. SCANS            batch_1/page_1.jpg, batch_1/page_2.jpg, batch_2/page_1.jpg, ...
                     (a folder on your computer, or a Cloudflare R2 bucket)
                          |
                          |   python make_OCR_batch.py
                          v
 2. AI PROVIDER      Gemini / OpenAI : a "batch job" runs in the cloud (minutes to 24 h)
                     Ollama          : a local model reads the images one by one, right now
                          |
                          |   python get_OCR_results.py
                          v
 3. VALIDATOR        ocr_ringing / ocr_retrap / ocr_sightings
                     checks, repairs and enriches the AI answers
                          |
                          v
 4. OUTPUT           data_output/output/<run_name>/results-<run_name>.jsonl   (all data)
                     data_output/output/<run_name>/review-<run_name>.jsonl    (rows to check by hand)
                          |
                          |   optional
                          v
 5. EXTRA TOOLS      CSV export, silent-error detector, ...
```

**Why are there two scripts?** Cloud *batch jobs* cost less than sending the images one by one, but they are slow.

- The first script sends the images.
- The second script collects the answers later: after a few minutes, or up to a day.

For local models, the first script does all the AI work itself. The second script then only validates.

---

## Quick start

For people who already know Python. Everyone else: [GETTING_STARTED.md](GETTING_STARTED.md).

```bash
pip install -r requirements.txt
```

1. Copy `.env.example` to `.env` and fill in your API key(s).
2. Edit `config.yaml`:
   - `run_settings`: provider, model, prompt and run name.
   - `image_management`: where the scans are.
   - `processing`: which scans to send.
   - `validation`: which validator to use.
3. Run `python make_OCR_batch.py`. It sends the scans and prints a **batch id**.
4. Wait until the batch job is finished. For Gemini you can check with `python list_batches_G.py`.
5. Put the batch id into the last lines of `get_OCR_results.py`, then run `python get_OCR_results.py`.
6. Look into `data_output/output/<run_name>/`.

> Always run the scripts **from the project folder**, the folder that contains `config.yaml`.

---

## What is supported

### AI providers (`run_settings.active_provider`)

| Value | What it is | You need |
|---|---|---|
| `gemini` | Google Gemini Batch API, the provider used most in this project | `GEMINI_API_KEY` in `.env` |
| `openai` | OpenAI Batch API | `OPENAI_API_KEY` in `.env` |
| `ollama` | A local model on this computer, through [Ollama](https://ollama.com) | Ollama installed and the model downloaded; no key |
| `ollama-ssh` | A local model on another computer (a server), reached over SSH | Ollama on the server and the `paramiko` package; no key |

Claude (Anthropic) is **not built in**. [ADDING_PROVIDERS.md](ADDING_PROVIDERS.md) shows how to add it.

### Document types

| Document | Prompt (`run_settings.active_prompt_key`) | Validator (`validation.active_validator`) |
|---|---|---|
| Ringing sheet (23 rows per page) | `PROMPT_4` | `ocr_ringing` |
| Retrap card | `PROMPT_5` | `ocr_retrap` |
| Sightings sheet, older, with handwritten species names | `PROMPT_1` | `ocr_sightings` |
| Sightings sheet, newer, with a pre-printed species list | `PROMPT_2` | `ocr_sightings` |
| Sightings sheet with a confidence level per row (not recommended) | `PROMPT_3` | `ocr_sightings` |

The prompt and the validator must fit together. The prompt tells the AI which JSON shape to return, and the validator expects exactly that shape. See [VALIDATORS.md](VALIDATORS.md).

---

## Basic FAQ

**What do I need to start?**
You need:
- Python 3.11 or newer,
- the packages from `requirements.txt`,
- your scans in the `batch_<N>/page_<M>.jpg` folder layout,
- either an API key (Gemini or OpenAI) or Ollama for local models.

→ [GETTING_STARTED.md](GETTING_STARTED.md)

**Which provider should I choose?**
- **Gemini** is the provider this project has used most, and it is the default in `config.yaml`.
- **OpenAI** works the same way.
- **A local model through Ollama** costs nothing per page and keeps the scans on your computer. But it is much slower, and the quality depends on the model you choose.

→ [OLLAMA_LOCAL_MODELS.md](OLLAMA_LOCAL_MODELS.md)

**Can I run it for free, or offline?**
Yes, with Ollama. Once the model is downloaded, no internet connection is needed, as long as your scans are in a local folder. Expect minutes per page on a normal computer: in this project's test run, `glm-ocr` needed about 7 minutes per ringing sheet.
→ [OLLAMA_LOCAL_MODELS.md](OLLAMA_LOCAL_MODELS.md)

**How long does a batch take?**
Cloud batch jobs run when the provider has free capacity. Gemini and OpenAI aim to finish within 24 hours, and it is often much faster. A local Ollama run starts immediately, and `make_OCR_batch.py` only ends when all images are done.
→ [SCRIPTS.md](SCRIPTS.md#get_ocr_resultspy)

**Where do I find my batch id?**
- `make_OCR_batch.py` prints it: `[SUCCESS] Batch job created: ...`.
- It is also saved in `processed_items.txt`, in the field `batch_job_id`.
- For Gemini, `python list_batches_G.py` lists all your batch jobs. For OpenAI, use the *Batches* page of the OpenAI dashboard.

→ [SCRIPTS.md](SCRIPTS.md#processed_itemstxt)

**Where are my results, and what is the review file?**
They are in `data_output/output/<run_name>/`:
- `results-<run_name>.jsonl` holds all extracted data, one line per scan.
- `review-<run_name>.jsonl` contains only the rows a validator found suspicious, each with its **reasons**. Check those rows against the scan by hand.

→ [VALIDATORS.md](VALIDATORS.md)

**How do I process only a few images?**
- Set `processing.mode: "explicit"` and list the image ids in `allowed_images`, e.g. `["img_3_1", "img_3_2"]`. An image id is `img_<batch>_<page>`.
- Or use `mode: "batches"` with a list of folder names.

Always test a new setup on 2–3 images first.
→ [CONFIGURATION.md](CONFIGURATION.md#processing)

**How do I re-run the validation without paying again?**
1. Download the batch with `mode="full"`. This keeps the untouched AI answers in `data_output/raw/`.
2. Later, run `mode="process_only"` as often as you like. It reads the saved file and never contacts the provider.

→ [SCRIPTS.md](SCRIPTS.md#re-run-the-validation-for-free)

**Why do I see the same records twice?**
Output files are always **added to**, never overwritten. If you process the same batch twice, its records are added twice. Delete or rename the old output files before you process a batch again.
→ [SCRIPTS.md](SCRIPTS.md#output-files-are-appended)

**Some images failed – how do I resend only those?**
Their ids are listed in `data_output/state/problems_to_deal_with-<run_name>.jsonl` (`error_custom_ids`). Resend them with `batch_operation_mode: "try_again_upload"`.
→ [SCRIPTS.md](SCRIPTS.md#retry-only-the-images-that-failed)

**Can I use Claude?**
Not out of the box. A new provider is one new Python file plus a few lines of registration. [ADDING_PROVIDERS.md](ADDING_PROVIDERS.md) contains a complete example for Claude.

**How do I add my own validator or local model?**
- Validator: [ADDING_VALIDATORS.md](ADDING_VALIDATORS.md).
- Local model: [OLLAMA_LOCAL_MODELS.md](OLLAMA_LOCAL_MODELS.md#adding-a-new-local-model).

Questions about errors and problems are answered in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## Key terms

| Term | Meaning |
|---|---|
| **Scan / page / image** | One scanned sheet, e.g. `page_12.jpg`. |
| **Scan folder** (`batch_3`) | A folder of scans. Its number becomes part of the image id. ⚠ This is **not** the same thing as a *batch job* (below); the word "batch" is used for both. |
| **Image id / custom_id** | The id of one scan, built from its path: `batch_3/page_12.jpg` → `img_3_12`. It links requests, results, review rows and log lines together. |
| **Request** | One scan plus the prompt, sent to the AI. |
| **Prompt** | The instructions for the AI, stored in `prompts.yaml`. It says what to read and which JSON shape to return. |
| **Batch file** | A `.jsonl` file with many requests, one per line (in `data_output/batches_out/`). |
| **Batch job / batch id** | The job a cloud provider runs for one uploaded batch file, and its id. Examples: `s2re3y87…` (Gemini), `batch_6a43…` (OpenAI), `ollama-74144dd4…` (local run). |
| **Provider** | The AI service: `gemini`, `openai`, `ollama` or `ollama-ssh`. The code for each one is in `ai_providers/`. |
| **Model module** | Ollama only: a small Python file that tells the pipeline how to work with one specific local model, e.g. `glm_ocr_provider.py`. |
| **Validator** | Code in `validators/` that reads the AI answer, repairs what it can, adds reference data and flags suspicious rows. |
| **Rule** | One check inside a validator, switched on or off in `config.yaml`. |
| **Review reason** | A short code such as `invalid_ring_no` that explains why a row was flagged. |
| **Lexicon** | A reference CSV file in `lexicon/`: species (with wing and weight ranges), ringing places, allowed ring series. |
| **Run name** | The name of the dataset you are processing (`run_settings.run_name`). It appears in folder and file names. |
| **Raw file** | The untouched AI answers for one batch job (in `data_output/raw/`). |
| **Results / review / problems file** | All extracted data / only the flagged rows / technical problems (failed batches, answers that were not valid JSON). |
| **JSON / JSONL** | JSON is a text format for structured data. JSONL ("JSON Lines") is a file with one JSON object per line. |
| **YAML** | The format of `config.yaml` and `prompts.yaml`. The indentation (spaces) matters. |
| **Regex** | "Regular expression": a text pattern. For example, `^\d{3}$` means "exactly three digits". |
| **API key** | A secret password for a cloud provider. It belongs in `.env`, never in `config.yaml`. |
| **"Bob"** | The code's nickname for extra clean-up steps that run after the main validation: merging duplicate review rows and filling in missing species names. |

---

## Secrets

Never write a key or a password into `config.yaml`, because that file is shared through git. The config only contains placeholders such as `"${GEMINI_API_KEY}"`. They are filled in from the `.env` file when a script starts. `.env` is excluded from git. `.env.example` is the template you copy. Details: [CONFIGURATION.md](CONFIGURATION.md#secrets-and-the-env-file).

Two exceptions to be aware of:

- `config-ollama.yaml` does **not** support `${...}` placeholders, and it is **not** excluded from git. Don't write an SSH password into it; use an SSH key file instead.
- `extra_tools/convertor_PDF_to_JPEG.py` has Cloudflare R2 keys written directly into the code. Don't share that file.

---

## Requirements

- Python 3.11 or newer. The project currently runs on 3.13.
- `pip install -r requirements.txt` installs everything the pipeline needs:

| Package | Needed for |
|---|---|
| `PyYAML`, `requests` | everything |
| `google-genai` | Gemini |
| `openai` | OpenAI |
| `rapidfuzz` | validators (fuzzy matching of species and place names) |
| `boto3` | Cloudflare R2 |
| `paramiko` | `ollama-ssh` only |
| `pandas`, `matplotlib`, `numpy` | the silent-error detector in `extra_tools/` |
| `tzdata` | Windows only (time-zone data for the timestamps) |

- Some extra tools need more packages. See [EXTRA_TOOLS.md](EXTRA_TOOLS.md).
- Local models need [Ollama](https://ollama.com/download).

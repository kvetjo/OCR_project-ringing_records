# Getting started

This page takes you from zero to your first finished run. It uses **Gemini**, the provider used most in this project. The steps for OpenAI are the same. If you want a free local model instead, read this page for the general idea, then follow [OLLAMA_LOCAL_MODELS.md](OLLAMA_LOCAL_MODELS.md).

**On this page**

1. [Before you start](#before-you-start)
2. [Step 1 – Install Python and the packages](#step-1--install-python-and-the-packages)
3. [Step 2 – Add your API key](#step-2--add-your-api-key)
4. [Step 3 – Prepare the scans](#step-3--prepare-the-scans)
5. [Step 4 – Choose the prompt and the validator](#step-4--choose-the-prompt-and-the-validator)
6. [Step 5 – Edit config.yaml](#step-5--edit-configyaml)
7. [Step 6 – Send the scans](#step-6--send-the-scans)
8. [Step 7 – Wait for the batch job](#step-7--wait-for-the-batch-job)
9. [Step 8 – Download and validate the results](#step-8--download-and-validate-the-results)
10. [Step 9 – Look at the output](#step-9--look-at-the-output)
11. [Step 10 – Optional next steps](#step-10--optional-next-steps)
12. [Checklist for every new run](#checklist-for-every-new-run)

---

## Before you start

You need:

- a computer with Windows, macOS or Linux,
- **Python 3.11 or newer** (check with `python --version`),
- scanned sheets as `.jpg` or `.png` images (PDF files can be converted, see step 3),
- a **Gemini API key**, or an OpenAI key, or Ollama for a local model.

> **Important:** always open the terminal **in the project folder**, the folder with `config.yaml`, `make_OCR_batch.py` and the other main files, and run the scripts from there. The scripts look for `config.yaml`, `prompts.yaml` and `data_output/` in the *current* folder.

---

## Step 1 – Install Python and the packages

1. Install Python from <https://www.python.org/downloads/>. On Windows, tick **"Add python.exe to PATH"** during the installation.
2. Open a terminal in the project folder. In VS Code: *Terminal → New Terminal*.
3. **Recommended:** create a *virtual environment*. This is a private package folder for this project, so the packages don't get mixed up with other Python projects.

   Windows (PowerShell):
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

   macOS / Linux:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

   When the environment is active, the terminal prompt starts with `(.venv)`. Activate it again every time you open a new terminal. If PowerShell refuses to run `Activate.ps1`, use the classic Command Prompt (`cmd`) and run `.venv\Scripts\activate.bat` instead.

4. Install the packages:
   ```bash
   pip install -r requirements.txt
   ```

---

## Step 2 – Add your API key

1. Copy the file `.env.example` to a new file called **`.env`** in the same folder.
2. Fill in the keys you need, and leave the others empty:
   ```
   GEMINI_API_KEY=your-gemini-key
   OPENAI_API_KEY=
   R2_ACCESS_KEY_ID=
   R2_SECRET_ACCESS_KEY=
   ```
   - Gemini key: Google AI Studio → <https://aistudio.google.com/apikey>
   - OpenAI key: <https://platform.openai.com/api-keys>
   - The two `R2_...` keys are only needed if your scans are stored in Cloudflare R2. You create them as an R2 API token in the Cloudflare dashboard.

`.env` is never committed to git. `config.yaml` only refers to the keys by name (`"${GEMINI_API_KEY}"`). More: [CONFIGURATION.md](CONFIGURATION.md#secrets-and-the-env-file).

---

## Step 3 – Prepare the scans

The pipeline finds and identifies the scans by their **folder and file names**. Use this layout:

```
<root folder>/
├── batch_1/
│   ├── page_1.jpg
│   ├── page_2.jpg
│   └── ...
├── batch_2/
│   ├── page_1.jpg
│   └── ...
└── ...
```

- Every scan gets an **image id** from its path: `batch_2/page_15.jpg` → **`img_2_15`**. This id follows the scan through the whole pipeline: requests, results, review rows and logs.
- The folders may also be called `davka_<N>`, and the separator may be `_` or `-`. The naming rule itself can be changed in `processing.path_parsing` (see [CONFIGURATION.md](CONFIGURATION.md#path_parsing)).
- Number the pages in their real order. Some rules compare a page with the page before it. For example, a ringing sheet's back side has no species name, and it can be taken from `page_<n-1>`.
- Prefer `.jpg`, `.jpeg` or `.png`. `.webp` and `.pdf` are also collected, but images work best, and local Ollama models skip PDF files.
- **Have PDF files?** `extra_tools/convertor_PDF_to_JPEG.py` converts them into exactly this layout. See [EXTRA_TOOLS.md](EXTRA_TOOLS.md#pdf-to-jpeg-converter).

Where to keep the scans:

| Option | Setting in `config.yaml` | Notes |
|---|---|---|
| A folder on your computer | `image_management.active_adapter: "local"` and `local_root` | the simplest option |
| Cloudflare R2 (cloud storage) | `active_adapter: "cloudflare"` and the `cloudflare:` section | public reading must be enabled on the bucket, because the images are downloaded through its public address; the free tier has 10 GB |

---

## Step 4 – Choose the prompt and the validator

The type of document decides which prompt and which validator you need:

| Document | `active_prompt_key` | `active_validator` |
|---|---|---|
| Ringing sheet (23 rows per page) | `PROMPT_4` | `ocr_ringing` |
| Retrap card | `PROMPT_5` | `ocr_retrap` |
| Sightings sheet, older, handwritten species names | `PROMPT_1` | `ocr_sightings` |
| Sightings sheet, newer, pre-printed species list | `PROMPT_2` | `ocr_sightings` |

The texts of the prompts are in `prompts.yaml`. You rarely need to change them. If you do, see [CONFIGURATION.md](CONFIGURATION.md#promptsyaml).

---

## Step 5 – Edit config.yaml

Open `config.yaml`. For a first test, check these keys. Everything else can stay as it is.

```yaml
run_settings:
  active_provider: "gemini"
  model_name: "gemini-3.1-pro-preview"        # models: https://ai.google.dev/gemini-api/docs/models
  active_prompt_key: "PROMPT_4"               # ringing sheets
  run_name: "ringing-test-01"                 # any name; used for folders and file names

image_management:
  active_adapter: "local"
  local_root: "C:\\Users\\me\\scans\\ringing"  # the folder that CONTAINS batch_1, batch_2, ...

batch_generation:
  image_upload_method: "file_api"
  batch_operation_mode: "generate_and_upload"
  upload_files: []

processing:
  mode: "explicit"                            # send only the images listed below
  allowed_images: ["img_1_1", "img_1_2"]

validation:
  active_validator: "ocr_ringing"
  species_lexicon_path: "lexicon/mbs_all_species_with_measurements_v3.csv"
  locations_lexicon_path: "lexicon/mbs_ringing_locations.csv"
  serial_lexicon_path: "lexicon/mbs_series_options.csv"
```

Tips:

- **Start with 2–3 images** (`mode: "explicit"`). It is cheap and shows problems early. When everything works, switch to `mode: "batches"` or `mode: "all"`.
- **Windows paths:** inside double quotes, write `\\` instead of `\`, or use forward slashes (`"C:/Users/me/scans"`).
- The lexicon paths in the delivered `config.yaml` are full paths on the author's computer. The relative paths shown above work as long as you run the scripts from the project folder.
- For ringing sheets, also look at `validation.ocr_ringing.backfill_species` and `ring_no_max_suffix_length` (see [VALIDATORS.md](VALIDATORS.md#ocr_ringing)).
- Every key is explained in [CONFIGURATION.md](CONFIGURATION.md).

---

## Step 6 – Send the scans

```bash
python make_OCR_batch.py
```

What you should see (shortened):

```
[INFO] Run name: ringing-test-01
[INFO] Found 1150 images before filtering
[INFO] Processing 2 images after filtering
[INFO] Uploading image to Gemini File API: C:\Users\me\scans\ringing\batch_1\page_1.jpg
[INFO] Uploading image to Gemini File API: C:\Users\me\scans\ringing\batch_1\page_2.jpg
[INFO] JSONL files created: ['data_output\\batches_out\\ringing-test-01\\batch-ringing-test-01_0001.jsonl']
[INFO] Uploading batch JSONL batch-ringing-test-01_0001.jsonl as 'batch-ringing-test-01_0001' to Gemini...
[INFO] Uploaded file URI: https://generativelanguage.googleapis.com/...
[INFO] Creating batch job 'batch-ringing-test-01_0001-job' for files/...
[SUCCESS] Batch job created: s2re3y87ta6vhxtvh4k92bm9vujuarompdtu
[INFO] Logged 2 requests from batch-ringing-test-01_0001.jsonl to processed_items.txt
[INFO] Script execution completed in 0.35 minutes (which is 21.0 seconds)
[INFO] Batch creation completed
```

**Copy the batch id** (`s2re3y87…` in the example). You need it in step 8. If you lose it, you can find it again in two places:

- `processed_items.txt`, in the field `batch_job_id`,
- the output of `python list_batches_G.py`, which lists all your Gemini batch jobs.

The script may stop early with a clear error, for example when no image matched your filters. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md#make_ocr_batchpy-problems).

---

## Step 7 – Wait for the batch job

A cloud batch job is not processed immediately. Gemini aims to finish within 24 hours, and it is often much faster. To check the state:

- **Gemini:** run `python list_batches_G.py`. You choose what to list in the last lines of the file (see [SCRIPTS.md](SCRIPTS.md#list_batches_gpy)). `JOB_STATE_SUCCEEDED` means the job is done.
- **OpenAI:** open the *Batches* page of your OpenAI dashboard (<https://platform.openai.com/batches>).

You can also simply go on with step 8. A batch that is not finished yet is skipped with `[INFO] Batch is still in progress`, and you run the script again later.

---

## Step 8 – Download and validate the results

`get_OCR_results.py` has no command-line options. You choose what to download in the **last lines of the file**:

```python
if __name__ == "__main__":
    # Example usage - modify as needed
    list_of_batches = ["s2re3y87ta6vhxtvh4k92bm9vujuarompdtu"]   # <- your batch id(s)
    main(batch_ids=list_of_batches, mode="full")
```

`mode="full"` does three things:

- downloads the results,
- keeps an untouched copy of the AI answers in `data_output/raw/`,
- runs the validator.

Thanks to the copy, you can re-run the validation later without paying again. The other modes are explained in [SCRIPTS.md](SCRIPTS.md#modes).

Before you run the script, check that `config.yaml` still describes this run:

- the same `run_name`,
- the same `active_provider`,
- the right `active_validator`.

Then run:

```bash
python get_OCR_results.py
```

It prints a lot of information: every batch job in your account is listed and skipped if it is not in your list. At the end you see `[INFO] Results retrieval completed`.

---

## Step 9 – Look at the output

| File | What it contains |
|---|---|
| `data_output/output/<run_name>/results-<run_name>.jsonl` | All extracted and cleaned data, one line per scan |
| `data_output/output/<run_name>/review-<run_name>.jsonl` | Only the rows that need a human check, each with a list of `reasons` |
| `data_output/state/problems_to_deal_with-<run_name>.jsonl` | Technical problems, for example failed requests or answers that were not valid JSON. The file only appears when something went wrong. |
| `data_output/state/processed_batches.json` | One entry per downloaded batch job: model, times, request counts, tokens |
| `processed_items.txt` | One line per sent image, marked `"results_downloaded": true` once it was processed |

What to do next:

1. **Open the review file.** Every line tells you which scan and which row to check:
   - `custom_id` is the scan: `img_1_2` = `batch_1/page_2`.
   - `id_row` is the row on the sheet.
   - `reasons` says what looks wrong. What each reason means: [VALIDATORS.md](VALIDATORS.md).
2. **If the problems file exists**, go through it: [TROUBLESHOOTING.md](TROUBLESHOOTING.md#problems-file-reasons).
3. Before you process the same batch again, **delete or rename the old output files**. New results are always *added* to the existing files.

JSONL files are plain text. You can open them in VS Code, or convert them to CSV for Excel (next step).

---

## Step 10 – Optional next steps

- **CSV for Excel:** `extra_tools/json2csv_v4.py` → [EXTRA_TOOLS.md](EXTRA_TOOLS.md#jsonl-to-csv-converter).
- **Silent errors (ringing sheets):** these are values that look valid in every single row but don't fit the rest of the dataset, for example a whole card with a misread ring series. See [EXTRA_TOOLS.md](EXTRA_TOOLS.md#silent-error-detector).
- **A second opinion:** you can also give the results file to a strong AI chat model, such as ChatGPT or Claude in a "thinking" mode. Ask it to point out unusual, unexpected or inconsistent values. This is a manual step outside the pipeline.
- **Process everything:** switch `processing.mode` to `"batches"` or `"all"` and repeat steps 6–9.
- **Try a local model:** [OLLAMA_LOCAL_MODELS.md](OLLAMA_LOCAL_MODELS.md).

---

## Checklist for every new run

Before `python make_OCR_batch.py`:

- [ ] `run_settings.run_name`: a new, unique name for this dataset
- [ ] `run_settings.active_provider` and `model_name`
- [ ] `run_settings.active_prompt_key` fits the document type
- [ ] `image_management` points to the right scans: `local_root`, or `r2_bucket_name` + `r2_base_path`
- [ ] `processing.mode` together with `allowed_batches` or `allowed_images`
- [ ] `batch_generation.batch_operation_mode` (usually `generate_and_upload`) and `upload_files` (usually `[]`)

Before `python get_OCR_results.py`:

- [ ] `run_name` and `active_provider` are the same as when the batch was sent
- [ ] `validation.active_validator` fits the document type
- [ ] the validator settings, e.g. `backfill_species` and `ring_no_max_suffix_length` for ringing sheets ([VALIDATORS.md](VALIDATORS.md#ocr_ringing))
- [ ] the batch id(s) and the `mode` in the last lines of `get_OCR_results.py`
- [ ] old output files deleted or renamed if you process a batch again

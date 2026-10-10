# Scripts and the files they produce

This page describes the scripts you run, what they do step by step, and every file they write. Run all of them **from the project folder**.

**On this page**

- [make_OCR_batch.py](#make_ocr_batchpy): send scans to the AI
- [get_OCR_results.py](#get_ocr_resultspy): download, validate, write the output
- [list_batches_G.py](#list_batches_gpy): list your Gemini batch jobs
- [Files written by the pipeline](#files-written-by-the-pipeline)
- [Typical workflows](#typical-workflows)

The helper scripts in `extra_tools/` are described in [EXTRA_TOOLS.md](EXTRA_TOOLS.md).

---

## make_OCR_batch.py

**Purpose:** select scans and send them to the AI provider.

```bash
python make_OCR_batch.py
```

There are no command-line options. Everything comes from `config.yaml`.

### What it does (Gemini and OpenAI)

1. Loads `config.yaml` and `.env`, and prints the run name.
2. Loads the provider named in `run_settings.active_provider` and checks that there is an API key for it.
3. Collects all scans with the configured image adapter (local folder or R2 bucket) and `processing.image_extensions`. It prints `[INFO] Found N images before filtering`.
4. Keeps only the scans selected by `processing.mode`. It prints `[INFO] Processing N images after filtering`.
5. Depending on `batch_generation.batch_operation_mode`:
   - **builds batch files**: one request line per image, made of the image plus the prompt from `prompts.yaml`. With `file_api`, every image is uploaded to the provider first.
   - **uploads** each batch file and creates a batch job for it. It prints `[SUCCESS] Batch job created: <batch id>`.
6. Writes one line per sent image into `processed_items.txt`, but only after a successful upload.
7. Prints how long the run took.

The four modes (`generate_and_upload`, `generate`, `upload`, `try_again_upload`) are explained in [CONFIGURATION.md](CONFIGURATION.md#batch-operation-modes).

### What it does with a local model (ollama / ollama-ssh)

Steps 1–4 are the same: the image adapter and all filters work exactly as for Gemini. After that, no batch file is built and nothing is uploaded. Instead:

- Every image is sent to the local model, one after another.
- Each answer is written straight into `data_output/raw/` as `ollama-<id><name_suffix>_raw.jsonl`.

The script ends with:

```
[SUCCESS] Local batch finished: ollama-9dde8baa311542c0b339275bc7af36a2
[INFO] Raw results: data_output\raw\ollama-9dde8baa311542c0b339275bc7af36a2-my_run_raw.jsonl
[INFO] Next step: in get_OCR_results.py run main(batch_ids=["ollama-9dde8baa311542c0b339275bc7af36a2"])
```

The full description is in [OLLAMA_LOCAL_MODELS.md](OLLAMA_LOCAL_MODELS.md#running-the-whole-workflow-locally).

### When it stops with an error

| Message | Reason |
|---|---|
| `ERROR: API key for provider '...' not found. Check your .env file.` | no key for this provider in `.env`, or no line for it under `api_keys` |
| `[ERROR] batch_operation_mode 'try_again_upload' requires ...` | `try_again_upload` is used without `mode: "explicit"`, without `allowed_images` or without `upload_files` |
| `[ERROR] No JSONL files were generated, so there is nothing to upload.` | the filters matched no image, or every image was skipped (see the `[WARN]` lines above it) |
| `[ERROR] None of the files listed in upload_files were found in ...` | mode `upload`: wrong file name, or a different `run_name` than the one the files were generated with |
| `[ERROR] Unknown batch_operation_mode: ...` | a typo in the mode |

If the upload of one batch file fails, the script prints `[ERROR] Failed to process batch ...` and continues with the next file. The failed file is **not** logged, so you can simply upload it again later with `batch_operation_mode: "upload"`.

More messages: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## get_OCR_results.py

**Purpose:** download finished batch jobs, run the validator, and write the output files.

### How to run it

The settings are in the **last lines of the file**. There are no command-line options.

```python
if __name__ == "__main__":
    # Example usage - modify as needed
    list_of_batches = ["s2re3y87ta6vhxtvh4k92bm9vujuarompdtu"]
    main(batch_ids=list_of_batches, mode="standard")
```

Then run:

```bash
python get_OCR_results.py
```

`main()` takes three parameters:

| Parameter | Meaning |
|---|---|
| `batch_ids` | A list of batch ids to process. Use each id exactly as `make_OCR_batch.py` printed it; for Gemini that is **without** the `batches/` prefix. If you give several ids, all their results go into the same output files. |
| `process_from` | Instead of ids: process every batch job created on or after this date, written as `"DD-MM-YYYY"`. It cannot be used with `process_only` or with local models. |
| `mode` | See [Modes](#modes). The default is `"standard"`. |

⚠ Without `batch_ids` and without `process_from`, **every finished batch job in your provider account** is processed with the current validator. Always give the batch ids.

### Modes

| Mode | Talks to the provider | Saves the raw answers | Runs the validator | Use it when |
|---|---|---|---|---|
| `standard` | yes | no | yes | normal, quick use |
| `full` | yes | yes (`data_output/raw/`) | yes | **recommended**: you can re-validate later without the provider |
| `download_only` | yes | yes | no | you only want to secure the data now |
| `process_only` | **no** | reads them from `data_output/raw/` | yes | re-running the validator after changing rules or lexicons; free, and no API key needed |

- `process_only` needs `batch_ids`. For each id it looks in `raw_dir_base` for a raw file whose name contains the id. If there is no such file, or more than one, that batch is skipped with a warning.
- In `full` and `download_only`, a raw file that already exists is **not downloaded again**. The saved copy is used instead.
- With local providers (`ollama`, `ollama-ssh`) the script always works as `process_only`, whatever mode you pass, because their results are already in `raw_dir_base`:
  ```
  [INFO] Provider 'ollama' runs locally -> results are read from 'data_output/raw' (mode='standard' -> 'process_only')
  ```

### What it does, step by step

1. Loads the config and the provider, and imports the validator named in `validation.active_validator`.
2. Gets the list of all batch jobs from the provider. In `process_only` mode it looks for the raw files instead.
3. For every batch job:
   - skips it if it is not in `batch_ids`, or if it is older than `process_from`;
   - checks its status:

   | Status | What happens |
   |---|---|
   | `completed` | downloaded and processed |
   | `in_progress` | skipped with `[INFO] Batch is still in progress`; run the script again later |
   | `failed` (also *cancelled*, and *expired* at OpenAI) | written to the problems file with the reason `FAILED` |
   | anything else | written to the problems file as `NOT_COMPLETED_STATUS_<STATUS>` |

4. Counts the requests inside the batch that failed, and logs them as `COMPLETED_WITH_ERRORS` together with their ids.
5. Takes the text answer of every successful request and hands all of them **at once** to the validator.
6. Writes the validator's files (results, review, problems). New lines are **added** to existing files.
7. Updates `processed_batches.json` and marks the images in `processed_items.txt` as downloaded.

The script prints the full batch job object for every batch it processes. This is normal.

### Output files are appended

All output files are opened in *append* mode: new lines are added at the end, nothing is overwritten. This is intentional, so several batch jobs can be collected into one results file. The downside: **processing the same batch twice adds its records twice.** Before you process a batch again, delete or rename the old results and review files. `processed_batches.json` shows each download time, so you can see when a batch was processed more than once.

---

## list_batches_G.py

Lists your **Gemini** batch jobs with their state. It uses `GEMINI_API_KEY` from `.env`.

You choose what to list in the last lines of the file. Uncomment the part you need:

```python
# all batch jobs created on or after a date (DD-MM-YYYY)
march_batches = get_filtered_batches(from_date="01-09-2026")

# the last 3 batch jobs
all_jobs = get_filtered_batches(limit=3)

# one batch job by its id
specific_job = get_filtered_batches(batch_id="l6br839grymq67vllkxqhjpe3w27cg7de307")
```

`print_description_of_batches(b, simple=False)` prints the details, including the progress:

```
Name: batch-ringing-test-01_0001-job
  ID: batches/s2re3y87ta6vhxtvh4k92bm9vujuarompdtu
  State: JOB_STATE_SUCCEEDED
  Created: 2026-09-02 08:08:01
  Progress: 91/91 (Failed: 0)
----------------------------------------
```

- `JOB_STATE_SUCCEEDED` means done. `JOB_STATE_PENDING` and `JOB_STATE_RUNNING` mean the job is still waiting or working.
- The list shows the id with a `batches/` prefix. Pass it to `get_OCR_results.py` **without** that prefix.
- There is no such helper for OpenAI. Use the *Batches* page of the OpenAI dashboard.

---

## Files written by the pipeline

| File | Written by | Contents |
|---|---|---|
| `data_output/batches_out/<run_name>/batch-<run_name>_0001.jsonl` | `make_OCR_batch.py` | the requests sent to the provider, one per line |
| `data_output/raw/<batch id><name_suffix>_raw.jsonl` | `get_OCR_results.py` (`full`, `download_only`); `make_OCR_batch.py` for Ollama | the untouched AI answers of one batch job |
| `data_output/output/<run_name>/results-<run_name>.jsonl` | `get_OCR_results.py` | all extracted and validated data, one line per scan |
| `data_output/output/<run_name>/review-<run_name>.jsonl` | `get_OCR_results.py` | only the rows that need a human check |
| `data_output/state/problems_to_deal_with-<run_name>.jsonl` | `get_OCR_results.py` | technical problems |
| `data_output/state/processed_batches.json` | `get_OCR_results.py` | one entry per processed batch job |
| `processed_items.txt` | both main scripts | one line per sent image |

### Results and review files

- **Results:** one line per scan. It contains everything the AI read, cleaned and enriched by the validator, plus `custom_id` (the image id) and `batch_id`.
- **Review:** one line per suspicious row (or per card, for retrap cards). It contains `custom_id`, `batch_id`, the row itself (`species_entry` or `record_entry`), the list `reasons` and a `timestamp`.

The exact shape depends on the validator. Examples and the meaning of every reason: [VALIDATORS.md](VALIDATORS.md).

### Raw files

- **Name:** `<batch id><name_suffix>_raw.jsonl`, e.g. `s2re3y87ta6vhxtvh4k92bm9vujuarompdtu-ringing-subalpine_warblers-full_raw.jsonl`.
- **Content:** one line per request, exactly as the provider returned it. For OpenAI, the successful and the failed requests are combined into one file. For Ollama, the pipeline writes its own format (see [OLLAMA_LOCAL_MODELS.md](OLLAMA_LOCAL_MODELS.md#the-raw-file-of-a-local-run)).
- **Don't rename raw files.** `process_only` finds them by looking for the batch id in the file name.

### processed_batches.json

One entry per batch id. It is written after a batch was processed successfully.

```json
"5mb7eodykpddkvv63n6b4w8wfwuv6r0tc81c": {
  "batch_id": "5mb7eodykpddkvv63n6b4w8wfwuv6r0tc81c",
  "display_name": "ringing-subalpine_warblers-test1",
  "model": "gemini-3.1-pro-preview",
  "provider": "gemini",
  "active_validator": "ocr_ringing",
  "processing_time_minutes": 3.48,
  "date_and_time_created": "02-09-2026 : 10-07-58",
  "date_and_time_completed": "02-09-2026 : 10-11-27",
  "date_and_time_downloaded": ["02-09-2026 : 10-22-47"],
  "completed_requests": 3,
  "failed_requests": 0,
  "tokens_consumption": { "input_tokens": 7383, "output_tokens": 9740, "total_tokens": 17123 }
}
```

- `display_name` is the run name (`name_suffix` without the leading `-`).
- `date_and_time_downloaded` is a **list**: every processing run adds a time. Processing a batch again is allowed; nothing is skipped because of this file.
- In `process_only` mode, and for local models, there is no batch job to ask. The times, request counts and tokens are therefore `null`, and `model` comes from `config.yaml`.
- All times use the Europe/Malta time zone and the format `DD-MM-YYYY : HH-MM-SS`.

### processed_items.txt

An **information-only** log, one JSON object per line. Nothing in the pipeline reads it to decide what to do. It exists so you can look up what happened to a particular scan.

`make_OCR_batch.py` writes one line per **actual request**, read back from the batch file that was sent. An image that was skipped never appears. A line is written **only after the batch really reached the provider**, so every line has a `batch_job_id`:

- `batch_operation_mode: "generate"` writes nothing, because no batch job exists yet.
- A failed upload writes nothing either.

```json
{
  "custom_id": "img_3_1",
  "batch_file": "batch-ringing-shearwater-allowed-s_0001.jsonl",
  "batch_job_id": "vhvel2szothjgnvr9yxsx0z3r3xori28rfdc",
  "provider": "gemini",
  "model": "gemini-3.1-pro-preview",
  "validator": "ocr_ringing",
  "image_upload_method": "file_api",
  "uploaded": true,
  "uploaded_at": "22-07-2026 : 15-11-05",
  "results_downloaded": false,
  "results_downloaded_at": null
}
```

`get_OCR_results.py` later sets `results_downloaded` to `true` and fills in `results_downloaded_at` for the images whose answers came back and went through the validator.

| Field | Meaning |
|---|---|
| `batch_file` | the batch file the request was in (for Ollama: the raw file) |
| `batch_job_id` | the provider's batch id, always filled in |
| `uploaded` | always `true`, because only successful uploads are logged |
| `validator` | the validator from the config, updated to the one actually used |
| `results_downloaded` | were the results downloaded **and** validated? |

Lines written by older versions of the pipeline are kept unchanged. Some of them have `"batch_job_id": null`; those can never be matched and stay `results_downloaded: false`.

### The problems file

`data_output/state/problems_to_deal_with<name_suffix>.jsonl` collects technical problems, one JSON object per line. The file is only created when there is a problem. There are two kinds of lines.

A problem with a whole batch job:

```json
{"batch_id": "batch_6a43c3a870d88190a2a9303a2331c390", "status": "failed", "reason": "FAILED", "error_count": 0, "timestamp": "2026-07-08T09:07:32.544115Z"}
```

When requests failed, the line also has `"error_custom_ids": ["img_9_43", ...]`.

A problem with one answer, written by the validator:

```json
{"custom_id": "img_1_7", "batch_id": "s2re3y87...", "reason": "JSON_PARSE_ERROR", "raw_text": "<the first 500 characters of the answer>", "timestamp": "..."}
```

What each reason means and what to do: [TROUBLESHOOTING.md](TROUBLESHOOTING.md#problems-file-reasons).

---

## Typical workflows

### Normal cloud run

1. `config.yaml`: new `run_name`, provider, prompt, images, `batch_operation_mode: "generate_and_upload"`.
2. `python make_OCR_batch.py`, then note the batch id.
3. Wait. Check with `python list_batches_G.py` (Gemini).
4. In `get_OCR_results.py`: `main(batch_ids=["<id>"], mode="full")`, then run `python get_OCR_results.py`.
5. Check the review file and the problems file.

### Re-run the validation for free

You changed a rule, a threshold or a lexicon, and you want to apply the change to data you already have.

1. The raw file must exist in `data_output/raw/`. It exists if you downloaded with `full` or `download_only`, and for every Ollama run.
2. Delete or rename the old results and review files (they would be appended to).
3. `main(batch_ids=["<id>"], mode="process_only")`, then run `python get_OCR_results.py`.

No API call is made and no API key is needed.

### Download now, validate later

1. `main(batch_ids=["<id>"], mode="download_only")` saves the raw answers only.
2. Later: `main(batch_ids=["<id>"], mode="process_only")`.

### Retry only the images that failed

1. Find the ids of the failed images, for example in the problems file (`error_custom_ids`, or the `custom_id` of `JSON_PARSE_ERROR` lines).
2. In `config.yaml`:
   ```yaml
   run_settings:
     run_name: "<the same run name as the original run>"
   batch_generation:
     batch_operation_mode: "try_again_upload"
     upload_files: ["batch-<run_name>_0001.jsonl"]   # the original batch file
   processing:
     mode: "explicit"
     allowed_images: ["img_9_43", "img_8_100"]
   ```
3. `python make_OCR_batch.py`. A copy `batch-<run_name>_0001-err.jsonl` with only these requests is created and uploaded. Note the **new** batch id.
4. Process the new batch id with `get_OCR_results.py`. Because the run name is the same, the results are added to the same output files.

⚠ With Gemini and `file_api`, this only works within **48 hours** of the first upload, because Gemini deletes uploaded images after that time. Later, send the images again as a new batch instead:

- `mode: "explicit"` with the ids,
- `batch_operation_mode: "generate_and_upload"`,
- a different `output_prefix` (e.g. `"batch-{run_name}-retry"`), so the original batch file is not overwritten.

### Generate now, upload later

1. `batch_operation_mode: "generate"`, then run `python make_OCR_batch.py`. The batch files appear in `data_output/batches_out/<run_name>/`, and you can inspect them.
2. Later, with the **same** `run_name`: `batch_operation_mode: "upload"` and `upload_files: ["batch-<run_name>_0001.jsonl"]`, then run `python make_OCR_batch.py`.

### Local model run

See [OLLAMA_LOCAL_MODELS.md](OLLAMA_LOCAL_MODELS.md#running-the-whole-workflow-locally).

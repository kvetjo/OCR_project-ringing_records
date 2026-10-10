# Troubleshooting

This page lists the error and warning messages you may see, what causes them, and how to fix them. It also explains the problems file and lists the known limitations of the current code. General questions are answered in the [basic FAQ in the README](README.md#basic-faq).

**On this page**

- [First checks](#first-checks)
- [Setup and configuration](#setup-and-configuration)
- [make_OCR_batch.py problems](#make_ocr_batchpy-problems)
- [get_OCR_results.py problems](#get_ocr_resultspy-problems)
- [Problems-file reasons](#problems-file-reasons)
- [Messages printed in Czech](#messages-printed-in-czech)
- [Local models (Ollama)](#local-models-ollama)
- [Questions about problems](#questions-about-problems)
- [Known limitations](#known-limitations)

---

## First checks

Most problems are one of these:

1. The terminal is **not in the project folder**. Run `cd` into the folder that contains `config.yaml`.
2. The **virtual environment is not active** (no `(.venv)` at the start of the prompt), so packages are "missing".
3. `config.yaml` was **not saved**, or you edited a preset file (`config-ringing.yaml`) instead of `config.yaml`.
4. `run_name`, `active_provider` or `active_validator` still belong to a **previous run**.
5. The batch id in `get_OCR_results.py` is **wrong** (for Gemini it must be given without `batches/`).

---

## Setup and configuration

| Message | Cause | Fix |
|---|---|---|
| `FileNotFoundError: ... 'config.yaml'` | The script was started from another folder. | Run it from the project folder. |
| `ERROR: File 'prompts.yaml' does not exist. Please check the file path.` | Same, or a wrong `prompts_file_path`. | Run it from the project folder; check the path. |
| `ModuleNotFoundError: No module named 'yaml'` (or `google`, `openai`, `rapidfuzz`, `boto3`, …) | The packages are not installed in this Python. | Activate the virtual environment; run `pip install -r requirements.txt`. |
| `ZoneInfoNotFoundError: 'No time zone found with key Europe/Malta'` | Windows has no time-zone data. | `pip install tzdata` |
| `yaml.scanner.ScannerError` … *escape character* … | A Windows path with single backslashes inside double quotes, e.g. `"C:\Users\me"`. | Write `"C:\\Users\\me"` or `"C:/Users/me"`. Put regular expressions in single quotes. |
| `[WARNING] No .env file found at ...` | There is no `.env` file. | Copy `.env.example` to `.env` and fill it in. |
| `[WARNING] Environment variable 'X' is not set but is used in the config. Using an empty value.` | `config.yaml` uses `${X}`, but `.env` has no line `X=...`. | Add the line to `.env`. |
| `ERROR: API key for provider 'gemini' not found. Check your .env file.` / `... not found in config.yaml` | There is no key: either no line for the provider under `api_keys`, or its variable is empty. | Check `api_keys.<provider>` and `.env`. The key under `api_keys` must be named exactly like `active_provider`. |
| `ERROR: The config uses the {run_name} placeholder, but run_settings.run_name is empty.` | `run_name` is empty. | Fill in `run_settings.run_name`. |
| `ERROR: Prompt key 'PROMPT_6' was not found in the file.` and `Available prompts are: [...]` | `active_prompt_key` does not exist. | Use one of the listed keys. |
| `WARNING: 'system_prompt' or 'user_prompt' is missing/empty for key '...'` | One part of the prompt is empty. | Check `prompts.yaml`. For special prompts of local models this can be intentional. |
| `ValueError: Unknown provider: X. Available providers: gemini, openai, ollama, ollama-ssh ...` | A typo in `active_provider`, or the provider is not registered. | Correct the name; see [ADDING_PROVIDERS.md](ADDING_PROVIDERS.md). |
| `NotImplementedError: Provider 'X' does not fulfil the provider interface - missing functions: [...]` | A new provider's dictionary in `provider_selector.py` is incomplete. | Add the listed functions. |
| `ValueError: Unknown image adapter: X` | `image_management.active_adapter` is not `local` or `cloudflare`. | Correct it. |

---

## make_OCR_batch.py problems

| Message / symptom | Cause | Fix |
|---|---|---|
| `[INFO] Found 0 images before filtering` | No scans were found. | `local`: `local_root` must be the folder that **contains** the `batch_*` folders. `cloudflare`: check the bucket name, `r2_base_path` and the R2 keys. Also check `image_extensions`. |
| `[INFO] Processing 0 images after filtering` | The filter matched nothing, or the paths don't follow the `batch_<N>/page_<M>` naming (such scans are dropped without a message). | `allowed_batches` must be exact folder names (`"batch_10"`); `allowed_images` must be ids like `"img_10_5"`; check `processing.mode`. Rename the folders/files, or adapt `processing.path_parsing`. |
| `[WARN] Skipping <path>: Invalid IMAGE_UPLOAD_METHOD ...` | `pass_a_url` with Gemini, or a typo in `image_upload_method`. | Use `file_api` (see [CONFIGURATION.md](CONFIGURATION.md#image-upload-methods)). |
| `[WARN] Skipping <path>: 404 Client Error: Not Found for url: ...` | Cloudflare: the image cannot be downloaded from `public_base_url`. | Enable public access on the bucket; check `public_base_url`. |
| `[ERROR] Item too large (single line exceeds limit): <path>` | One request is bigger than the batch file limit (with `base64`). | Use `file_api`, or make the images smaller. |
| `[ERROR] No JSONL files were generated, so there is nothing to upload.` | The filter matched nothing, or every image was skipped. | Read the `[WARN]` lines above it. |
| `[ERROR] None of the files listed in upload_files were found in ...` | Mode `upload`: a wrong file name, or `run_name` is not the one the files were generated with. | Check the name in `data_output/batches_out/<run_name>/`. |
| `[WARN] File not found: <name> (searched in ... and subdirectories)` | Mode `try_again_upload`: the same as above. | Use the original `run_name`, and the exact file name. |
| `[ERROR] batch_operation_mode 'try_again_upload' requires ...` | `processing.mode` is not `explicit`, or `allowed_images` / `upload_files` is empty. | Set all three ([SCRIPTS.md](SCRIPTS.md#retry-only-the-images-that-failed)). |
| `[ERROR] Failed to process batch <file>: ...` followed by `Nothing logged for <file> - it never reached the API.` | The upload or the job creation failed: network, key, quota, or a provider error. | Fix the cause shown, then upload the same file with `batch_operation_mode: "upload"`. |
| `[WARN] Unknown processing mode: X` | A typo in `processing.mode`. | Use `all`, `batches` or `explicit`. |

---

## get_OCR_results.py problems

| Message / symptom | Cause | Fix |
|---|---|---|
| Only `[INFO] Skipping batch ... (not in batch_id filter)` lines, nothing processed | The batch id is not in the list the provider returned. | Check the id: for Gemini without `batches/`. Check that `active_provider` is the provider the batch was sent to, and that the API key belongs to the same account. |
| `[INFO] Batch is still in progress` | The batch job is not finished. | Run the script again later. |
| `[WARNING] Batch X has bad type 'unknown', writing it into problems.jsonl` | An unusual state, e.g. a Gemini batch that expired after 48 hours. | Check the batch with `list_batches_G.py` or in the dashboard; send the images again. |
| `ERROR: Unknown validator 'X'` | `active_validator` is misspelled, or it is a new validator that is not registered. | Correct the name, or add the validator to `get_OCR_results.py` ([ADDING_VALIDATORS.md](ADDING_VALIDATORS.md)). |
| `[ERROR] Validator process_batch failed: ...` and a Python error | The validator crashed; the batch is logged as `VALIDATOR_FAILED`. | Read the error. A common cause is a wrong lexicon path (`FileNotFoundError`). After fixing it, run again, ideally with `process_only`. |
| `[WARNING] Error while loading lexicon (...)` | The species lexicon could not be read, so the ringing validator continues without it. | Fix `species_lexicon_path`. Otherwise every row gets `sp_low_match_score`. |
| `[DEBUG] Skipping error record: img_x \| Reason: [...] ...` | This request failed at the provider. | It is listed in the problems file (`COMPLETED_WITH_ERRORS`); resend it ([SCRIPTS.md](SCRIPTS.md#retry-only-the-images-that-failed)). |
| `[DEBUG] No output_text found for request: img_x` and `-> Reason for missing text (finishReason): MAX_TOKENS` (or `SAFETY`, …) | The AI returned no text for this image. | Such images appear **only** in the console. In `processed_items.txt` they stay `"results_downloaded": false`. Resend them. |
| `[WARNING] There is a PROBLEM with a JSON which was not possible to decode:` | A broken line in the downloaded data. | That line is skipped. Resend the image if it is missing. |
| `[ERROR] Cannot run mode='process_only' together with 'process_from' parameter...` | `process_only` cannot filter by date. | Use `batch_ids`. |
| `[ERROR] Missing 'batch_ids'. When using mode='process_only', ...` | `process_only` needs the ids. | Add `batch_ids`. |
| The same records appear twice in the results | The output files are appended to. | Delete or rename the old output files before processing a batch again. |

---

## Problems-file reasons

The problems file is `data_output/state/problems_to_deal_with<name_suffix>.jsonl` ([SCRIPTS.md](SCRIPTS.md#the-problems-file)).

| `reason` | Meaning | What to do |
|---|---|---|
| `FAILED` | The whole batch job failed, was cancelled, or (OpenAI) expired. | Send the images again. |
| `NOT_COMPLETED_STATUS_<STATUS>` | The batch job has an unexpected state. `NOT_COMPLETED_STATUS_UNKNOWN` is also what an expired Gemini batch produces. | Check it in the provider's dashboard; send the images again. |
| `COMPLETED_WITH_ERRORS` | The batch finished, but some requests failed. Their ids are in `error_custom_ids`. | Resend those ids with `try_again_upload`. |
| `DOWNLOAD_FAILED` | The results could not be downloaded. | Run `get_OCR_results.py` again. |
| `COMPLETED_NO_OUTPUT` | The batch finished but contained no answers. | Check the batch in the dashboard; send the images again. |
| `PARSE_RAW_FAILED` | The raw file could not be read. | Check or delete the raw file; download again. |
| `VALIDATOR_FAILED` | The validator crashed. `error_custom_ids` lists all images of that batch. | Fix the cause (see the error printed in the console) and process again, ideally with `process_only`. |
| `JSON_PARSE_ERROR` (ringing, retrap) / `JSON_PARSE_FAILED` (sightings) | The answer for one image was not valid JSON. `raw_text` shows its start. | Resend the image. If it happens often, check the prompt, or the model module for local models. |

---

## Messages printed in Czech

A few messages, mostly in `get_OCR_results.py`, are printed in Czech. What they mean:

| Message (start) | Meaning |
|---|---|
| `[WARNING] V '...' není žádný raw soubor obsahující batch_id '...', přeskakuji` | There is no raw file containing this batch id in the raw folder; the batch is skipped. Check the id, and whether the raw file exists (it only exists after `full`/`download_only`, or after an Ollama run). |
| `[WARNING] batch_id '...' odpovídá více raw souborům (...) - nejasné, který zpracovat, přeskakuji` | Several raw files contain this batch id; it is unclear which one to use, so it is skipped. Keep only one of them. |
| `[INFO] raw_dir_base '...' neexistuje, není co zpracovávat` | The raw folder does not exist, so there is nothing to process. |
| `[ERROR] Nepodařilo se naparsovat raw soubor ...` / `... stažená raw data pro batch ...` | The raw file / the downloaded raw data could not be read (`PARSE_RAW_FAILED`). |
| `[ERROR] Provider '...' neimplementuje '...' (potřeba pro mode='...')` | The provider does not have a function that this mode needs. |
| `[INFO] Raw soubor pro batch ... už existuje, stahování přeskočeno` | The raw file already exists, so the download is skipped. This is normal. |
| `[INFO] Raw data pro batch ... uložena do ...` | The raw data was saved to the file shown. This is normal. |
| `Progress: Nedostupné (REST chyba ...)` / `(Chyba spojení: ...)` (`list_batches_G.py`) | The progress is not available: REST error, or connection error. |
| `[INFO] Načteno N lokací z lexikonu.` / `[WARNING] Chyba pri nacitani lexikonu lokací` (ringing validator) | N places were loaded from the locations lexicon / the locations lexicon could not be loaded. |
| In the reason `backfill_ring_discontinuity: ...` (with `verbose: true`): `předchozí sken neobsahuje čísla kroužků`, `aktuální sken neobsahuje čísla kroužků`, `nelze číst číslo kroužku` | The previous scan has no ring numbers / this scan has no ring numbers / a ring number cannot be read. |

---

## Local models (Ollama)

All messages related to Ollama and SSH are explained in [OLLAMA_LOCAL_MODELS.md](OLLAMA_LOCAL_MODELS.md#troubleshooting-local-models). The most common ones:

- `[ERROR] Cannot reach Ollama at http://localhost:11434` → start Ollama.
- `ERROR: Model 'x' is not installed on the Ollama server` → `ollama pull x`, or correct `model_name`.
- `[WARN] Model 'x' has no entry in config-ollama.yaml -> model_providers.` → add the model to `model_providers`.

---

## Questions about problems

**Almost every row in the review file has `sp_low_match_score`. Why?**
The species names are not found in the species lexicon. Possible causes:
- The lexicon path is wrong (look for a warning in the console).
- All `rules` are switched off, so the lexicon is not loaded.
- The pages have no species name and `backfill_species` is off.
- The names are written in a way the lexicon does not know. Add them to `Alternate_Names`.

**Many rows have `loc_not_matched`.**
Add the spellings used on the sheets to `Alternative_names` in `lexicon/mbs_ringing_locations.csv`. You can also lower `location_match_threshold` a little, but that increases the risk of wrong matches.

**Many rows have `invalid_ring_no`.**
- Check `ring_no_max_suffix_length` (3 for `.012`, 4 for `.2329`).
- In `MANUAL` mode, check `ring_no_manual_regex`.
- With `verbose: true`, the pattern used for each page is printed.

**Every page has `wrong_row_count`.**
The AI returned more or fewer than 23 rows. For other forms, switch off the `23_rows` rule.

**The results contain a line with `"raw"` instead of the data.**
The answer for that scan was not valid JSON. See `JSON_PARSE_ERROR` above.

**I changed a rule. How do I apply it to data I already have?**
Use `process_only` with the batch id. This needs the raw file, so download with `mode="full"` in the future. See [SCRIPTS.md](SCRIPTS.md#re-run-the-validation-for-free).

**Which images of a batch have no result?**
In `processed_items.txt`, look for the lines of that `batch_job_id` that still have `"results_downloaded": false`.

**Excel shows strange characters in the CSV.**
Import the file as UTF-8: *Data → From Text/CSV*.

**PowerShell does not let me activate the virtual environment.**
Use the Command Prompt (`cmd`) and run `.venv\Scripts\activate.bat`.

---

## Known limitations

These are limitations of the **current code**. They are documented here so they don't surprise you; the code itself has not been changed.

**Running the scripts**

- `get_OCR_results.py` has no command-line options: the batch ids and the mode are edited in its last lines.
- New validators must be added by hand to the `if/elif` list in `get_OCR_results.py`.
- Output files are always appended to, so processing a batch twice duplicates its records.
- If `name_suffix` is missing from the config, `get_OCR_results.py` uses the old value `-FB_scraping`.
- Some console messages are printed in Czech ([translations above](#messages-printed-in-czech)).
- `list_batches_G.py` only works for Gemini.
- `config-ringing.yaml` and `config-sightings.yaml` are never read by the scripts.
- The comment in `config.yaml` lists `PROMPT_6`, which does not exist in `prompts.yaml`.

**Providers**

- **OpenAI + `base64`** does not work: the plain base64 text is sent instead of a `data:image/...;base64,...` address. Use `file_api`.
- **Gemini + `pass_a_url`** is not supported: every image is skipped.
- An **expired Gemini batch** (not finished within 48 hours) is reported as state `unknown` (`NOT_COMPLETED_STATUS_UNKNOWN`), because the list of Gemini states has no entry for "expired".
- Images whose answer was empty (e.g. stopped by the model's token limit or safety filter) are only reported in the console, not in the problems file.

**Validators**

- `ocr_ringing-STORM_PETREL.py` is not used. The value `ocr_ringing-STORM_PETREL` runs `ocr_ringing.py` ([VALIDATORS.md](VALIDATORS.md#variant-files)).
- The `low_match_score` switch has no effect in `ocr_ringing` and `ocr_retrap`: an unmatched species name is always flagged.
- Fixed dates in the code:
  - `ocr_retrap` flags every date after 2020,
  - `ocr_sightings` flags dates outside 01-01-1930 … 01-06-2026.
- The `sudden_change` defaults in the code differ from the values the comment in `config.yaml` calls "the default values".
- The skip word `Wader` in `ocr_sightings` never matches, because it is written with a capital letter.

**Local models**

- `ollama-ssh` is marked as not fully tested.
- A fixed `postprocess_output()` is not applied to raw files that were already written.
- `glm_ocr_provider.py` only converts ringing sheets.
- Resuming an interrupted Ollama run also skips the images that failed.

**Extra tools**

- The paths are written into the code and must be edited.
- `convertor_PDF_to_JPEG.py` contains Cloudflare keys in the code.
- `json2csv_v4.py` does not support sightings results.

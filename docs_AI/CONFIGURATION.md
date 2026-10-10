# Configuration reference

This page explains every setting. The settings live in four files:

| File | Contents |
|---|---|
| `config.yaml` | all normal settings (no secrets) |
| `.env` | the secrets: API keys and passwords |
| `prompts.yaml` | the instructions sent to the AI |
| `config-ollama.yaml` | settings for local models only, explained in [OLLAMA_LOCAL_MODELS.md](OLLAMA_LOCAL_MODELS.md#config-ollamayaml) |

**On this page**

- [How the configuration is loaded](#how-the-configuration-is-loaded)
- [YAML in one minute](#yaml-in-one-minute)
- [Secrets and the .env file](#secrets-and-the-env-file)
- [run_settings](#run_settings)
- [The run name placeholder](#the-run-name-placeholder)
- [api_keys](#api_keys)
- [image_management](#image_management)
- [directories](#directories)
- [batch_generation](#batch_generation)
- [processing](#processing)
- [validation](#validation)
- [prompts.yaml](#promptsyaml)
- [config-ollama.yaml](#config-ollamayaml)
- [Preset config files](#preset-config-files)

---

## How the configuration is loaded

- Both main scripts always read **`config.yaml` from the current folder**. There is no option to choose another file, so run the scripts from the project folder.
- While the file is loaded, two kinds of placeholders are replaced:

| Placeholder | Replaced with | Example |
|---|---|---|
| `${NAME}` | the value of `NAME` from `.env`, or from the system environment | `"${GEMINI_API_KEY}"` |
| `{run_name}` | the value of `run_settings.run_name` | `"data_output/output/{run_name}"` |

- If a setting is missing from the file, the code uses a built-in default. The tables below list those defaults. It is still best to keep every setting in the file, so you can see what is used.
- `config.yaml` is tracked by git, so never put a secret into it.

---

## YAML in one minute

```yaml
section:                  # a section; the keys below it are indented
  key: "text value"       # text - quotes are the safest choice
  number: 50000           # numbers without quotes
  switch: true            # true / false
  nothing: null           # "no value"
  short_list: ["a", "b"]  # a list
  long_list:              # the same kind of list, written vertically
    - "a"
    - "b"
```

- Indentation is done with **spaces**, never tabs. It shows which section a key belongs to.
- `#` starts a comment. Everything after it on the line is ignored.
- **Windows paths** inside double quotes need doubled backslashes: `"C:\\Users\\me\\scans"`. Forward slashes work too: `"C:/Users/me/scans"`.
- **Regular expressions** (text patterns) should be written in **single quotes**, e.g. `'^\d{3}$'`. Inside single quotes a backslash is kept exactly as written.
- YAML does not calculate. Write `209715200`, not `200 * 1024 * 1024`.

---

## Secrets and the .env file

```yaml
# config.yaml
api_keys:
  gemini: "${GEMINI_API_KEY}"
```

```
# .env  (in the project folder, never committed to git)
GEMINI_API_KEY=AIza...
```

Rules:

- `.env` is read from the project folder. Lines starting with `#` are comments. Quotes around a value are removed.
- A variable that is already set in your system environment **wins** over `.env`. This lets you test another key without editing the file.
- A placeholder whose variable does not exist is replaced with an empty text. The script then prints `[WARNING] Environment variable 'X' is not set but is used in the config.` If it was an API key, the script stops a moment later with `API key for provider ... not found`.
- Placeholders work in every text value of `config.yaml`, not only in `api_keys`.
- Placeholders do **not** work in `config-ollama.yaml`.
- `.env.example` is the template. When you add a new secret, add its name (without the value) to `.env.example` too.

Variables used today: `GEMINI_API_KEY`, `OPENAI_API_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`.

---

## run_settings

```yaml
run_settings:
  active_provider: "gemini"
  model_name: "gemini-3.1-pro-preview"
  active_prompt_key: "PROMPT_4"
  prompts_file_path: "prompts.yaml"
  run_name: "ringing-subalpine_warblers-full"
```

| Key | Allowed values | Default if missing | Meaning |
|---|---|---|---|
| `active_provider` | `gemini`, `openai`, `ollama`, `ollama-ssh` | `gemini` | Which AI service is used. The name must be registered in `ai_providers/provider_selector.py`. Cloud providers also need a line with the same name under `api_keys`. |
| `model_name` | a model name of that provider | `gemini-3.1-pro-preview` | Gemini models: <https://ai.google.dev/gemini-api/docs/models>. OpenAI models: <https://platform.openai.com/docs/models>. For Ollama, the exact name shown by `ollama list`. |
| `active_prompt_key` | a key from `prompts.yaml` (`PROMPT_1` … `PROMPT_5`) | `PROMPT_2` | Which prompt is sent with every image. An Ollama model module can replace it with its own prompt. |
| `prompts_file_path` | a file path | `prompts.yaml` | Where the prompts are. |
| `run_name` | any text that is allowed in a file name | none | The name of this dataset. It fills every `{run_name}` placeholder. |

> The comment in `config.yaml` also mentions `PROMPT_6`, but that key does **not** exist in `prompts.yaml`. If you use it, the script stops and prints the list of available prompts.

---

## The run name placeholder

To start a new dataset you change **one** value: `run_settings.run_name`. Every setting that contains `{run_name}` follows it:

```yaml
run_settings:
  run_name: "ringing-willow_warblers"

directories:
  output_dir_base: "data_output/output/{run_name}"     # -> data_output/output/ringing-willow_warblers
  batch_out_dir: "data_output/batches_out/{run_name}"  # -> data_output/batches_out/ringing-willow_warblers
  name_suffix: "-{run_name}"                           # -> -ringing-willow_warblers

batch_generation:
  output_prefix: "batch-{run_name}"                    # -> batch-ringing-willow_warblers
```

Rules:

- Only the exact text `{run_name}` is replaced. Other braces, such as `\d{3}` inside a regular expression, are never touched.
- It works in any text value of the config, not only in the four keys above.
- To name one value by hand, simply remove `{run_name}` from that value and write what you need. Everything else keeps following the run name.
- If `{run_name}` is used somewhere but `run_name` is empty, the script stops with an error. It does not create a folder literally called `{run_name}`.
- The script prints the name when it starts: `[INFO] Run name: ...`.
- `state_dir_base`, `raw_dir_base` and `processed_log_file` are deliberately **shared** by all runs. The files inside them are told apart by `name_suffix` or by the batch id.
- `upload_files` is written out in full on purpose, because there you usually point to an older batch file.

---

## api_keys

```yaml
api_keys:
  openai: "${OPENAI_API_KEY}"
  gemini: "${GEMINI_API_KEY}"
```

- The scripts look the key up under the **name of the active provider**. With `active_provider: "gemini"` they read `api_keys.gemini`. A new provider therefore needs its own line here (see [ADDING_PROVIDERS.md](ADDING_PROVIDERS.md)).
- `ollama` and `ollama-ssh` need no key.
- `get_OCR_results.py` in `process_only` mode needs no key either.

---

## image_management

Where the scans are stored.

```yaml
image_management:
  active_adapter: "cloudflare"            # cloudflare | local
  local_root: "C:\\Users\\me\\scans"
  cloudflare:
    r2_endpoint_url: "https://<account-id>.r2.cloudflarestorage.com"
    r2_access_key_id: "${R2_ACCESS_KEY_ID}"
    r2_secret_access_key: "${R2_SECRET_ACCESS_KEY}"
    r2_bucket_name: "blm-ringing-retrap"
    r2_base_path: "ringing/"
    public_base_url: "https://pub-<id>.r2.dev/"
```

| Key | Default if missing | Meaning |
|---|---|---|
| `active_adapter` | `local` | `local` = a folder on this computer; `cloudflare` = a Cloudflare R2 bucket. |
| `local_root` | `.` | `local` only: the folder that **contains** the `batch_*` folders. All subfolders are searched. Files whose name starts with `.` are ignored. |
| `cloudflare.r2_endpoint_url` | – | The S3 address of your R2 account. |
| `cloudflare.r2_access_key_id`, `cloudflare.r2_secret_access_key` | – | The R2 keys, taken from `.env`. They are used **only to list** the files in the bucket. |
| `cloudflare.r2_bucket_name` | – | The name of the bucket. |
| `cloudflare.r2_base_path` | `""` (the whole bucket) | Only files below this folder are used, e.g. `ringing/` or `retrap/`. |
| `cloudflare.public_base_url` | – | The public address of the bucket. The images themselves are **downloaded through this address**, so public access must be enabled on the bucket. |

The folder and file naming rules (`batch_<N>/page_<M>`) are the same for both adapters.

---

## directories

Where the pipeline writes its files.

```yaml
directories:
  state_dir_base: "data_output/state"
  output_dir_base: "data_output/output/{run_name}"
  batch_out_dir: "data_output/batches_out/{run_name}"
  name_suffix: "-{run_name}"
  processed_log_file: "processed_items.txt"
  raw_dir_base: "data_output/raw"
```

| Key | Default if missing | What is stored there |
|---|---|---|
| `state_dir_base` | `state` | `processed_batches.json` and the problems files |
| `output_dir_base` | `output` | the results and review files |
| `batch_out_dir` | `batches_out` | the generated batch files (`.jsonl`), before they are uploaded |
| `name_suffix` | `""`, but `get_OCR_results.py` uses `-FB_scraping` (an old leftover), so **always set it** | added to the output file names: `results<name_suffix>.jsonl` |
| `processed_log_file` | `processed_items.txt` | the log of all sent images ([SCRIPTS.md](SCRIPTS.md#processed_itemstxt)) |
| `raw_dir_base` | `data_output/raw` | the untouched AI answers (download modes, and all Ollama runs) |

Relative paths are relative to the folder you run the scripts from. Missing folders are created automatically.

---

## batch_generation

Used by `make_OCR_batch.py` with `gemini` and `openai`. **Ignored completely** with `ollama` and `ollama-ssh`.

```yaml
batch_generation:
  output_prefix: "batch-{run_name}"
  image_upload_method: "file_api"             # base64 | file_api | pass_a_url
  batch_operation_mode: "generate_and_upload" # generate | upload | generate_and_upload | try_again_upload
  upload_files: []
  limits:
    max_lines: 50000
    max_jsonl_bytes: 209715200
    safety_factor: 0.90
```

| Key | Default if missing | Meaning |
|---|---|---|
| `output_prefix` | `batch-gemini` | Names of the batch files: `<output_prefix>_0001.jsonl`, `_0002`, … |
| `image_upload_method` | `base64` | How the image gets to the AI; see the next table. |
| `batch_operation_mode` | `generate_and_upload` | What the script does; see [Batch operation modes](#batch-operation-modes). |
| `upload_files` | `[]` | File names for the modes `upload` and `try_again_upload`. |
| `limits.max_lines` | `50000` | Maximum number of requests in one batch file. |
| `limits.max_jsonl_bytes` | `209715200` (200 MB) | Maximum size of one batch file. |
| `limits.safety_factor` | `0.90` | Only this share of `max_jsonl_bytes` is used, as a safety margin. |

- When a batch file would go over a limit, a new file is started (`_0002`, …).
- A single request that is bigger than the limit on its own is skipped with `[ERROR] Item too large`.
- The default limits are OpenAI's batch limits (50,000 requests and 200 MB per file). Gemini allows larger files (up to 2 GB).
- Generating again with the same `output_prefix` **overwrites** existing batch files with the same number (`_0001.jsonl`, …).

### Image upload methods

| Method | How it works | Gemini | OpenAI |
|---|---|---|---|
| `file_api` | Each image is first uploaded to the provider's file storage. The batch file then only contains a reference to it. **Recommended.** | ✔ | ✔ |
| `base64` | The image itself is written into the batch file as text. Batch files become very large. | ✔ | ✘ does not work in the current code (see below) |
| `pass_a_url` | The batch file contains the public Cloudflare address of the image, and the provider downloads it from there. Needs `active_adapter: "cloudflare"`. | ✘ every image is skipped | ✔ |

- Gemini deletes uploaded files after **48 hours**. This matters when you resend old requests with `try_again_upload`, because they still point to the first upload.
- OpenAI + `base64`: the code puts the plain base64 text into the request, but OpenAI expects a full `data:image/jpeg;base64,...` address. These requests are therefore expected to fail. Use `file_api`.
- Local models (Ollama) always get the image directly as base64, so this setting is ignored and a note is printed.

### Batch operation modes

| Mode | What happens | Logged in `processed_items.txt` |
|---|---|---|
| `generate_and_upload` | Builds the batch file(s) from the selected images and uploads them right away. **The normal choice.** | yes, after a successful upload |
| `generate` | Only builds the batch file(s) in `batch_out_dir`. Nothing is sent. Useful to look at the requests first. | no |
| `upload` | Uploads batch files you generated earlier. List their names in `upload_files`. | yes |
| `try_again_upload` | Resends **only some requests** of an earlier batch file, e.g. the ones that failed. | yes |

Details and pitfalls:

- `generate_and_upload` uploads exactly the files it has just created. If your filters matched no image, it stops with `[ERROR] No JSONL files were generated` instead of uploading older files.
- `upload` looks for the files **directly inside** `batch_out_dir`. Because `batch_out_dir` contains `{run_name}`, the `run_name` must be the one the files were generated with.
- `try_again_upload` needs three things:
  - `processing.mode: "explicit"`,
  - the ids to resend in `processing.allowed_images`,
  - the original file name in `upload_files`.

  The file is searched in `batch_out_dir` and its subfolders, so again the `run_name` must match. A copy that contains only the listed requests is written next to the original with an `-err` suffix (e.g. `batch-myrun_0001-err.jsonl`), and that copy is uploaded. Step-by-step example: [SCRIPTS.md](SCRIPTS.md#retry-only-the-images-that-failed).

---

## processing

Selects **which scans** are sent. Used for all providers, including Ollama.

```yaml
processing:
  mode: "explicit"                        # all | batches | explicit
  allowed_batches: ["batch_10"]
  allowed_images: ["img_9_43", "img_8_100"]
  image_extensions: [".jpg", ".jpeg", ".png", ".webp", ".pdf"]
  path_parsing:
    batch_pattern: '(batch|davka)[_\-](.+)'
    page_pattern: 'page[_\-](.+)'
```

| Key | Default if missing | Meaning |
|---|---|---|
| `mode` | `batches` | `all` = every scan found; `batches` = only scans inside the folders listed in `allowed_batches`; `explicit` = only the images listed in `allowed_images`. |
| `allowed_batches` | `[]` | Folder names, written **exactly** as on disk: `"batch_10"` (not `10`, not `img_10`). Used only with `mode: "batches"`. |
| `allowed_images` | `[]` | Image ids `img_<batch>_<page>`, e.g. `img_9_43` = `batch_9/page_43.jpg`. Used only with `mode: "explicit"`. |
| `image_extensions` | the five shown | Only files with these endings are collected. The AI reads `.jpg`, `.jpeg` and `.png` best. `.pdf` works with Gemini; Ollama skips it. |
| `path_parsing` | the two patterns shown | How the image id is read from the path; see below. |

Scans whose path does not follow the naming rule are silently left out.

### path_parsing

The image id `img_<batch>_<page>` is built from the path of the scan:

```
ringing/batch_3/page_12.jpg   ->   batch "3", page "12"   ->   img_3_12
```

- Every part of the path (every folder name and the file name) is tested against both patterns. The **last** part that matches wins.
- The id is taken from the **last group in brackets** of the pattern. `(batch|davka)[_\-](.+)` has two groups, and the id is in the second one.
- The path is changed to lower case first, so `Batch_3` works too. The file extension is removed from the page id.
- The shape `img_<batch>_<page>` itself cannot be changed. It links the batch files, results, review files and logs together.
- Write the patterns in **single quotes**.

Example: your folders are called `box-7/scan-15.jpg`:

```yaml
  path_parsing:
    batch_pattern: 'box[_\-](.+)'
    page_pattern: 'scan[_\-](.+)'
```

This gives the id `img_7_15`.

---

## validation

Settings for `get_OCR_results.py`: which validator runs, and how.

```yaml
validation:
  active_validator: "ocr_ringing"
  species_lexicon_path: "lexicon/mbs_all_species_with_measurements_v3.csv"
  locations_lexicon_path: "lexicon/mbs_ringing_locations.csv"
  serial_lexicon_path: "lexicon/mbs_series_options.csv"

  ocr_sightings:
    ...
  ocr_ringing:
    ...
  ocr_retrap:
    ...
```

| Key | Default if missing | Meaning |
|---|---|---|
| `active_validator` | `ocr_sightings` | Which validator is used: `ocr_ringing`, `ocr_retrap` or `ocr_sightings`. There is also `ocr_ringing-STORM_PETREL`; see [VALIDATORS.md](VALIDATORS.md#variant-files) before you use it. |
| `species_lexicon_path` | – | The species list with wing and weight ranges and monthly maximums. Used by all three validators. |
| `locations_lexicon_path` | – | The ringing places. Used by `ocr_ringing` and `ocr_retrap`. |
| `serial_lexicon_path` | – | The allowed ring series per species. Used by `ocr_ringing`. |

- Each validator reads **its own section**, which must have exactly the same name as `active_validator` (`validation.ocr_ringing`, …). You can keep all sections in the file; only the active one is used.
- Inside a section, `rules:` switches individual checks on (`true`) or off (`false`). A rule that is **not listed is off**. A few basic steps always run, whatever the switches say. They are listed in [VALIDATORS.md](VALIDATORS.md).
- Lexicon paths may be absolute (`"C:\\...\\lexicon\\file.csv"`) or relative to the project folder (`"lexicon/file.csv"`).

The tables below give a short overview. **Every setting and every rule is explained in detail in [VALIDATORS.md](VALIDATORS.md).**

### Settings of ocr_ringing

```yaml
  ocr_ringing:
    verbose: true
    species_match_threshold: 80
    location_match_threshold: 85
    measurement_tolerance: 0.05
    backfill_species: "Subalpine Warbler"       # false | "AUTO" | "<species name>"
    ring_no_mode: "AUTO"                        # AUTO | MANUAL
    ring_no_manual_regex: '^[A-Za-z0-9]+\.\d{3}$'
    carry_over_settings:
      prefix_keys: ["ring_no"]
      ring_no_max_suffix_length: 3
      carry_over_keys: ["date", "place", "initials", "time", "serial"]
      assumed_keys: ["age"]
    rules:
      serial_validation: true
      serial_validation-simple: true
      ring_no_validation: true
      low_match_score: true
      wing_range: true
      weight_range: true
      23_rows: true
      age_validation: true
      date_validation: true
      sex_validation: true
```

| Key | Default if missing | Meaning |
|---|---|---|
| `verbose` | `false` | Print more details while validating. |
| `species_match_threshold` | `80` | The minimum similarity (0–100) for a species name to be matched to the species lexicon. |
| `location_match_threshold` | `85` | The same, for place names and the locations lexicon. |
| `measurement_tolerance` | `0.10` | The allowed deviation from the lexicon's wing and weight range: `0.05` = ±5 %. |
| `backfill_species` | `false` | Fill in missing species names. `false` = off. `"AUTO"` = take the name from the previous page. `"Moorhen"` = use this name for every page without a species. |
| `ring_no_mode` | `AUTO` | How ring numbers are checked. `AUTO` = learn the usual shape from the page itself. `MANUAL` = use `ring_no_manual_regex`. |
| `ring_no_manual_regex` | `^\d{2}\.\d{3}$` | The pattern used in `MANUAL` mode. |
| `carry_over_settings` | none (off) | Filling empty cells from the row above, and completing shortened ring numbers. |
| `rules` | all off | The individual checks. |

→ [VALIDATORS.md – ocr_ringing](VALIDATORS.md#ocr_ringing)

### Settings of ocr_retrap

```yaml
  ocr_retrap:
    verbose: true
    species_match_threshold: 80
    location_match_threshold: 85
    measurement_tolerance: 0.05
    ring_no_mode: "MANUAL"                      # AUTO | MANUAL
    ring_no_manual_regex: '^[A-Za-z0-9]+(?:\.\d{3})?$'
    rules:
      species_match: true
      location_match: true
      low_match_score: true
      wing_range: true
      weight_range: true
      age_validation: true
      date_validation: true
      ring_no_validation: true
```

| Key | Default if missing | Meaning |
|---|---|---|
| `verbose` | – | Present in the config, but not used by the current code. |
| `species_match_threshold` | `80` | As for `ocr_ringing`. |
| `location_match_threshold` | `85` | As for `ocr_ringing`. |
| `measurement_tolerance` | `0.10` | As for `ocr_ringing`. |
| `ring_no_mode` | `AUTO` | `AUTO` = built-in rules for ring numbers. `MANUAL` = use `ring_no_manual_regex`. |
| `ring_no_manual_regex` | none | The pattern for `MANUAL` mode. Without it, `MANUAL` checks nothing. |
| `rules` | all off | The individual checks. |

→ [VALIDATORS.md – ocr_retrap](VALIDATORS.md#ocr_retrap)

### Settings of ocr_sightings

```yaml
  ocr_sightings:
    deduplicate_review: true
    rules:
      date_validation: true
      duplicate_species_in_record: true
      low_match_score: true
      name_without_number: true
      number_has_illegal_chars: true
      suspicious_number_rule: true
      rare_species_E: true
      exceeds_monthly_max: true
      sudden_change: true
    sudden_change_config:
      window_days: 7
      min_required_days: 4
      max_gap_days: 2
      min_ratio: 3
      min_absolute_diff: 6
      volatility: 0.46
      verbose: true
```

| Key | Default if missing | Meaning |
|---|---|---|
| `deduplicate_review` | `false` | Merge review lines about the same species on the same sheet into one line with all reasons. |
| `rules` | all off | The individual checks. |
| `sudden_change_config` | see VALIDATORS.md | Fine-tuning of the `sudden_change` rule. |

→ [VALIDATORS.md – ocr_sightings](VALIDATORS.md#ocr_sightings)

---

## prompts.yaml

```yaml
PROMPT_4:
  system_prompt: |
    You are a structured data extraction engine specializing in bird ringing records.
    ...
  user_prompt: |
    You are an expert OCR system specialized in digitizing handwritten historical bird ringing records.
    ...
```

- Each top-level key is one prompt. `run_settings.active_prompt_key` chooses which one is used.
- `system_prompt` holds the general role and rules for the AI. `user_prompt` holds the concrete task and the exact JSON shape to return. Both are sent with every image.
- `|` starts a text that spans several lines. Keep the indentation of every line.
- If the key does not exist, the script stops and prints the available keys. If `system_prompt` or `user_prompt` is missing or empty, it prints a warning.

| Key | For | The AI returns |
|---|---|---|
| `PROMPT_1` | sightings sheets with handwritten species names (older sheets) | one object: `Location`, `Weather`, `Date`, `Time`, `Obs`, `species` (a list of `Name`, `Number`, `Remarks`) |
| `PROMPT_2` | sightings sheets with a pre-printed species list (newer sheets) | the same, without weather; only species with a written count; remarks from two places on the form |
| `PROMPT_3` | like `PROMPT_1`, plus `Confidence` and `Flag_Reason` for every species (not recommended) | the same, plus two fields |
| `PROMPT_4` | ringing sheets (exactly 23 rows) | a list of 23 rows: `id_row`, `serial`, `ring_no`, `species`, `ringing` (`date`, `age`, `sex`, `wing`, `weight`, `time`, `initials`, `place`, `re`) |
| `PROMPT_5` | retrap cards | one object: `species`, `ring_no`, `ringing_date`, `ringing_wing`, `ringing_weight`, `ringing_age`, `ringing_place`, `retraps` (a list) |

**Adding a new prompt**

1. Copy an existing block and give it a new key, e.g. `PROMPT_7`.
2. Change the texts.
3. Set `active_prompt_key: "PROMPT_7"`.

Describe the exact JSON shape in the prompt, with an example, and ask for JSON only. If the JSON shape changes, the validator must understand the new shape (see [ADDING_VALIDATORS.md](ADDING_VALIDATORS.md)).

---

## config-ollama.yaml

This file is only read when `active_provider` is `ollama` or `ollama-ssh`. It holds three things:

- the connection: this computer, or a server over SSH,
- the link between a model name and its model module,
- the default request settings.

It is explained in full in [OLLAMA_LOCAL_MODELS.md](OLLAMA_LOCAL_MODELS.md#config-ollamayaml).

Remember:

- `${...}` placeholders do **not** work in this file.
- The file is **not** excluded from git.

---

## Preset config files

`config-ringing.yaml` and `config-sightings.yaml` are **saved example configurations** for ringing runs and sightings runs. The scripts never read them.

To use one:

1. Keep a backup of your current `config.yaml`.
2. Copy the content of the preset into `config.yaml`.

The presets may lack newer settings. For example, `config-sightings.yaml` has no `raw_dir_base` and no `path_parsing`. In that case the defaults from this page apply.

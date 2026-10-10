# Project structure

This page shows what every folder and file is for, and how the code fits together. Use it to find your way around, or before you change the code.

**On this page**

- [Folder tree](#folder-tree)
- [What belongs where](#what-belongs-where)
- [How the code fits together](#how-the-code-fits-together)
- [The data_output folder](#the-data_output-folder)

---

## Folder tree

```
OCR_universal_project/
├── make_OCR_batch.py           Step 1: select the scans and send them to the AI
├── get_OCR_results.py          Step 2: download the answers, validate them, write the output
├── list_batches_G.py           Helper: list your Gemini batch jobs and their state
│
├── config.yaml                 The main configuration (no secrets)
├── config-ollama.yaml          Ollama only: connection, model -> module mapping, defaults
├── config-ringing.yaml         A saved example configuration for ringing sheets (not read by the scripts)
├── config-sightings.yaml       A saved example configuration for sightings sheets (not read by the scripts)
├── prompts.yaml                The prompts sent to the AI (PROMPT_1 ... PROMPT_5)
├── .env                        Your API keys and passwords (never committed to git)
├── .env.example                The template for .env
├── .gitignore                  Files and folders git ignores
├── requirements.txt            Python packages:  pip install -r requirements.txt
├── pyproject.toml              Project description, with the same packages
├── processed_items.txt         Log: one line per sent image (created by the pipeline)
│
├── ai_providers/               Talking to the AI services
│   ├── provider_interface.py   Describes the functions every provider must have
│   ├── provider_selector.py    Picks the provider named in config.yaml and checks it is complete
│   ├── base.py                 Shared helpers for all providers: config, prompts, ids, batch files, logs
│   ├── gemini_provider.py      Google Gemini Batch API
│   ├── openai_provider.py      OpenAI Batch API
│   ├── base_ollama.py          Ollama (local and SSH): connection, image-by-image run, raw file
│   └── glm_ocr_provider.py     Model module for the local model glm-ocr (HTML table -> JSON)
│
├── image_management/           Reading the scans
│   ├── local_adapter.py        Scans in a folder on this computer
│   └── cloudflare_adapter.py   Scans in a Cloudflare R2 bucket
│
├── validators/                 Checking, repairing and enriching the AI answers
│   ├── validator_interface.py  Describes process_batch(), the function every validator must have
│   ├── ocr_ringing.py          Ringing sheets
│   ├── ocr_retrap.py           Retrap cards
│   ├── ocr_sightings.py        Sightings sheets
│   ├── ocr_ringing-STORM_PETREL.py   Storm Petrel variant - currently NOT used (see VALIDATORS.md)
│   └── ocr_ringing-org.py      An old backup copy of ocr_ringing.py - not used
│
├── lexicon/                    Reference lists used by the validators
│   ├── mbs_all_species_with_measurements_v3.csv   Species, EURING codes, wing/weight ranges, monthly maximums
│   ├── mbs_ringing_locations.csv                  Ringing places and their other spellings
│   └── mbs_series_options.csv                     Allowed ring series per species
│
├── extra_tools/                Helpers outside the main workflow (see EXTRA_TOOLS.md)
│   ├── convertor_PDF_to_JPEG.py    PDF -> batch_N/page_M.jpg (optionally uploaded to R2)
│   ├── json2csv_v4.py              JSONL -> CSV
│   ├── silent_errors.py            Silent-error detector: the rules
│   ├── silent_errors_plots.py      Silent-error detector: the charts
│   ├── main-silent_errors.py       Silent-error detector: runs both
│   └── storm_petrels-hard.py       Expands ring-number ranges (Storm Petrel cards)
│
├── data_output/                Everything the pipeline produces (listed in .gitignore)
│   ├── batches_out/<run_name>/     Batch files sent to the provider
│   ├── raw/                        Untouched AI answers (download modes, Ollama runs)
│   ├── output/<run_name>/          results-*.jsonl and review-*.jsonl
│   └── state/                      processed_batches.json and problems_to_deal_with-*.jsonl
│
├── docs/                       This documentation (README_Jonas.txt = the author's short notes)
├── z_archived_data/            Old test data, not used by anything (listed in .gitignore)
└── __pycache__/                Python's automatic cache (also inside the code folders) - ignore it
```

---

## What belongs where

### Shared helpers or provider code?

Rule of thumb: **if it does not talk to the provider, it belongs in `base.py`.** A fix made there helps every provider at once.

| Lives in `ai_providers/base.py` | Lives in the provider file (`gemini_provider.py`, …) |
|---|---|
| loading `config.yaml` and `.env`, filling in the placeholders | building one request line (`make_jsonl_line`) |
| loading the prompts | uploading images and batch files |
| building the image id from the path | creating and listing batch jobs |
| splitting requests into batch files | reading the provider's answer format |
| reading and writing `processed_batches.json` | recognising a failed request |
| writing `processed_items.txt` | batch information: tokens, times, counts |
| the problem log | the name of the id field in a request (`id` / `custom_id`) |
| reading raw JSONL text, the raw file name | |

### Ollama: base or model module?

| Lives in `ai_providers/base_ollama.py` | Lives in a model module (`glm_ocr_provider.py`, …) |
|---|---|
| reading `config-ollama.yaml` | the prompt the model understands (`OCR_PROMPT`) |
| the connection, including the SSH tunnel | the model's options (`OLLAMA_OPTIONS`, …) |
| checking that the model is installed and can see images | converting the model's answer (`postprocess_output()`) |
| sending the images one by one, retries | |
| writing the raw file, resuming | |
| choosing the model module (`model_providers`) | |

### Validators

Each validator is **independent**: there is no shared validator base file, because future document types may have nothing in common. Helper functions such as fuzzy matching are therefore copied between validators.

---

## How the code fits together

### make_OCR_batch.py

```
make_OCR_batch.py
 ├─ base.load_config()                       config.yaml + .env + {run_name}
 ├─ provider_selector.get_provider(name)     -> the provider's dictionary of functions (checked)
 ├─ provider['get_image_adapter'](config)    -> local_adapter / cloudflare_adapter
 │     └─ collect_image_files()              all scans
 ├─ filter_images()                          processing.mode, using extract_batch_page_ids / is_in_allowed_batch
 │
 ├─ cloud providers (gemini, openai):
 │     ├─ provider['build_jsonl_batches']()  prompt + image -> request lines -> batch files
 │     ├─ provider['upload_file_to_provider']() + provider['create_batch_job']()
 │     └─ base.append_processed_items()      -> processed_items.txt
 │
 └─ local providers (ollama, ollama-ssh):
       └─ base_ollama.run_local_batch()
             ├─ config-ollama.yaml, connection (SSH tunnel for ollama-ssh)
             ├─ model module (model_providers) -> settings and prompt
             ├─ for every image: Ollama API -> model_module.postprocess_output()
             └─ raw file in data_output/raw/   (+ processed_items.txt)
```

### get_OCR_results.py

```
get_OCR_results.py  main(batch_ids, process_from, mode)
 ├─ base.load_config(), provider_selector.get_provider(name)
 ├─ import the validator named in validation.active_validator   (if/elif list in this file)
 ├─ get the answers:
 │     standard       provider['list_batches'] -> provider['parse_batch_results']
 │     full/download  provider['download_raw_batch'] -> data_output/raw/ -> base.parse_raw_content
 │     process_only   data_output/raw/ -> base.parse_raw_content          (also used for all Ollama runs)
 ├─ provider['is_error_record'] / ['extract_text_from_record']  -> [{"text", "custom_id", "batch_id"}, ...]
 ├─ validator.process_batch(all_raw_results, config)            -> [(file name, content), ...]
 ├─ write_validator_outputs()                                   -> results / review / problems files (appended)
 └─ base.save_processed_batches() + base.mark_processed_items_downloaded()
```

---

## The data_output folder

| Folder | Written by | Read by | Contents |
|---|---|---|---|
| `batches_out/<run_name>/` | `make_OCR_batch.py` | `make_OCR_batch.py` (modes `upload`, `try_again_upload`) | batch files, `batch-<run_name>_0001.jsonl` (and `...-err.jsonl` for retries) |
| `raw/` | `get_OCR_results.py` (`full`, `download_only`); `make_OCR_batch.py` (Ollama) | `get_OCR_results.py` (`process_only`, Ollama) | `<batch id><name_suffix>_raw.jsonl` |
| `output/<run_name>/` | `get_OCR_results.py` | you, and the extra tools | `results-<run_name>.jsonl`, `review-<run_name>.jsonl` |
| `state/` | `get_OCR_results.py` | `get_OCR_results.py` (`processed_batches.json`) | `processed_batches.json`, `problems_to_deal_with-<run_name>.jsonl` |

All the file formats are described in [SCRIPTS.md](SCRIPTS.md#files-written-by-the-pipeline).

# Extra tools

The folder `extra_tools/` contains helper scripts that are **not part of the main workflow**. They help before it (preparing scans) or after it (converting and checking results).

Important for all of them:

- They do **not** read `config.yaml`. Their settings are variables at the top of each file (`CONFIG`, `INPUT_...`) or in its last lines. Open the file, change the settings, save, then run it.
- The paths written in them point to the author's computer. **Change them before the first run.**
- Some need extra Python packages; see each tool.

**On this page**

- [PDF to JPEG converter](#pdf-to-jpeg-converter): turn scanned PDF files into the `batch_N/page_M.jpg` layout
- [JSONL to CSV converter](#jsonl-to-csv-converter): turn results or review files into CSV for Excel
- [Silent-error detector](#silent-error-detector): find misreads that look valid, in ringing data
- [Storm Petrel ring-range expander](#storm-petrel-ring-range-expander): split ring-number ranges into one row per ring

---

## PDF to JPEG converter

**File:** `extra_tools/convertor_PDF_to_JPEG.py`

It converts scanned **PDF files** into the folder layout the pipeline needs:

```
<OUTPUT_BASE_FOLDER>/batch_10/page_1.jpg, page_2.jpg, ... page_100.jpg
                    /batch_11/page_1.jpg, ...
```

It can keep the files locally, or upload them directly to Cloudflare R2.

### Requirements

- `pip install pypdf pdf2image` (these are **not** in `requirements.txt`). `boto3` for R2 is already there.
- For JPEG output: **Poppler**, a free PDF program that `pdf2image` uses. On Windows, download it, unpack it, and set `POPPLER_PATH` to its `Library\bin` folder. How to install it on each system: <https://github.com/Belval/pdf2image>.

### Settings (at the top of the file)

| Setting | Meaning |
|---|---|
| `POPPLER_PATH` | the folder of the Poppler programs (Windows) |
| `INPUT_MODE` | `"folder"` = all PDF files in `INPUT_FOLDER_PATH`, including subfolders; `"single_file"` = only `INPUT_FILE_PATH` |
| `INPUT_FOLDER_PATH`, `INPUT_FILE_PATH` | where the PDF files are |
| `OUTPUT_FORMAT` | `"jpeg"` / `"jpg"` = one image per PDF page; `"pdf"` = split into smaller PDF files |
| `PAGES_PER_PDF` | only for `"pdf"` output: pages per new PDF file |
| `OUTPUT_BASE_FOLDER` | where the `batch_N` folders are created |
| `FILES_PER_BATCH` | how many files go into one `batch_N` folder (e.g. 100) |
| `START_BATCH_NUMBER` | the number of the first folder, so you can continue the numbering of an earlier conversion |
| `OUTPUT_DESTINATION` | `"local"`, or `"r2"` = also upload every file to Cloudflare R2 |
| `KEEP_LOCAL_FILES` | with `"r2"`: `True` = keep the local copies, `False` = delete them after the upload |
| `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` | the Cloudflare R2 connection |
| `R2_TARGET_FOLDER` | a folder inside the bucket, e.g. `"ringing"` gives `ringing/batch_10/page_1.jpg`. Match it with `r2_base_path` in `config.yaml`. |
| `MAX_R2_STORAGE_GB` | safety limit (9). Before uploading, the script adds up the size of the bucket and stops if this much is already used. The R2 free tier has 10 GB. |

### How the files are numbered

- All PDF files are processed in alphabetical order of their paths.
- The pages are numbered continuously: `page_1` … `page_100` go into the first folder, then the next folder starts again at `page_1`.
- PDF files that cannot be read are listed at the end of the run.
- The comments at the top of the file show which batch numbers the author used for which dataset and year.

### Run

```bash
python extra_tools/convertor_PDF_to_JPEG.py
```

> ⚠ **Security:** this file contains Cloudflare R2 keys written directly into the code, unlike the main pipeline, which reads them from `.env`. Do not share the file and do not publish it. Ideally, move the keys into `.env`.

---

## JSONL to CSV converter

**File:** `extra_tools/json2csv_v4.py`

It turns a results file, a review file or the silent-error output into a **CSV file** for Excel or a database. Nested fields are flattened with `_`; for example, `ringing → age → written` becomes the column `ringing_age_written`.

### Settings (in the last lines of the file)

```python
input_file  = r"C:\...\results-ringing-subalpine_warblers-full.jsonl"
review_file = r"C:\...\review-ringing-subalpine_warblers-full.jsonl"
output_file = r"C:\...\results-ringing-subalpine_warblers-full.csv"

convert_jsonl_to_csv(
    input_filepath=input_file,
    output_filepath=output_file,
    review_filepath=review_file,
    filter_review_data=False,      # True = leave out the rows that are in the review file
    data_type='ringing',           # which kind of file (see below)
    normalize_place=False          # see below
)
```

| `data_type` | Input file | One CSV row per … |
|---|---|---|
| `ringing` | results of `ocr_ringing` | row of the sheet |
| `retrap` | results of `ocr_retrap` | retrap (the card fields are repeated in every row) |
| `ringing_review` | review of `ocr_ringing` | flagged row; `reasons` are joined with commas |
| `retrap_review` | review of `ocr_retrap` | retrap of a flagged card |
| `silent_errors` | `silent_error_flags.jsonl` of the silent-error detector | flagged row, plus the columns `details_count`, `details_severity` (the worst one), `details_fields`, `details_rules`, `details_evidence` |

- **`filter_review_data=True`**, together with `review_file`, leaves out every row that also appears in the review file. Rows are matched by image id + `id_row` (for retrap cards: `id_retrap`). The result is a "clean" CSV that contains only unflagged rows.
- **`normalize_place=True`** is for the review and silent-error types. When `place_mbs_dict` is only the text `loc_not_matched`, it is moved into the normal place columns, so all rows have the same columns.
- **Column order:** `custom_id`, `batch_id`, `reasons`, the `details_*` columns, `timestamp`, `row_count`, `id_row`, `id_retrap`, `serial`, `ring_no`, `species` come first. All other columns follow alphabetically.
- **Sightings results are not supported.**
- The CSV file is written in UTF-8. In Excel, open it with *Data → From Text/CSV* so that special characters are shown correctly.

### Run

```bash
python extra_tools/json2csv_v4.py
```

---

## Silent-error detector

**Files:**

- `extra_tools/silent_errors.py`: the rules,
- `extra_tools/silent_errors_plots.py`: the charts,
- `extra_tools/main-silent_errors.py`: runs both.

### What it finds

Some reading mistakes produce values that **look valid in every single row**, so the validator cannot see them. Example: a whole card where the ring series was misread the same way in all 23 rows. Inside the card everything agrees, but compared with the other cards it is clearly wrong.

This tool looks at the **whole dataset**. It uses no AI and no internet. It first learns how the archive is organised: which ring series uses which number blocks, on which cards, in which period, at which places, and by which ringers. Then it flags rows that break this pattern.

It is meant for **ringing sheets** (`ocr_ringing` results).

### How it works

1. It reads the results: the `results-*.jsonl` of the pipeline, or a CSV made with the JSONL to CSV converter (`data_type='ringing'`).
2. Optionally, it removes rows that are already in the review file, so known-bad rows do not shape what "normal" looks like.
3. It learns the structure of the data.
4. It applies seven rules.
5. It writes the flagged rows.

| Rule | Severity | Flags a row when … |
|---|---|---|
| R1 `orphan_block_gapfit` | high / medium | a rare block of ring numbers (at most 30 % of a series, on at most 2 cards) would fit exactly into a gap of the series' main block if one digit were changed. This is typical of a misread number prefix on a whole card. |
| R2 `cross_card_context` | high | the series of a row disagrees with rows on **other** cards that have nearby ring numbers (± 40) or the same date |
| R3 `era_violation` | medium | a series is used far outside the period in which it appears elsewhere (± 120 days), while another series fits that date and number block |
| R4 `card_sequence_break` | high / low | the ring numbers continue from the previous card but the series changes (high), or a rare number block appears inside a series (low) |
| R5 `duplicate_key` | medium | the same series + ring number appears more than once |
| R6 `missing_key_fields` | high | the ring number is empty or unreadable (and the series is empty, if `blank_serial_is_null` is `False`) |
| R7 `group_minority` | low | a series is a minority within its date + ringer + place group, and its number block does not support it |

### Settings (`CONFIG` at the top of silent_errors.py)

| Setting | Meaning |
|---|---|
| `input_path` | the results file (`.jsonl`) or a CSV |
| `input_format` | `"auto"` (decided by the file ending), `"csv"` or `"jsonl"` |
| `filter_review_data`, `review_path` | drop the rows that are already in the review file before learning the structure |
| `output_format` | `"jsonl"` = one line per flagged row, in the shape of a review file; `"csv"` = one line per flag |
| `output_csv`, `output_jsonl` | output file names (`silent_error_flags.csv` / `.jsonl`) |
| `jsonl_output.include_details` | also write the rule, field, severity and evidence of every flag |
| `columns` | the column names. The defaults fit the pipeline's output. |
| `date_formats` | the date formats that are tried |
| `ring_no_unit_digits`, `ring_no_prefix_digits` | how ring numbers are written: `<thousands>.<units>`, e.g. `05.034` = 5034 |
| `blank_serial_is_null` | `True` = an empty series is a deliberate empty value, not an error |
| `rules` | each rule with `enabled` and its limits |

The charts are configured in `PLOT_CONFIG` at the top of `silent_errors_plots.py`:

- `plots` switches each chart on or off,
- `plots_dir` is the folder the charts are saved in (default `plots`),
- `plots_show: True` opens the charts in windows instead of saving them.

### Run

Run it **from inside the `extra_tools` folder**, because the files load each other by name:

```bash
cd extra_tools
python main-silent_errors.py      # rules + charts
python silent_errors.py           # rules only, no charts
```

### Output

It is written into the current folder, `extra_tools/`:

- `silent_error_flags.jsonl` (or `.csv`). The JSONL lines look like review lines: `custom_id`, `batch_id`, `species_entry`, `reasons` (the rule names), `timestamp`, plus `details` (rule, field, severity, evidence).
- `plots/p1_series_map.png` … `plots/p7_block_remap.png`, seven charts of the discovered structure and the flags.
- A readable summary in the terminal.

To get a CSV, use the JSONL to CSV converter with `data_type='silent_errors'`.

The texts inside the files still mention `silent_errors_run.py`; that file is now called `main-silent_errors.py`. It needs `pandas`, and for the charts `matplotlib` and `numpy`; all three are in `requirements.txt`.

---

## Storm Petrel ring-range expander

**File:** `extra_tools/storm_petrels-hard.py`

On some Storm Petrel ringing cards, **one row stands for a whole range of rings**. For example, `0.151-200` means rings 0.151 to 0.200. This script expands such rows into **one row per ring** and keeps all the other fields of the row.

It understands these ways of writing:

| Written | Meaning |
|---|---|
| `0.151-200` | 0.151 … 0.200 |
| `.201-250` | the prefix is taken from the last number written in full |
| `2.171-200` | the end without its prefix |
| `216.2971-` and the next row `3000` | the range continues on the next row; that row is used up |
| `0.958-1.000` | a range across a prefix change (it is flagged) |
| `212.2666` | a single ring, kept as it is |

Nothing is repaired silently. Every assumption is written into the row (`_flags`) and into a quality-check file.

### Settings (`CONFIG` at the top of the file)

| Setting | Meaning |
|---|---|
| `input_path` | a results file of `ocr_ringing` |
| `output_path` | the expanded JSONL file |
| `qc_path` | the quality-check report (relative paths go to the current folder); `None` = don't write it |
| `flat_output_path` | optional: one ring per line, e.g. for a database import; `None` = off |
| `serial_width` | digits after the dot (`None` = detect it per sheet) and `serial_width_fallback` (4) |
| `max_range` | longer ranges are not expanded (a guard against misreads), default 1000 |
| `warn_range_above` | longer ranges are expanded but flagged, default 100 |
| `consume_continuation_row`, `allow_prefix_carry`, `allow_dot_as_dash`, `warn_on_context_mismatch` | switch the special cases on or off |
| `keep_expansion_metadata` | add `original_id_row`, `ring_no_raw`, `_seq` (position within the range) and `_flags` to every row |
| `per_bird_fields`, `numeric_fields`, `context_fields` | the fields used for the quality checks |

### Output

- An expanded JSONL file. Every page gets the new fields `row_count`, `source_row_count` and `serial_width`.
- A QC file with:
  - a summary (tables, rows in/out, how often each flag occurs, duplicate ring numbers),
  - one entry per expanded or flagged row.
- A summary in the terminal.

Common flags:

| Flag | Meaning |
|---|---|
| `range_end_from_next_row` | the end of the range was on the next row |
| `prefix_carry` | the range crosses a prefix change |
| `large_range` | a long range was expanded |
| `range_too_large_not_expanded` | the range was too long and was not expanded |
| `per_bird_fields_copied:wing+weight` | measurements that belong to one bird were copied to every ring of the range |
| `dangling_dash` | a range start without an end |
| `unparsable_ring_no` | the ring number could not be read |

### Run

```bash
python extra_tools/storm_petrels-hard.py
```

> The Storm Petrel variant of the ringing validator (`validators/ocr_ringing-STORM_PETREL.py`) was written for the same cards, but it is currently not used. See [VALIDATORS.md](VALIDATORS.md#variant-files).

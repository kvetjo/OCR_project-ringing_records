# Adding a new validator

A **validator** is the part of the pipeline that understands one type of paper form. It:

- reads the AI's answers,
- checks and repairs them,
- adds reference data,
- decides which rows a person must look at.

You need a new validator when you want to digitise a **new kind of document**. The three existing validators are described in [VALIDATORS.md](VALIDATORS.md).

**On this page**

1. [Where the validator fits in](#where-the-validator-fits-in)
2. [The one function you must write](#the-one-function-you-must-write)
3. [What you get as input](#what-you-get-as-input)
4. [What you return](#what-you-return)
5. [Step by step](#step-by-step)
6. [A complete minimal validator](#a-complete-minimal-validator)
7. [Conventions used by the existing validators](#conventions-used-by-the-existing-validators)
8. [Testing without paying again](#testing-without-paying-again)
9. [Checklist](#checklist)

---

## Where the validator fits in

```
prompts.yaml  ->  AI answer (JSON as text)  ->  get_OCR_results.py  ->  your validator  ->  results / review / problems files
```

- The **prompt** decides which JSON shape the AI returns.
- The **validator** must understand exactly that shape.

A new document type therefore usually needs both a new prompt ([CONFIGURATION.md](CONFIGURATION.md#promptsyaml)) and a new validator.

---

## The one function you must write

Every validator is one Python file in `validators/` with one function, `process_batch`. It is described in `validators/validator_interface.py`:

```python
def process_batch(
    all_raw_results: List[Dict[str, Any]],
    config: Dict[str, Any]
) -> List[Tuple[str, Any]]:
    ...
```

- `get_OCR_results.py` calls it **once per batch job**, with **all** answers of that batch.
- You may write as many helper functions in the file as you like.
- Validators are deliberately independent of each other. They share no common base file, so copy what you need from an existing validator.

---

## What you get as input

**`all_raw_results`** is a list with one entry per successful answer:

```python
{"text": "<the AI answer, usually JSON as text>", "custom_id": "img_3_12", "batch_id": "s2re3y87..."}
```

- Only successful answers arrive here. Failed requests and empty answers are filtered out before, and they are counted in the problems file.
- `text` is exactly what the AI (or an Ollama model module) returned. It may be wrapped in a ```` ```json ```` block, or have extra text around the JSON.
- The order of the answers is the order the provider returned them. It is **not** guaranteed to be page order. If you compare pages with each other, look them up by `custom_id`.

**`config`** is the whole `config.yaml` as a Python dictionary, with the placeholders already filled in. Read your settings from `config["validation"][<your validator name>]`.

---

## What you return

A list of pairs `(file name, content)`. `get_OCR_results.py` writes them:

| File name you return | Content | What happens |
|---|---|---|
| `"results.jsonl"` (any `.jsonl`) | a dictionary, or a list of dictionaries | one line per dictionary, added to `<output_dir_base>/results<name_suffix>.jsonl` |
| `"review.jsonl"` | a dictionary, or a list of dictionaries | added to the review file in the same way |
| `"problems.jsonl"` | a dictionary, or a list of dictionaries | added to the **problems file** in `state_dir_base` (`problems_to_deal_with<name_suffix>.jsonl`) |
| `"review.csv"` (any `.csv`) | `{"header": [...], "rows": [[...], ...]}`, or a plain list of rows | rows added to a CSV file. With the first form, the header is written when the file is new; the plain list writes no header. |
| any other name | anything | written as one JSON line |

- `name_suffix` (e.g. `-my_run`) is added to the file name **automatically** if it is not already in it. So returning `"results.jsonl"` and returning `f"results{name_suffix}.jsonl"` give the same file.
- Files are always **appended to**, never overwritten.
- If your function crashes with a Python error, the whole batch is logged as `VALIDATOR_FAILED`, and the error is printed.

---

## Step by step

1. **Create the file** `validators/<name>.py`.
   - Use only lower-case letters, digits and underscores: `ocr_nest_cards.py`.
   - **No hyphens.** A file like `ocr_ringing-STORM_PETREL.py` cannot be loaded by the normal `import` line that `get_OCR_results.py` uses.
2. **Write `process_batch()`.** Start from the template below.
3. **Add a config section** with the **same name** as the file:
   ```yaml
   validation:
     active_validator: "ocr_nest_cards"
     ocr_nest_cards:
       rules:
         date_required: true
   ```
4. **Register the validator** in `get_OCR_results.py`. Find the block of `if/elif` lines that chooses the validator and add one branch:
   ```python
   elif active_validator == 'ocr_nest_cards':
       from validators.ocr_nest_cards import process_batch
   ```
   Without this line the script stops with `ERROR: Unknown validator 'ocr_nest_cards'`. This step is needed because the list of validators is written directly into the code.
5. **Write or choose a prompt** that makes the AI return the JSON shape your validator expects, and set `active_prompt_key`.
6. **Using glm-ocr (Ollama)?** Its answers are HTML tables. They are only converted to JSON for the validators listed in `CONVERTERS` in `ai_providers/glm_ocr_provider.py`, so add a converter there too (see [OLLAMA_LOCAL_MODELS.md](OLLAMA_LOCAL_MODELS.md#example-glm_ocr_providerpy)).
7. **Test** ([Testing without paying again](#testing-without-paying-again)).

---

## A complete minimal validator

A working starting point. It reads the JSON, checks one rule, and writes results, review lines and problems.

```python
"""
validators/ocr_nest_cards.py - example validator for a new form type.
"""
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple


def _now() -> str:
    """Timestamp in the format used everywhere in the pipeline."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_json(text: str):
    """Find the JSON in the AI answer (it may be wrapped in ```json ... ``` or other text)."""
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text or "")
    if not match:
        raise ValueError("no JSON found")
    return json.loads(match.group(1))


def process_batch(all_raw_results: List[Dict[str, Any]],
                  config: Dict[str, Any]) -> List[Tuple[str, Any]]:
    # --- settings -------------------------------------------------------
    validation = config.get("validation", {})
    my_name = validation.get("active_validator", "ocr_nest_cards")
    settings = validation.get(my_name, {})
    rules = settings.get("rules", {})

    results, reviews, problems = [], [], []

    for raw in all_raw_results:
        custom_id = raw.get("custom_id", "")
        batch_id = raw.get("batch_id", "")

        # --- 1. read the answer ---------------------------------------------
        try:
            data = _parse_json(raw.get("text", ""))
        except ValueError:          # also covers json.JSONDecodeError
            problems.append({
                "custom_id": custom_id,
                "batch_id": batch_id,
                "reason": "JSON_PARSE_ERROR",
                "raw_text": raw.get("text", "")[:500],
                "timestamp": _now(),
            })
            continue

        if isinstance(data, list):          # the AI returned a list of rows
            data = {"data": data}

        # --- 2. your checks -------------------------------------------------
        reasons = []
        if rules.get("date_required", False) and not data.get("date"):
            reasons.append("missing_date")

        # --- 3. collect the output ------------------------------------------
        record = {**data, "custom_id": custom_id, "batch_id": batch_id}
        results.append(record)

        if reasons:
            reviews.append({
                "custom_id": custom_id,
                "batch_id": batch_id,
                "record_entry": record,
                "reasons": reasons,
                "timestamp": _now(),
            })

    outputs: List[Tuple[str, Any]] = [("results.jsonl", results)]
    if reviews:
        outputs.append(("review.jsonl", reviews))
    if problems:
        outputs.append(("problems.jsonl", problems))
    return outputs
```

---

## Conventions used by the existing validators

Follow these, so that the output of your validator looks like the others. Tools such as `extra_tools/json2csv_v4.py` rely on it.

- **Settings:** read them from `config["validation"][config["validation"]["active_validator"]]`, and switches from its `rules` section.
- **Rules are off unless set to `true`:** `rules.get("my_rule", False)`.
- **Results line:** the parsed data, plus `custom_id` and `batch_id`. For tables, the ringing validator uses `{"data": [rows...], "custom_id", "batch_id", "row_count"}`.
- **Review line:**
  ```json
  {"custom_id": "...", "batch_id": "...", "species_entry": {...}, "reasons": ["short_code", ...], "timestamp": "...Z"}
  ```
  - Use `species_entry` for one row of a table (with `id_row`).
  - Use `record_entry` for a whole card.
- **Reasons:** short lower-case codes without spaces, e.g. `invalid_date`. Explain each one in [VALIDATORS.md](VALIDATORS.md).
- **One review line per row:** if a row has several problems, merge them into one line with several reasons. `ocr_ringing.merge_review_records()` does this.
- **Problems:** `{"custom_id", "batch_id", "reason": "JSON_PARSE_ERROR", "raw_text": first 500 characters, "timestamp"}`.
- **Lexicons:** take the paths from `config["validation"]["species_lexicon_path"]` etc. For fuzzy matching, copy `best_species_match()` from `ocr_ringing.py`.
- **"Bob" functions:** clean-up steps that need several rows or pages at once, such as filling missing species from the previous page or merging duplicate review lines. They belong inside the validator, after the per-row checks.

---

## Testing without paying again

You don't have to send new scans to the AI for every change in your validator:

1. Run one small batch once with `mode="full"` in `get_OCR_results.py`. This saves the raw answers in `data_output/raw/`.
2. Change your validator, then run:
   ```python
   main(batch_ids=["<that batch id>"], mode="process_only")
   ```
   This makes no API call and needs no API key. It reads the saved answers and runs your validator again.
3. Before each test, **delete the old results and review files**. Otherwise the new lines are added to the old ones.

Local Ollama runs always have a raw file, so `process_only` works for them right away.

---

## Checklist

- [ ] `validators/<name>.py` with `process_batch(all_raw_results, config)`; the name uses underscores, not hyphens
- [ ] a config section `validation.<name>` with the settings and `rules`
- [ ] an `elif active_validator == '<name>':` branch in `get_OCR_results.py`
- [ ] a prompt in `prompts.yaml` that returns the JSON shape your validator expects
- [ ] with glm-ocr: a converter in `CONVERTERS` of `glm_ocr_provider.py`
- [ ] tested with `process_only` on a saved raw file
- [ ] review reasons documented in [VALIDATORS.md](VALIDATORS.md)

# Validators

A **validator** takes the answers of the AI, checks and repairs them, adds reference data and flags every row a person should look at. This page describes the three built-in validators in detail: what they expect, every setting, every rule and the meaning of every **review reason**.

To write a new validator, see [ADDING_VALIDATORS.md](ADDING_VALIDATORS.md).

**On this page**

- [How validators work](#how-validators-work)
- [ocr_ringing](#ocr_ringing): ringing sheets
- [ocr_retrap](#ocr_retrap): retrap cards
- [ocr_sightings](#ocr_sightings): sightings sheets
- [Lexicon files](#lexicon-files)
- [Variant files](#variant-files)

---

## How validators work

These points are the same for all three validators:

- **When:** `get_OCR_results.py` runs the validator named in `validation.active_validator`. It hands over **all** successful answers of one batch job in one go. Failed requests and empty answers never reach the validator; they are counted in the problems file instead.
- **Reading the answer:** a surrounding ```` ```json ```` block is removed. Then the text from the first `{` or `[` to the last `}` or `]` is read as JSON. If that fails, the scan is written to the problems file with its first 500 characters.
- **Settings:** each validator reads its own section in `config.yaml` (`validation.<active_validator>`). Inside it, `rules:` switches checks on (`true`) or off (`false`). **A rule that is not listed is off.** Some basic steps always run; they are listed under *Always on* for each validator.
- **Matching names:** species and place names are compared with the lexicon files by *fuzzy matching* (the `rapidfuzz` package). Fuzzy matching gives a similarity score, so small spelling or reading mistakes still find the right entry. `Subalpine Warblr` still matches `Subalpine Warbler`.
- **Output:** a results file (all data) and a review file (only the flagged rows, each with a list of `reasons`). See [SCRIPTS.md](SCRIPTS.md#results-and-review-files).

---

## ocr_ringing

For **ringing sheets**: one page is a table of 23 rows, one ringed bird per row. Use it with prompt `PROMPT_4`.

### What the AI must return

A JSON list with one object per row:

```json
[
  {
    "id_row": 1,
    "serial": "XY",
    "ring_no": "03.337",
    "species": "SUBALPINE WARBLER",
    "ringing": [
      {"date": "08-08-1974", "age": "3", "sex": null, "wing": "60", "weight": "9.4",
       "time": "0610", "initials": "EG", "place": "Wied il-Luq, Buskett", "re": true}
    ]
  },
  ...
]
```

### What happens, in order

For every page (scan):

1. **Carry-over:** empty cells are filled from the rows above, and shortened ring numbers are completed. See [Carry-over](#carry-over) and [Ring number completion](#ring-number-completion).
2. **Ring number shape:** the expected shape of a ring number is decided for this page. See [Ring number check](#ring-number-check).
3. **Every row is checked and enriched:**
   - species matching,
   - ring number check,
   - serial checks,
   - age fixes and check,
   - wing and weight check,
   - date check,
   - sex check,
   - place matching.
4. **Row count check:** the page must have 23 rows.

After all pages:

5. **Species backfill:** pages without a species name get one. See [Species backfill](#species-backfill).
6. **Merging:** review lines about the same row are merged into one line with all reasons.

### Settings

| Key | Default if missing | Meaning |
|---|---|---|
| `verbose` | `false` | Print more details. For every page this includes the ring-number pattern used and an OK/ERROR per row. The `backfill_ring_discontinuity` reason then also contains the details. |
| `species_match_threshold` | `80` | Minimum similarity (0–100) for a species name to be accepted as a match. |
| `location_match_threshold` | `85` | The same, for place names. |
| `measurement_tolerance` | `0.10` | How far wing and weight may be outside the lexicon range: `0.05` = 5 % below the minimum and 5 % above the maximum. |
| `backfill_species` | `false` | `false`, `"AUTO"` or a species name. See [Species backfill](#species-backfill). |
| `ring_no_mode` | `AUTO` | `AUTO` or `MANUAL`. See [Ring number check](#ring-number-check). |
| `ring_no_manual_regex` | `^\d{2}\.\d{3}$` | The pattern for `MANUAL` mode. Write it in single quotes. |
| `carry_over_settings.carry_over_keys` | – | Columns where an empty cell means "same as above". |
| `carry_over_settings.assumed_keys` | – | Columns where the written and the copied value are kept apart. |
| `carry_over_settings.prefix_keys` | – | Columns with shortened numbers that are completed from the row above (`ring_no`). |
| `carry_over_settings.ring_no_max_suffix_length` | `null` | The maximum length of a shortened ring number. See [Ring number completion](#ring-number-completion). |

### Carry-over

On paper, people often leave a cell empty when the value is the same as in the row above, or they write a ditto mark. The AI is told to return `null` for such cells, and carry-over fills them in again.

**`carry_over_keys`** (in this project: `date`, `place`, `initials`, `time`, `serial`):

| Row | Written on the sheet (`date`) | After carry-over |
|---|---|---|
| 1 | `12-07-1994` | `12-07-1994` |
| 2 | *(empty)* | `12-07-1994` (copied from the row above) |
| 3 | `-` | `null` (a dash means "deliberately empty", and copying stops) |
| 4 | *(empty)* | `null` (nothing to copy until a new value is written) |

- Counted as empty: `null`, `""`, `"null"`, `"none"`, `"-"`, `"--"`, `"."`, `"n/a"`, `"nan"`.
- Counted as a deliberate dash: `-`, `--`, `–`, `—`.
- A `serial` that is longer than 3 characters stays in its own row, but it is not copied into the next rows.

**`assumed_keys`** (in this project: `age`): the value is kept in two parts, so you can see what was really written:

| Written | Result |
|---|---|
| `4` | `{"written": "4", "assumed": null}` |
| *(empty, row above had 4)* | `{"written": null, "assumed": "4"}` |

The memory starts fresh on every page (every image id).

### Ring number completion

On the sheets, a ring number is often written in full only once, e.g. `13.011`. The next rows only show the end, e.g. `.012`, `.013`. With `prefix_keys: ["ring_no"]`, these short values are completed from the previous full value.

`ring_no_max_suffix_length` tells how many characters a *shortened* value may have. It depends on your sheets: `3` for values like `.012`, `4` for values like `.2329`. This is an important setting.

Examples with `ring_no_max_suffix_length: 3`:

| Previous ring number | Written in this row | Result |
|---|---|---|
| `13.011` | `.012` | `13.012` |
| `138010` (the reader missed the dot) | `.012` | `138.012` |
| `00.031` | `.7` | `00.037` |
| `9.875` | `.876` | `9.876` |
| `13.012` | `,013` | `13.013` (a comma is turned into a dot first) |
| `13.012` | `14.100` | `14.100` (longer than 3 characters: a new full number, and the new base for the next rows) |

- Only letters and digits are counted; dots are not.
- Values containing one of `* # $ - / " : '` are left exactly as written.
- With `ring_no_max_suffix_length` set to `null` or `0`, an older rule is used: a value is completed when it is shorter than the previous one or starts with a non-letter/non-digit. Setting the number is more reliable.

### Ring number check

Rule `ring_no_validation`, reason `invalid_ring_no`.

- **`ring_no_mode: "MANUAL"`:** every ring number must match `ring_no_manual_regex`. For example, `'^[A-Za-z0-9]+\.\d{3}$'` means "letters or digits, a dot, then exactly three digits".
- **`ring_no_mode: "AUTO"`:** the page decides what is normal:
  1. From every ring number that was written **in full** on the page (not the completed ones), a *shape* is made. Digits become "some digits" and letters become "some letters". So `15.656` has the shape *digits.digits*, and `0A.569` has the shape *digits letters.digits*.
  2. A shape that appears at least twice on the page is accepted. If the page has fewer than 3 fully written numbers, every shape found is accepted.
  3. Shapes with odd characters (two symbols in a row, or a symbol at the start or at the end) are never accepted.
  4. Every row whose ring number does not have an accepted shape gets `invalid_ring_no`.

Example: `15.656`, `12.442`, `15.657`, `15.660` are all *digits.digits*, so that shape is accepted. A row with `15656` (only *digits*, once) is flagged.

### Rules

| Rule | What it checks | Reason |
|---|---|---|
| `serial_validation` | The serial must be one of the allowed series for this species (`mbs_series_options.csv`, found by EURING code or official name). `NULL` in that list allows an empty serial. Species that are not in the list are not checked. | `not_allowed_serial` |
| `serial_validation-simple` | The serial looks unusual: longer than 3 characters, contains a space, `-` or `.`, or ends with a digit. | `strange_serial` |
| `ring_no_validation` | The ring number has the expected shape (see above). | `invalid_ring_no` |
| `low_match_score` | Meant to flag weak species matches. In the current code this switch has no effect, see *Always on*. | – |
| `wing_range` | The wing length lies within the species' range from the lexicon ± `measurement_tolerance`. Only checked when the species was matched and the lexicon has a range. An empty value is fine; a non-number such as `-` is flagged. | `wing_out_of_range` |
| `weight_range` | The same for weight. | `weight_out_of_range` |
| `23_rows` | The page has exactly 23 rows. | `wrong_row_count` (one line for the whole page, without a row) |
| `age_validation` | After the automatic fixes, the age is one of `1 2 3 4 5 6 7 1J 3J`. | `invalid_age` |
| `date_validation` | `.` and `/` in dates are replaced by `-` (`08.08.1974` → `08-08-1974`). The date must then be `DD-MM-YYYY` and a real calendar date. | `invalid_date_format (<value>)`, `invalid_calendar_date (<value>)` |
| `sex_validation` | `f`, `♀`, `female` → `female`; `m`, `♂`, `male` → `male`. Anything else is flagged and left as it is. | `invalid_sex` |

### Always on

These steps run whatever the rule switches say:

- **Species matching.** The species name of every row is matched with the species lexicon. A match adds:
  - `OfficialName`,
  - `EuringCode`,
  - `MatchScore` (0–100),
  - `allowed_wing_range` and `allowed_weight_range` (the lexicon range including the tolerance, e.g. `"66.5-83.0 mm"`).

  A non-empty name that cannot be matched gets `sp_low_match_score`.
- **Age fixes.** Typical reading mistakes are corrected. Dots are removed and upper/lower case is ignored:

  | Read | Becomes |
  |---|---|
  | `15` | `1J` |
  | `35` | `3J` |
  | `25` | `2J` |
  | `Pull`, `Pullus`, `Pul` | `1` |
  | `H` | `4` |
- **Place matching**, when `locations_lexicon_path` is set. Each place is matched with the locations lexicon and the result is added as `place_mbs_dict`:
  - a match gives `{"place": "Wied Zembaq - Birzebbuga", "id": 27}`;
  - no match gives the text `"loc_not_matched"`, plus the reason `loc_not_matched`.
- **Carry-over and species backfill**, when they are configured.

The species lexicon is only loaded when at least one rule is switched on.

### Species backfill

The species name is usually written only on the front side of a sheet, so the back side (the next scan) has no species. `backfill_species` fills it in. It only acts on pages where **no row** has a species name.

| Value | What happens |
|---|---|
| `false` | Nothing. |
| `"Subalpine Warbler"` (any species name) | Every page without a species gets this name. Use it when the whole batch is one species. |
| `"AUTO"` | A page `img_B_P` gets the species of the previous page `img_B_(P-1)`. |

In `AUTO` mode there is a safety check. The first ring number of this page should continue the last ring number of the previous page (+1). If it doesn't, the species is still filled in, but the page gets `backfill_ring_discontinuity`.

After a backfill, species matching, the wing and weight checks and the serial check are run again for the filled rows.

The previous page must be part of the **same batch job**.

### Review reasons

| Reason | Meaning | What to do |
|---|---|---|
| `sp_low_match_score` | The species name was not found in the species lexicon. | Check the name on the scan. If it is a valid spelling, add it to `Alternate_Names` in the lexicon. |
| `invalid_ring_no` | The ring number does not have the expected shape. | Compare with the scan. |
| `not_allowed_serial` | This series is not allowed for this species. | Check serial and species. |
| `strange_serial` | The serial looks unusual (too long, a space, a dash or dot, or ends with a digit). | Check the serial. |
| `invalid_age` | The age is not one of the allowed values. | Check the age. |
| `wing_out_of_range`, `weight_out_of_range` | The measurement is outside the species' range. The row shows the allowed range in `allowed_wing_range` / `allowed_weight_range`. | Check the number, and the species. |
| `invalid_date_format (…)`, `invalid_calendar_date (…)` | The date is not `DD-MM-YYYY`, or it doesn't exist (e.g. `32-01-1994`). The value is shown in brackets. | Check the date. |
| `invalid_sex` | The sex is not male/female (e.g. a `♀` read as `9`). | Check the sex column. |
| `loc_not_matched` | The place was not found in the locations lexicon. | If the place is correct, add the spelling to `Alternative_names` in the locations lexicon. |
| `wrong_row_count` | The page does not have 23 rows. | The AI skipped or split rows. Check the whole page. |
| `backfill_ring_discontinuity` | The species was copied from the previous page, but the ring numbers don't continue. | Check that the two pages really belong together. |
| `backfill_no_prev_scan` | No species on this page, and the previous page is not in this batch. | Use a fixed species name, or process both pages together. |
| `backfill_prev_also_missing` | The previous page has no species either. | Check both pages. |
| `backfill_unparseable_id` | The image id does not end with a page number. | Check the file names. |

### Output example

A results line (one per page, shortened):

```json
{"data": [
   {"id_row": 1, "serial": "8", "ring_no": "15.656", "species": "Spanish Sparrow",
    "ringing": [{"date": "12-07-1994", "age": {"written": "4", "assumed": null}, "sex": "9",
                 "wing": "76.5", "weight": "29.7", "time": "0930", "initials": "06",
                 "place": "Ctra dura", "place_mbs_dict": "loc_not_matched", "re": false}],
    "OfficialName": "Spanish Sparrow", "EuringCode": "15920", "MatchScore": 100.0,
    "allowed_wing_range": "66.5-83.0 mm", "allowed_weight_range": "19.5-34.4 g"},
   "... 22 more rows ..."
 ],
 "custom_id": "img_1_1", "batch_id": "ollama-74144dd4e1d242e1b97dba413d88580a", "row_count": 23}
```

The matching review line:

```json
{"custom_id": "img_1_1", "batch_id": "ollama-74144dd4e1d242e1b97dba413d88580a",
 "species_entry": {"id_row": 1, "serial": "8", "ring_no": "15.656", "...": "the whole row as above"},
 "reasons": ["strange_serial", "invalid_sex", "loc_not_matched", "not_allowed_serial"],
 "timestamp": "2026-08-28T09:04:36.588939Z"}
```

If an answer could not be read as JSON, its results line contains `"raw": "<the answer>"` instead of `"data"`, and a `JSON_PARSE_ERROR` line is written to the problems file.

---

## ocr_retrap

For **retrap cards**: one card per bird, with the ringing details at the top and a table of later recaptures. Use it with prompt `PROMPT_5`.

### What the AI must return

One JSON object per card:

```json
{
  "species": "SUBALPINE WARBLER",
  "ring_no": "106962",
  "ringing_date": "03-08-1993",
  "ringing_wing": "60.5",
  "ringing_weight": "12.9",
  "ringing_age": "3J",
  "ringing_place": "B'Bugia",
  "retraps": [
    {"id_retrap": 1, "retrap_date": "07-08-1993", "retrap_age": "3", "retrap_wing": null,
     "retrap_weight": "14.2", "retrap_time": "0605", "initials": "JMM", "remarks": null}
  ]
}
```

### Settings

| Key | Default if missing | Meaning |
|---|---|---|
| `species_match_threshold` | `80` | As in `ocr_ringing`. |
| `location_match_threshold` | `85` | As in `ocr_ringing`. |
| `measurement_tolerance` | `0.10` | As in `ocr_ringing`. |
| `ring_no_mode` | `AUTO` | `AUTO` = built-in checks; `MANUAL` = `ring_no_manual_regex`. |
| `ring_no_manual_regex` | none | The pattern for `MANUAL` mode. In this project: `'^[A-Za-z0-9]+(?:\.\d{3})?$'`, meaning letters or digits, optionally followed by a dot and three digits. |
| `verbose` | – | Present in the config, but not used by this validator. |

### Rules

| Rule | What it checks | Reason(s) |
|---|---|---|
| `species_match` | Matches the species with the species lexicon and adds `OfficialName`, `EuringCode`, `MatchScore`, `allowed_wing_range`, `allowed_weight_range`. **The wing and weight rules need this rule.** | `sp_low_match_score` (name not found), `missing_species` (no name) |
| `location_match` | Matches `ringing_place` with the locations lexicon and adds `ringing_place_mbs_dict`. | `loc_not_matched`, `missing_place` |
| `low_match_score` | No effect in the current code (as in `ocr_ringing`). | – |
| `wing_range`, `weight_range` | The ringing measurements and every retrap measurement lie within the species' range ± tolerance. Empty values and `-`, `/`, `N/A` are fine. | `ringing_wing_out_of_range`, `ringing_weight_out_of_range`, `retrap_wing_out_of_range`, `retrap_weight_out_of_range` |
| `age_validation` | `ringing_age` and every `retrap_age` is one of `1 2 3 4 5 6 7 1J 3J`. | `invalid_ringing_age`, `invalid_retrap_age` |
| `date_validation` | `ringing_date` and every `retrap_date`: present, `DD-MM-YYYY` (`.` and `/` are replaced by `-`), a real date, and **year 2020 or earlier**. Retraps (in the order of `id_retrap`) must also have a later date than the one before, starting with the ringing date. | `invalid_ringing_date`, `invalid_retrap_date`, `chronological_order_error` |
| `ring_no_validation` | Spaces are removed and the number must have more than 3 characters. `AUTO`: only letters, digits and dots, and the last 3 characters must be digits. `MANUAL`: must match `ring_no_manual_regex`. | `invalid_ring_no` |

### Always on

- A missing or empty ring number always gets `invalid_ring_no`, even when `ring_no_validation` is off.
- The age fixes from `ocr_ringing` (`15` → `1J`, …) are applied to `ringing_age` and every `retrap_age`.

### Review and output

- **Review:** one line per card that has at least one reason: `{"custom_id", "batch_id", "record_entry": <the whole card>, "reasons": [...], "timestamp"}`.
- **Results:** one line per card: the card plus the added fields, `custom_id` and `batch_id`.
- A card whose answer could not be read as JSON is **not** written to the results file. It only appears in the problems file (`JSON_PARSE_ERROR`).

Example review line:

```json
{"custom_id": "img_1_1", "batch_id": "q5vpseewkhyjdpspcpl0syziaf06vhq7b1ct",
 "record_entry": {"species": "SUBALPINE WARBLER", "ring_no": "106962", "...": "...",
                  "OfficialName": "Subalpine Warbler", "EuringCode": "12651", "MatchScore": 100.0,
                  "allowed_wing_range": "53.7-68.2 mm", "allowed_weight_range": "7.6-13.7 g",
                  "ringing_place_mbs_dict": {"place": "Benghisa - Birzebbuga", "id": 39}},
 "reasons": ["retrap_weight_out_of_range"],
 "timestamp": "2026-08-06T13:15:41.488125Z"}
```

### Good to know

- The limit "year 2020 or earlier" is written into the code, because the cards are historical. Newer cards would get a date reason on every date.

---

## ocr_sightings

For **daily sightings sheets**: one sheet per day and place, with a list of species and counts. Use it with prompt `PROMPT_1` (handwritten species names), `PROMPT_2` (pre-printed species list) or `PROMPT_3`.

### What the AI must return

One JSON object per sheet:

```json
{"Location": "Ghadira", "Weather": "...", "Date": "12-04-2019", "Time": "0700-0900", "Obs": "JK",
 "species": [{"Name": "Mallard (feral)", "Number": "3", "Remarks": ""}]}
```

If the AI returns a list of several such objects for one sheet, they are merged into one. The `Location`, `Date` and other header fields must then agree; otherwise the answer counts as unreadable.

### What happens, in order

1. **Counts are cleaned** (always):

   | Written | Becomes |
   |---|---|
   | `c.20` | `20` |
   | `3♂` | `3` |
   | `5+3` | `8` |
   | `5,3` or `5;3` | `8` |
   | `10-20` | `10` (the smaller number) |
   | `5-` or `5+` | `5` |

2. **The date is checked** (rule `date_validation`).
3. **Group names are removed** (always). Species whose name contains one of the words `sp`, `sp.`, `species`, `hirundines`, `broadwing`, `bop` or `ringtail` are left out of the results, because they are not a single species. Examples are "Duck sp." and "Hirundines". Whole words are compared, and upper/lower case is ignored.
4. **Every species is enriched** (always):

   | Field | Meaning |
   |---|---|
   | `OfficialName` | the official name from the lexicon |
   | `EuringCode` | the EURING code |
   | `RareSpec` | `E` if the lexicon marks the species as rare |
   | `MonthMax` | the lexicon's maximum count for the month of the sheet |
   | `ObservedMonth` | the month of the sheet's date |
   | `MatchScore` | 0–1; `1.0` = the name was found exactly |
   | `MatchedVia` | which lexicon name matched (the official one or an alternative) |
   | `LevenshteinDistance` | how many single-letter changes separate the written name from the matched name |

   The best candidate is always taken, even a poor one. Watch `MatchScore`.
5. **The species rules run**, and duplicates on the sheet are looked for.
6. After all sheets: `sudden_change`, then `deduplicate_review`.

### Rules

| Rule | What it checks | Reason |
|---|---|---|
| `date_validation` | The date is present, `DD-MM-YYYY` (`.` and `/` replaced by `-`), a real date, and between 01-01-1930 and 01-06-2026. | `invalid_date_format`, `date_out_of_bounds` (the review line has an empty `species_entry`) |
| `duplicate_species_in_record` | The same species (same EURING code or official name) appears more than once on the sheet. Every copy is flagged. | `duplicate_species_in_record` |
| `low_match_score` | `MatchScore` is below 0.5. | `low_match_score` |
| `name_without_number` | A species has no count. | `name_without_number` |
| `number_has_illegal_chars` | The count is not a plain number (optionally with `c.` before and `+` after). | `number_has_illegal_chars` |
| `suspicious_number_rule` | The count is above 40 and does not end in 0 or 5, e.g. `43`. Large counts are normally rounded, so this is often a misread. | `suspicious_number_rule` |
| `rare_species_E` | The lexicon marks the species as rare (`Rare_Spec` = `E`). | `rare_species_E` |
| `exceeds_monthly_max` | The count is higher than the lexicon maximum for that month. | `exceeds_monthly_max` |
| `sudden_change` | The count is a sudden spike or drop compared with the surrounding days at the same place (below). | `sudden_change` |

### The sudden_change rule

Example: the daily counts 2, 2, 2, 42, 2 – the 42 is flagged.

How it works:

- For every combination of place (`Location`) and species, the daily counts are put in date order.
- A gap of more than `max_gap_days` days starts a new series.
- For each day, the counts of the other days within ± `window_days` form the *context*.
- If there are fewer than `min_required_days` context days, the day is not judged.
- If the context itself jumps around too much, the day is not judged either. "Too much" means that the standard deviation divided by the average is above `volatility`. A **lower** `volatility` therefore means **fewer** flags.
- The day is flagged when its count is at least `min_ratio` times higher (or lower) than the median of the context, **and** differs from it by at least `min_absolute_diff` birds.
- Only sheets of the **same batch job** are compared with each other.

| Setting (`sudden_change_config`) | Default in the code | Used in this project's `config.yaml` |
|---|---|---|
| `window_days` | 3 | 7 |
| `min_required_days` | 4 | 4 |
| `max_gap_days` | 3 | 2 |
| `min_ratio` | 2.5 | 3 |
| `min_absolute_diff` | 3 | 6 |
| `volatility` | 3 | 0.46 |
| `verbose` | false | true (the reason then includes the counts of the surrounding days, and details are printed) |

The comment in `config.yaml` calls its values "the default values". The real defaults are the ones in the middle column; the values in `config.yaml` replace them.

### deduplicate_review

With `deduplicate_review: true`, review lines about the same species on the same sheet (same image id, EURING code and name) are merged into one line that lists all reasons.

### Review and output

- **Review:** one line per flagged species: `{"custom_id", "batch_id", "species_entry": <the species with the added fields>, "reasons": [...], "timestamp"}`.
- **Results:** one line per sheet: the sheet with the enriched species list, `custom_id`, `batch_id` and `enriched_at`.
- An answer that could not be read as JSON is written to the problems file as `JSON_PARSE_FAILED` (note: a different name than in the other two validators). In the results file its line contains `"raw"`.

Example review line:

```json
{"custom_id": "img_38_1", "batch_id": "l6br839grymq67vllkxqhjpe3w27cg7de307",
 "species_entry": {"Name": "Mallard (feral)", "Number": "3", "Remarks": "", "OfficialName": "Feral Mallard",
                   "EuringCode": "", "RareSpec": "E", "MonthMax": 999, "ObservedMonth": 7, "MatchScore": 1.0,
                   "MatchedVia": "Mallard", "LevenshteinDistance": 8},
 "reasons": ["rare_species_E"], "timestamp": "2026-06-08T10:11:08.628108Z"}
```

### Good to know

- The date limits (1930 to 1 June 2026) are written into the code, so newer sheets get `date_out_of_bounds`.
- With `date_validation` on, an unreadable answer also gets a review line `invalid_date_format`, because it has no date.
- `Wader` is in the list of group names, but it is written with a capital letter and is therefore never matched. A name that is just "Wader" is currently kept.

---

## Lexicon files

The lexicons are CSV files in `lexicon/`. Their paths are set in `validation.*_lexicon_path`. When a correct name keeps getting flagged, add the spelling as an alternative name rather than lowering the thresholds.

### Species – mbs_all_species_with_measurements_v3.csv

```
Euring_Code,Rare_Spec,Full_Official_Name,Alternate_Names,Jan_Max,...,Nov_max,Dec_Max,weight_range,wing_range
3700,,Common Quail,Quail,0,0,1,2,1,0,0,0,2,2,1,0,70.2–135.4 g,107.0–116.0 mm
2260,E,White-headed Duck,White headed Duck,0,0,0,0,0,0,0,0,0,0,0,0,657–717 g,152–182 mm
```

| Column | Used by | Meaning |
|---|---|---|
| `Euring_Code` | all | the EURING species code |
| `Full_Official_Name` | all | the official name |
| `Alternate_Names` | all | other spellings. Separate them with `;` (the ringing and retrap validators also accept `,`) |
| `Rare_Spec` | sightings | `E` = rare species |
| `Jan_Max` … `Dec_Max` | sightings | the normal maximum count per month (the November column is spelled `Nov_max`) |
| `wing_range`, `weight_range` | ringing, retrap | `min–max` with any unit, e.g. `107.0–116.0 mm`. `unknown` or empty = no check. |

### Locations – mbs_ringing_locations.csv

```
ID,Location_name, Alternative_names
52,Bahar ic-Caghaq - Naxxar
```

- `ID` is the place id. `Location_name` is the official name. `Alternative_names` holds other spellings, separated by `;` or `,`.
- Before comparing, accents, punctuation and upper/lower case are ignored. An exact match is tried first, then fuzzy matching with `location_match_threshold`.

### Ring series – mbs_series_options.csv

```
bird_name,spc_euringCode,series_options
Alpine Swift,7980,SS
Aquatic Warbler,12420,NULL
```

- `series_options` lists the allowed ring series for the species, separated by `;`.
- `NULL` means that an empty serial is allowed.
- Used by the `serial_validation` rule of `ocr_ringing`.

---

## Variant files

The `validators/` folder contains two more files that are **not** normal validators:

| File | What it is |
|---|---|
| `ocr_ringing-org.py` | An older backup copy of `ocr_ringing.py`, with the older ring-number completion. It is not used anywhere. |
| `ocr_ringing-STORM_PETREL.py` | A variant for Storm Petrel cards. It keeps `ring_no` exactly as read: no completion, no carry-over and no pattern check, because those cards use a different numbering (ranges of rings; see [EXTRA_TOOLS.md](EXTRA_TOOLS.md#storm-petrel-ring-range-expander)). |

⚠ **`ocr_ringing-STORM_PETREL.py` is currently not used**, even if you set `active_validator: "ocr_ringing-STORM_PETREL"`. That value runs the normal `ocr_ringing.py` code:

- The settings are read from a config section named `ocr_ringing-STORM_PETREL`. In `config.yaml` there is a commented-out section header with that name.
- `backfill_species` is always read from `validation.ocr_ringing`.

The variant file cannot be loaded by the normal `import` line in `get_OCR_results.py`, because its name contains a hyphen. To really use it, it would need a name with underscores and its own line in `get_OCR_results.py` (see [ADDING_VALIDATORS.md](ADDING_VALIDATORS.md)).

"""
Model Provider: glm-ocr (Ollama)
================================
Everything specific to ONE Ollama model lives in a module like this one; the
connection, the batch loop and the raw file format come from base_ollama.py.

The module is found through config-ollama.yaml -> model_providers, which maps
the exact run_settings.model_name to this file name. Nothing here is required:
a constant that is missing or set to None falls back to config-ollama.yaml ->
defaults, and a missing postprocess_output() leaves the model output untouched.

Two things this file exists for:

  1. The request settings glm-ocr wants (OCR_PROMPT, OLLAMA_OPTIONS below).
     OCR_PROMPT wins over run_settings.active_prompt_key in config.yaml -
     glm-ocr is a table recognition model and works best with its own short
     instruction, not with the long schema prompt written for Gemini.

  2. postprocess_output(): glm-ocr answers with an HTML table, while the
     validators expect the JSON array described in prompts.yaml. The
     conversion happens here, at the moment the raw file is written, so
     get_OCR_results.py sees exactly the same kind of text it gets from Gemini.
     The untouched answer stays in the raw file as 'raw_response' whenever
     config-ollama.yaml -> run.save_raw_response is on.

The ringing form has a two level header - a group row (MEASUREMENTS, REMARKS
AND / OR RECAPTURES) above the real column names (WING, BILL, ..., PLACE, Re) -
built with colspan. Both levels are expanded into a flat grid before the
columns are matched, otherwise every column after MEASUREMENTS is read one
place off.
"""

import json
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple


# ============================================================================
# REQUEST SETTINGS
# ============================================================================
# Anything set to None (or deleted) falls back to config-ollama.yaml -> defaults.

OCR_PROMPT = "Table Recognition:"

OLLAMA_OPTIONS = {
    "temperature": 0,
    "num_ctx": 8192,
}

OLLAMA_FORMAT = None
KEEP_ALIVE = None
REQUEST_TIMEOUT = None
MAX_RETRIES = None
RETRY_DELAY = None
CHECK_VISION_CAPABILITY = None


# ============================================================================
# COLUMN NAMES
# ============================================================================
# Header text of the scanned form -> field name of the target schema. The key
# is the header with everything except letters and digits removed, so
# "RING NO.", "Ring no" and "ring_no" all end up as "ringno".
#
# Columns the schema does not want (BILL, TARS, TAIL, TRAP, Rec, A.T., G.T.)
# are simply absent here and are therefore dropped, as prompts.yaml asks.

HEADER_ALIASES: Dict[str, str] = {
    "serial": "serial",
    "serialno": "serial",
    "series": "serial",

    "ringno": "ring_no",
    "ring": "ring_no",
    "ringnumber": "ring_no",

    "age": "age",
    "sex": "sex",
    "date": "date",

    "wing": "wing",

    "weight": "weight",
    "wt": "weight",

    "time": "time",

    "initials": "initials",
    "init": "initials",

    "place": "place",
    "locality": "place",
    "remarks": "place",

    "re": "re",
}

# How many rows from the top are searched for a header when the model did not
# wrap it in <thead>.
HEADER_SEARCH_DEPTH = 3

# Below this many recognised columns a row is treated as data, not as a header.
MIN_HEADER_MATCHES = 3

# Cell content that means "same as the row above" or "nothing here". A single
# dash is NOT in this list: prompts.yaml treats it as real data.
EMPTY_CELL_VALUES = {"", '"', "''", '""', "„", "”", "“", "-\"-", ",,", "//", "do", "ditto"}

MALE_VALUES = {"m", "male", "♂"}
FEMALE_VALUES = {"f", "female", "♀"}

DATE_PATTERN = re.compile(r"^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2}|\d{4})$")

CODE_FENCE_PATTERN = re.compile(r"^\s*```[a-zA-Z]*\n|\n?```\s*$")

# Splits a header label into its words, so "Re. Rec. AT GT" can be matched on
# its first one.
HEADER_TOKEN_PATTERN = re.compile(r"[.\s/]+")


# ============================================================================
# HTML TABLE PARSING
# ============================================================================

class _TableExtractor(HTMLParser):
    """
    Collect every <table> of a document, keeping the head and the body apart.

    Each cell is stored as (text, colspan, rowspan); the spans are resolved
    later, when the rows are turned into a rectangular grid.

    Deliberately forgiving: OCR models produce HTML that is not always well
    formed, so an unclosed cell, row or table is finished off rather than
    dropped.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)

        self.tables: List[Dict[str, List[List[Tuple[str, int, int]]]]] = []

        self._table: Optional[Dict[str, List[List[Tuple[str, int, int]]]]] = None
        self._row: Optional[List[Tuple[str, int, int]]] = None
        self._cell: Optional[List[str]] = None
        self._cell_spans: Tuple[int, int] = (1, 1)
        self._in_head = False
        self._row_is_header = False

    @staticmethod
    def _span(attrs, name: str) -> int:
        """Read a colspan / rowspan attribute, defaulting to 1."""
        for key, value in attrs:
            if key == name:
                try:
                    return max(1, int(str(value).strip()))
                except (TypeError, ValueError):
                    return 1

        return 1

    def _close_cell(self) -> None:
        if self._cell is None or self._row is None:
            return

        text = " ".join("".join(self._cell).split())
        self._row.append((text, self._cell_spans[0], self._cell_spans[1]))
        self._cell = None
        self._cell_spans = (1, 1)

    def _close_row(self) -> None:
        if self._row is None or self._table is None:
            return

        self._close_cell()

        # A row of <th> is a header even without <thead> around it.
        section = "head" if (self._in_head or self._row_is_header) else "body"
        self._table[section].append(self._row)

        self._row = None
        self._row_is_header = False

    def _close_table(self) -> None:
        if self._table is None:
            return

        self._close_row()
        self.tables.append(self._table)
        self._table = None
        self._in_head = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "table":
            self._close_table()
            self._table = {"head": [], "body": []}

        elif tag == "thead":
            self._in_head = True

        elif tag in ("tbody", "tfoot"):
            self._in_head = False

        elif tag == "tr" and self._table is not None:
            self._close_row()
            self._row = []
            self._row_is_header = True

        elif tag in ("td", "th") and self._row is not None:
            self._close_cell()
            self._cell = []
            self._cell_spans = (self._span(attrs, "colspan"), self._span(attrs, "rowspan"))

            if tag == "td":
                self._row_is_header = False

        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._close_cell()

        elif tag == "tr":
            self._close_row()

        elif tag == "thead":
            self._close_row()
            self._in_head = False

        elif tag == "table":
            self._close_table()

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def close(self) -> None:
        super().close()

        # An answer that was cut off mid-table still carries usable rows.
        self._close_table()


def expand_rows(
    rows: List[List[Tuple[str, int, int]]],
    repeat_colspan: bool
) -> List[List[str]]:
    """
    Turn rows of spanned cells into a rectangular grid of plain strings.

    A header cell that spans several columns is repeated across all of them,
    because the group label (MEASUREMENTS) applies to every column under it.
    A body cell is written once and the columns it swallowed are left empty,
    because repeating a measured value would invent data.

    rowspan is carried down into the following rows, so a header cell written
    once for two header levels still lines up.
    """
    grid: List[List[str]] = []
    carried: Dict[int, List[Any]] = {}

    for cells in rows:
        row: List[str] = []
        cursor = 0

        for text, colspan, rowspan in cells:
            # A column held by a cell from an earlier row is filled first.
            while cursor in carried:
                row.append(carried[cursor][0])
                cursor += 1

            for offset in range(colspan):
                value = text if (repeat_colspan or offset == 0) else ""
                row.append(value)

                if rowspan > 1:
                    carried[cursor] = [value, rowspan]

                cursor += 1

        while cursor in carried:
            row.append(carried[cursor][0])
            cursor += 1

        grid.append(row)

        for column in list(carried):
            carried[column][1] -= 1

            if carried[column][1] <= 0:
                del carried[column]

    return grid


def parse_html_tables(html_text: str) -> List[Dict[str, List[List[str]]]]:
    """
    Every non-empty <table> of the text, as {'head': grid, 'body': grid}.

    When the model marked no header at all, the split is left to the caller -
    'head' is then empty and every row sits in 'body'.
    """
    parser = _TableExtractor()

    try:
        parser.feed(html_text)
        parser.close()
    except Exception as exc:
        print(f"[WARN] glm_ocr_provider: HTML parsing failed: {exc}")
        return []

    tables: List[Dict[str, List[List[str]]]] = []

    for table in parser.tables:
        if not table["head"] and not table["body"]:
            continue

        tables.append({
            "head": expand_rows(table["head"], repeat_colspan=True),
            "body": expand_rows(table["body"], repeat_colspan=False),
        })

    return tables


# ============================================================================
# CELL VALUES
# ============================================================================

def _normalize_header(text: str) -> str:
    """Header text reduced to letters and digits, for alias lookup."""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def match_header_label(label: str) -> Optional[str]:
    """
    Field name behind one header label, or None.

    Tried whole first ("RING NO." -> ringno), then on the first word, which is
    what makes a merged label such as "Re. Rec. AT GT" resolve to 're'.
    """
    if not label:
        return None

    normalized = _normalize_header(label)

    if normalized in HEADER_ALIASES:
        return HEADER_ALIASES[normalized]

    first_word = HEADER_TOKEN_PATTERN.split(str(label).strip())[0]

    return HEADER_ALIASES.get(_normalize_header(first_word))


def clean_cell(value: Optional[str]) -> Optional[str]:
    """
    Cell text as it should appear in the output.

    An empty cell and a ditto mark both become null, exactly as prompts.yaml
    asks for. A single dash survives - there it is meaningful data.
    """
    if value is None:
        return None

    text = str(value).strip()

    if text.lower() in EMPTY_CELL_VALUES:
        return None

    return text


def normalize_sex(value: Optional[str]) -> Optional[str]:
    """Sex symbols and shorthand turned into 'male' / 'female'."""
    if value is None:
        return None

    text = str(value).strip().lower()

    if text in MALE_VALUES:
        return "male"

    if text in FEMALE_VALUES:
        return "female"

    return value


def normalize_date(value: Optional[str]) -> Optional[str]:
    """
    Reformat a transcribed date to DD-MM-YYYY.

    The forms on the sheets are DD.MM.YY, DD-MM-YY and DD/MM/YY. A two digit
    year is read as 19xx, the same assumption prompts.yaml makes. Anything that
    does not look like a date is left exactly as it was read.
    """
    if value is None:
        return None

    match = DATE_PATTERN.match(str(value).strip())

    if not match:
        return value

    day, month, year = match.groups()

    if len(year) == 2:
        year = f"19{year}"

    return f"{int(day):02d}-{int(month):02d}-{year}"


def normalize_re(value: Optional[str]) -> bool:
    """The 'Re' column is a flag: anything written in it means true."""
    return clean_cell(value) is not None


# ============================================================================
# TABLE -> SCHEMA
# ============================================================================

def build_header_map(head_rows: List[List[str]]) -> Dict[int, str]:
    """
    Column index -> field name.

    With a two level header the lower row holds the real column names, so it is
    read first and the group row above is only consulted when the lower label
    means nothing on its own ("gm." under WEIGHT, "G.M.T." under TIME).

    A field is claimed by the first column that matches it, so the four columns
    the model merged under PLACE do not all become 'place'.
    """
    width = max((len(row) for row in head_rows), default=0)

    header_map: Dict[int, str] = {}
    taken = set()

    for column in range(width):
        field = None

        for row in reversed(head_rows):
            label = row[column] if column < len(row) else ""
            field = match_header_label(label)

            if field:
                break

        if field and field not in taken:
            header_map[column] = field
            taken.add(field)

    return header_map


def split_head_and_body(table: Dict[str, List[List[str]]]) -> Tuple[List[List[str]], List[List[str]]]:
    """
    Head and body of one table, even when the model marked neither.

    With no <thead> and no row of <th>, the first rows are searched for one
    that looks like a header and everything below it becomes the body.
    """
    head_rows = table["head"]
    body_rows = table["body"]

    if head_rows:
        return head_rows, body_rows

    best_index = -1
    best_matches = 0

    for index, row in enumerate(body_rows[:HEADER_SEARCH_DEPTH]):
        matches = len(build_header_map([row]))

        if matches > best_matches:
            best_index = index
            best_matches = matches

    if best_matches < MIN_HEADER_MATCHES:
        return [], body_rows

    return [body_rows[best_index]], body_rows[best_index + 1:]


def rows_to_ringing_records(
    header_map: Dict[int, str],
    data_rows: List[List[str]]
) -> List[Dict[str, Any]]:
    """
    Build the JSON array the ocr_ringing validator expects (prompts.yaml PROMPT_4).

    'species' is left as null: it is printed at the top of the sheet, outside
    the table, so a table recognition model never sees it. The validator fills
    it in through validation.ocr_ringing.backfill_species.

    Blank rows are kept. The sheet has a fixed number of physical rows and the
    validator checks that count, so dropping the empty ones would hide a page
    the model read only half of.
    """
    records: List[Dict[str, Any]] = []

    for row_number, cells in enumerate(data_rows, start=1):
        values: Dict[str, Optional[str]] = {}

        for column_index, field in header_map.items():
            raw_value = cells[column_index] if column_index < len(cells) else None
            values[field] = clean_cell(raw_value)

        records.append({
            "id_row": row_number,
            "serial": values.get("serial"),
            "ring_no": values.get("ring_no"),
            "species": None,
            "ringing": [
                {
                    "date": normalize_date(values.get("date")),
                    "age": values.get("age"),
                    "sex": normalize_sex(values.get("sex")),
                    "wing": values.get("wing"),
                    "weight": values.get("weight"),
                    "time": values.get("time"),
                    "initials": values.get("initials"),
                    "place": values.get("place"),
                    "re": normalize_re(values.get("re")),
                }
            ],
        })

    return records


def convert_ringing_table(table: Dict[str, List[List[str]]]) -> List[Dict[str, Any]]:
    """One recognised table -> the ocr_ringing record list."""
    head_rows, body_rows = split_head_and_body(table)

    if not head_rows:
        return []

    header_map = build_header_map(head_rows)

    if len(header_map) < MIN_HEADER_MATCHES:
        return []

    return rows_to_ringing_records(header_map, body_rows)


# Validators this module knows how to build a JSON array for. A validator that
# is not listed leaves the model output untouched.
CONVERTERS = {
    "ocr_ringing": convert_ringing_table,
    "ocr_ringing-STORM_PETREL": convert_ringing_table,
}


# ============================================================================
# ENTRY POINT
# ============================================================================

def _strip_code_fences(text: str) -> str:
    """Remove a markdown fence the model wrapped its answer in."""
    stripped = text.strip()

    if not stripped.startswith("```"):
        return stripped

    return CODE_FENCE_PATTERN.sub("", stripped).strip()


def _looks_like_json(text: str) -> bool:
    """True when the answer already is the JSON the validator wants."""
    return text.startswith("[") or text.startswith("{")


def _table_size(table: Dict[str, List[List[str]]]) -> int:
    return len(table["body"])


def postprocess_output(raw_text: str, config: Dict[str, Any]) -> str:
    """
    Turn the glm-ocr answer into the text handed to the validator.

    Called by base_ollama while the raw file is being written, so what lands in
    data_output/raw/ is already in the shape get_OCR_results.py expects.

    Anything that cannot be converted is returned unchanged rather than
    dropped: the validator then logs a JSON_PARSE_ERROR carrying the text, and
    the full answer is still in 'raw_response'.
    """
    if not raw_text:
        return raw_text

    text = _strip_code_fences(raw_text)

    # With OCR_PROMPT switched off the model gets the schema prompt from
    # prompts.yaml and answers with JSON on its own - nothing to convert.
    if _looks_like_json(text):
        return text

    active_validator = str(config.get('validation', {}).get('active_validator', '')).strip()
    converter = CONVERTERS.get(active_validator)

    if converter is None:
        print(
            f"[WARN] glm_ocr_provider: no HTML -> JSON converter for validator "
            f"'{active_validator}', the model output is left unchanged."
        )
        return text

    tables = parse_html_tables(text)

    if not tables:
        print("[WARN] glm_ocr_provider: no <table> in the model output, left unchanged.")
        return text

    # A sheet can carry a small header table above the real one.
    records = converter(max(tables, key=_table_size))

    if not records:
        print("[WARN] glm_ocr_provider: no recognisable header row in the table, output left unchanged.")
        return text

    return json.dumps(records, ensure_ascii=False)

#!/usr/bin/env python3
"""
expand_ring_sequences.py

Post-processor for OCR'd ringing cards where one table row can hold a whole ring
number SEQUENCE instead of a single ring. Produces JSONL with exactly one ring
number per record, keeping every other field of the parent row.

Handles the writing habits found on the cards:

    "0.151-200"      -> 0.151 .. 0.200          (range on one row)
    ".201-250"       -> prefix inherited from the last explicit one
    "2.171-200"      -> 2.171 .. 2.200          (end omits the prefix)
    "216.2971-"      -> range start, END IS ON THE NEXT ROW
    "3000"           -> ...that end row; it gets consumed, not emitted
    "0.958-1.000"    -> range crosses a prefix group (flagged)
    "212.2666"       -> plain single ring, passes through

Ring numbers are modelled as  prefix * 10**SERIAL_WIDTH + serial, i.e. the dot
is treated as a group separator. That makes both card styles work with the same
arithmetic: 216.2971 -> 2162971 (width 4) and 0.958 -> 958 (width 3).

Nothing is silently repaired: every assumption the script makes is recorded in
the per-row "_flags" list and in the QC file.
"""

import copy
import json
import re
from collections import Counter
from pathlib import Path

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------
CONFIG = {
    # --- paths -------------------------------------------------------------
    "input_path": r"C:\Birdlife_projects\OCR_universal_project\data_output\output\ringing-storm_petrels-HARD-test\results-ringing-storm_petrels-HARD-test.jsonl",
    "output_path": r"C:\Birdlife_projects\OCR_universal_project\data_output\output\ringing-storm_petrels-HARD-test\output-expanded.jsonl",
    "qc_path": "output-expanded.qc.json",   # None = don't write the QC file
    "flat_output_path": None,               # e.g. "output-flat.jsonl" for DB import

    # --- ring number model -------------------------------------------------
    # digits of the serial group (the part after the dot).
    # None = detect per JSONL line: 4 for 216.2971, 3 for 0.151
    "serial_width": None,
    "serial_width_fallback": 4,

    # --- sequence resolution ----------------------------------------------
    "max_range": 1000,              # refuse to expand anything longer (misread guard)
    "warn_range_above": 100,        # still expanded, but flagged for review
    "consume_continuation_row": True,   # "216.2971-" + next row "3000" = one sequence
    "allow_prefix_carry": True,     # 0.958-1.000 -> end borrows the next prefix group
    "allow_dot_as_dash": True,      # ".201.250" with no dash -> last dot read as dash
    "warn_on_context_mismatch": True,   # continuation row has a different date/place

    # --- output shape ------------------------------------------------------
    "keep_expansion_metadata": True,
    "original_id_key": "original_id_row",
    "raw_ring_key": "ring_no_raw",
    "meta_key": "_seq",
    "flags_key": "_flags",

    # --- data quality checks ----------------------------------------------
    # fields that describe ONE bird; copying them across a sequence is flagged
    "per_bird_fields": ["wing", "weight", "sex", "time"],
    "numeric_fields": ["wing", "weight"],
    # fields compared between a range start row and its continuation row
    "context_fields": ["date", "place", "initials"],
}

# --------------------------------------------------------------------------
# ring number parsing
# --------------------------------------------------------------------------
DASH_CHARS = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u02d7~"
_DASH_RE = re.compile("[" + re.escape(DASH_CHARS) + "]")
_ALLOWED_RE = re.compile(r"^[\d.\-]+$")


def normalise_token(raw):
    """Clean an OCR ring_no string without destroying its structure."""
    if raw is None:
        return ""
    text = _DASH_RE.sub("-", str(raw))
    text = re.sub(r"\s+", "", text)
    text = text.strip("()[]{}|,;:'\"")
    text = re.sub(r"-{2,}", "-", text)
    return text


def split_side(text, width, flags):
    """Parse one endpoint of a range. Returns (prefix|None, serial, explicit)."""
    if not text:
        return None
    groups = [g for g in text.split(".") if g]
    if not groups or not all(g.isdigit() for g in groups):
        return None
    serial_txt = groups[-1]
    if len(groups) >= 2:                       # "216.2971" / "0.151"
        return (int("".join(groups[:-1])), int(serial_txt), True)
    if len(serial_txt) > width:                # "2162971" - separator lost by OCR
        flags.append("split_oversized_group")
        return (int(serial_txt[:-width]), int(serial_txt[-width:]), True)
    return (None, int(serial_txt), False)      # ".2667" / "3000" - inherit prefix


def parse_token(raw, width):
    """
    Returns a dict:
        start / end : endpoint tuples or None
        open_end    : True when a trailing dash promises the end on the next row
        flags       : parsing assumptions
    """
    norm = normalise_token(raw)
    out = {"raw": raw, "norm": norm, "start": None, "end": None,
           "open_end": False, "flags": []}

    if not norm:
        out["flags"].append("empty_ring_no")
        return out
    if not _ALLOWED_RE.match(norm):
        out["flags"].append("unparsable_ring_no")
        return out

    open_end = norm.endswith("-")
    body = norm[:-1] if open_end else norm
    body = body.lstrip("-")
    if not body:
        out["flags"].append("unparsable_ring_no")
        return out

    if "-" in body:
        left, _, right = body.partition("-")
        if "-" in right:
            out["flags"].append("multiple_dashes")
            right = right.split("-")[0]
    else:
        left, right = body, ""
        groups = [g for g in body.split(".") if g]
        # a genuine ring has exactly ONE group separator, so one dot too many
        # means a dash was misread as a dot. The leading dot of an inherited
        # prefix (".201") counts as that one separator.
        separators = len(groups) - 1 + (1 if body.startswith(".") else 0)
        if CONFIG["allow_dot_as_dash"] and separators >= 2:
            out["flags"].append("dot_read_as_dash")
            left = ("." if body.startswith(".") else "") + ".".join(groups[:-1])
            right = groups[-1]

    start = split_side(left, width, out["flags"])
    end = split_side(right, width, out["flags"]) if right else None
    if start is None:
        out["flags"].append("unparsable_ring_no")
        return out
    if right and end is None:
        out["flags"].append("unparsable_range_end")

    out["start"] = start
    out["end"] = end
    out["open_end"] = open_end and end is None
    return out


def side_value(side, inherited_prefix, width):
    prefix, serial, explicit = side
    if not explicit:
        prefix = inherited_prefix
    if prefix is None:
        return None
    return prefix * (10 ** width) + serial


def format_ring(value, width):
    return "{}.{:0{w}d}".format(value // (10 ** width), value % (10 ** width), w=width)


def detect_width(rows):
    """Serial width = most common length of the group that follows a dot."""
    dotted, plain = Counter(), Counter()
    for row in rows:
        norm = normalise_token(row.get("ring_no"))
        if not norm or not _ALLOWED_RE.match(norm):
            continue
        for chunk in norm.split("-"):
            groups = [g for g in chunk.split(".") if g]
            if not groups:
                continue
            if len(groups) >= 2:
                dotted[len(groups[-1])] += 1
            else:
                plain[len(groups[0])] += 1
    if dotted:
        return dotted.most_common(1)[0][0]
    if plain:
        return plain.most_common(1)[0][0]
    return CONFIG["serial_width_fallback"]


# --------------------------------------------------------------------------
# row level helpers
# --------------------------------------------------------------------------
def ringing_events(row):
    events = row.get("ringing")
    return [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []


def context_of(row):
    events = ringing_events(row)
    if not events:
        return {}
    return {f: events[0].get(f) for f in CONFIG["context_fields"]}


def consumed_row_payload(start_row, consumed_row):
    """
    A continuation row only supplies the range end, the rest of it is dropped.
    Report anything non-empty on it that the start row does not already say, so
    OCR column shifts (a ring number sitting in `wing`, ...) are never lost.
    """
    extra = {}
    for key, value in consumed_row.items():
        if key in ("id_row", "ring_no", "ringing"):
            continue
        if value is None or value == "" or value is False:
            continue
        if value != start_row.get(key):
            extra[key] = value
    first = (ringing_events(start_row) or [{}])[0]
    for event in ringing_events(consumed_row):
        for key, value in event.items():
            if value is None or value == "" or value is False:
                continue
            if value != first.get(key):
                extra["ringing." + key] = value
    return extra


def measurement_flags(row):
    """Per-bird values present + values that do not look numeric (column shift)."""
    present, broken = set(), set()
    for event in ringing_events(row):
        for field in CONFIG["per_bird_fields"]:
            if event.get(field) not in (None, ""):
                present.add(field)
        for field in CONFIG["numeric_fields"]:
            value = event.get(field)
            if value in (None, ""):
                continue
            try:
                float(str(value).replace(",", "."))
            except ValueError:
                broken.add(field)
    return sorted(present), sorted(broken)


# --------------------------------------------------------------------------
# expansion
# --------------------------------------------------------------------------
def expand_table(rows, width, custom_id):
    """Expand one JSONL line's `data` list. Returns (new_rows, qc_entries)."""
    out_rows, qc = [], []
    last_prefix = None
    idx = 0
    new_id = 0

    while idx < len(rows):
        row = rows[idx]
        token = parse_token(row.get("ring_no"), width)
        flags = list(token["flags"])
        source_ids = [row.get("id_row")]
        step = 1

        start_val = side_value(token["start"], last_prefix, width) if token["start"] else None
        end_val = None
        refused_end = None
        consumed_extra = {}

        if start_val is None:
            if token["start"] is not None:
                flags.append("unknown_prefix")
            values = [None]
        else:
            base_prefix = start_val // (10 ** width)

            if token["end"] is not None:
                end_val = side_value(token["end"], base_prefix, width)
            elif token["open_end"]:
                end_val, step, extra = resolve_continuation(
                    rows, idx, start_val, base_prefix, width
                )
                flags.extend(extra)
                if step == 2:
                    source_ids.append(rows[idx + 1].get("id_row"))
                    consumed_extra = consumed_row_payload(row, rows[idx + 1])
                    if consumed_extra:
                        flags.append("continuation_row_had_data")

            if end_val is not None and end_val < start_val and CONFIG["allow_prefix_carry"]:
                carried = end_val + (10 ** width)
                if carried >= start_val and carried - start_val + 1 <= CONFIG["max_range"]:
                    end_val = carried
                    flags.append("prefix_carry")

            if end_val is not None and end_val < start_val:
                flags.append("range_end_before_start")
                end_val = None

            if end_val is None:
                values = [start_val]
            elif end_val - start_val + 1 > CONFIG["max_range"]:
                flags.append("range_too_large_not_expanded")
                refused_end = end_val
                values = [start_val]
            else:
                values = list(range(start_val, end_val + 1))
                if len(values) > CONFIG["warn_range_above"]:
                    flags.append("large_range")
                if end_val // (10 ** width) != base_prefix:
                    flags.append("range_crosses_prefix_group")

            last_prefix = values[-1] // (10 ** width)

        present, broken = measurement_flags(row)
        if len(values) > 1 and present:
            flags.append("per_bird_fields_copied:" + "+".join(present))
        flags.extend("non_numeric_" + f for f in broken)

        total = len(values)
        for position, value in enumerate(values, start=1):
            new_id += 1
            record = {"id_row": new_id, CONFIG["original_id_key"]: row.get("id_row")}
            for key, val in row.items():
                if key != "id_row":
                    record[key] = copy.deepcopy(val)
            if value is not None:
                record["ring_no"] = format_ring(value, width)
            if CONFIG["keep_expansion_metadata"]:
                record[CONFIG["raw_ring_key"]] = row.get("ring_no")
                record[CONFIG["meta_key"]] = {
                    "expanded": total > 1,
                    "index": position,
                    "size": total,
                    "start": format_ring(values[0], width) if values[0] is not None else None,
                    "end": format_ring(values[-1], width) if values[-1] is not None else None,
                    "source_id_rows": source_ids,
                }
                record[CONFIG["flags_key"]] = flags
            out_rows.append(record)

        if flags or total > 1:
            entry = {
                "custom_id": custom_id,
                "source_id_rows": source_ids,
                "ring_no_raw": row.get("ring_no"),
                "resolved": [
                    format_ring(values[0], width) if values[0] is not None else None,
                    format_ring(values[-1], width) if values[-1] is not None else None,
                ],
                "count": total,
                "serial_width": width,
                "flags": flags,
            }
            if refused_end is not None:
                entry["refused_end"] = format_ring(refused_end, width)
            if consumed_extra:
                entry["continuation_row_data"] = consumed_extra
            qc.append(entry)

        idx += step

    return out_rows, qc


def resolve_continuation(rows, idx, start_val, base_prefix, width):
    """A trailing dash: try to read the range end from the following row."""
    if idx + 1 >= len(rows):
        return None, 1, ["dangling_dash_at_table_end"]

    nxt = rows[idx + 1]
    ntok = parse_token(nxt.get("ring_no"), width)
    if ntok["start"] is None or ntok["end"] is not None or ntok["open_end"]:
        return None, 1, ["dangling_dash"]

    candidate = side_value(ntok["start"], base_prefix, width)
    if candidate is None:
        return None, 1, ["dangling_dash"]
    if candidate < start_val and CONFIG["allow_prefix_carry"]:
        candidate += 10 ** width
    if candidate < start_val or candidate - start_val + 1 > CONFIG["max_range"]:
        return None, 1, ["dangling_dash_implausible_end"]

    flags = ["range_end_from_next_row"]
    if CONFIG["warn_on_context_mismatch"] and context_of(rows[idx]) != context_of(nxt):
        flags.append("continuation_context_mismatch")
    return candidate, 2, flags


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------
def read_records(path):
    """JSONL first; falls back to concatenated / pretty-printed JSON objects."""
    text = Path(path).read_text(encoding="utf-8")
    records, clean = [], True
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            clean = False
            break
    if clean and records:
        return records

    decoder, records, pos = json.JSONDecoder(), [], 0
    while pos < len(text):
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        if pos >= len(text):
            break
        obj, pos = decoder.raw_decode(text, pos)
        records.append(obj)
    return records


def main():
    records = read_records(CONFIG["input_path"])
    out_records, all_qc = [], []
    total_in = total_out = 0

    for record in records:
        rows = record.get("data")
        if not isinstance(rows, list):
            out_records.append(record)
            continue

        width = CONFIG["serial_width"] or detect_width(rows)
        new_rows, qc = expand_table(rows, width, record.get("custom_id"))
        all_qc.extend(qc)
        total_in += len(rows)
        total_out += len(new_rows)

        new_record = dict(record)
        new_record["data"] = new_rows
        new_record["row_count"] = len(new_rows)
        new_record["source_row_count"] = len(rows)
        new_record["serial_width"] = width
        out_records.append(new_record)

    with open(CONFIG["output_path"], "w", encoding="utf-8") as handle:
        for record in out_records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    if CONFIG["flat_output_path"]:
        with open(CONFIG["flat_output_path"], "w", encoding="utf-8") as handle:
            for record in out_records:
                envelope = {k: v for k, v in record.items() if k != "data"}
                for row in record.get("data", []):
                    handle.write(json.dumps({**row, **envelope}, ensure_ascii=False) + "\n")

    # a physical ring exists once, so a repeated number means a misparse
    emitted = Counter()
    for record in out_records:
        for row in record.get("data", []):
            if isinstance(row.get("ring_no"), str):
                emitted[row["ring_no"]] += 1
    duplicates = sorted(ring for ring, count in emitted.items() if count > 1)

    counter = Counter(
        flag.split(":")[0] for entry in all_qc for flag in entry["flags"]
    )

    if CONFIG["qc_path"]:
        Path(CONFIG["qc_path"]).write_text(
            json.dumps({
                "summary": {
                    "tables": len(records),
                    "rows_in": total_in,
                    "rows_out": total_out,
                    "flag_counts": dict(counter.most_common()),
                    "duplicate_ring_no_count": len(duplicates),
                },
                "duplicate_ring_no": duplicates,
                "rows": all_qc,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print("tables      : {}".format(len(records)))
    print("rows in/out : {} -> {}".format(total_in, total_out))
    print("flagged     : {}".format(len(all_qc)))
    for flag, count in counter.most_common():
        print("  {:<34} {}".format(flag, count))
    print("duplicates  : {}".format(len(duplicates)))


if __name__ == "__main__":
    main()
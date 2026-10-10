#!/usr/bin/env python3
"""
silent_errors.py -- validation and logic layer.

Rule-based detector for "silent" OCR errors in ringing card exports -- the
subtle misreads that produce structurally valid values and therefore survive
field-level validation.

No AI, no network. Everything is derived from the file itself: the script first
infers the latent structure of the archive (which serial owns which ring-number
block, in which period, at which sites), then looks for records that violate it.

Key design point
----------------
A whole-card systematic misread is invisible to any "minority within its own
group" heuristic, because inside the card the wrong value is unanimous. Every
rule here that targets that failure mode therefore builds its context from
OTHER cards only.

This module is self-contained: it reads CSV or JSONL, applies the rules and
writes the review output. It never imports matplotlib and never draws anything.
`silent_errors_plots.py` is an optional add-on layered on top by
`silent_errors_run.py`.

Standalone:
    edit CONFIG below, then `python3 silent_errors.py`

From the workflow -- same contract as run_post_ai_eval() in the AI post-eval
scripts (openai_post_eval.py / gemini_post_eval.py):
    from silent_errors import run_silent_errors
    result = run_silent_errors(input_path="results.jsonl")
    result = run_silent_errors(input_jsonl=jsonl_text)          # str with JSONL
    result = run_silent_errors(input_jsonl=list_of_dicts,       # list of records
                               review_jsonl=review_rows,        # this run's review rows
                               record_shape="post_eval")        # AI-shaped records
    result["output_records"]  -> list of dicts, one per flagged row
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# =============================================================================
# CONFIG -- single source of truth, no CLI arguments
# =============================================================================

CONFIG = {
    # --- input ----------------------------------------------------------
    # Standalone input. From the workflow, run_silent_errors() gets the input
    # as a parameter instead (input_path / input_jsonl) and this is not used.
    # Either the flat CSV produced by the jsonl->csv converter, or the raw
    # batch JSONL itself. "auto" decides on the file extension.
    "input_path": r"C:\Birdlife_projects\OCR_universal_project\data_output\output\ringing-subalpine_warblers-full\results-ringing-subalpine_warblers-full.jsonl",
    "input_format": "auto",         # "auto" | "csv" | "jsonl"
    "encoding": "utf-8-sig",

    # --- review filter ---------------------------------------------------
    # filter_review_data is the only switch, in the standalone run and in the
    # workflow alike. When True, the review comes from the review rows passed
    # to run_silent_errors() in a variable (the workflow passes this run's
    # review rows); only when none were passed, from the file in review_path.
    "filter_review_data": False,
    "review_path": r"",

    # --- output -----------------------------------------------------------
    # The result always comes back in the variable run_silent_errors()
    # returns; this switch decides whether it is ALSO written to disk.
    "save_output_to_disk": False,
    "output_dir": Path(__file__).resolve().parent / "output_data_silent_errors",
    # File name: <output_stem>_<YYYYmmdd_HHMMSS>.<jsonl|csv>. "local_noai"
    # keeps it apart from the AI post-eval output (silent_errors_<ts>.jsonl).
    # Nothing is written when no row is flagged.
    "output_stem": "silent_error_flags_local_noai",
    # Shape of the JSONL records -- in the returned variable and the .jsonl file:
    #   "review"    -> one line per flagged RECORD, shaped like review-*.jsonl:
    #                  custom_id / batch_id / species_entry / reasons / timestamp
    #   "post_eval" -> one line per flagged RECORD, shaped like the AI post-eval
    #                  output: custom_id / id_row / suspicious_fields / reasons
    #                  + a note with the evidence (see post_eval_output)
    "record_shape": "post_eval",           # "review" | "post_eval"
    # What goes to disk:
    #   "jsonl" -> the records above
    #   "csv"   -> one line per flag (custom_id, id_row, rule, severity, ...)
    # The returned variable holds the JSONL records either way.
    "output_format": "jsonl",           # "jsonl" | "csv"
    "jsonl_output": {
        # record_shape "review": rules that hit the same row are merged into
        # one `reasons` list, the same shape merge_review_records produces
        # downstream.
        "include_details": True,    # keep field/severity/evidence per rule
    },
    "post_eval_output": {
        # record_shape "post_eval": the evidence of every rule that hit the row
        # goes into one note under this key. "ai_note" is the AI's own key, so
        # post_eval_runner copies it into review the same way as the AI note.
        "note_key": "ai_note",
    },

    # --- column names ---------------------------------------------------
    # These are the flattened names: the JSONL loader produces exactly the
    # same ones as the converter, so both inputs land on this single mapping.
    "columns": {
        "card": "custom_id",        # scan / card identifier
        "row": "id_row",            # row index within the card
        "serial": "serial",         # ring series prefix -- primary target
        "ring_no": "ring_no",       # ring number, European decimal notation
        "date": "ringing_date",
        "ringer": "ringing_initials",
        "place": "ringing_place_mbs_dict_place",
        "place_raw": "ringing_place",
        "species": "species",
    },
    # Optional -- carried into the JSONL output when present, never required.
    "batch_column": "batch_id",
    # "%d-%m-%Y" is the notation the raw JSONL uses ("03-08-1994"); it is
    # appended last, so nothing that parsed before parses differently now.
    "date_formats": ["%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d-%m-%Y"],

    # ring_no is written as <thousands>.<units>, e.g. 5.034 -> 5034,
    # 2.05 -> 2050 (trailing zeros dropped by the exporter).
    "ring_no_unit_digits": 3,
    "ring_no_prefix_digits": 2,     # '00.002' -- used for canonical output only

    # An empty serial is a deliberate NULL on the card (the '-' written in the
    # Series column), not a value that went missing. True -> such rows are
    # never reported as incomplete and never treated as a series of their own,
    # so they neither get their own entry in `struct` nor vote in R2 / R7.
    "blank_serial_is_null": True,

    # Only consulted for CSV input + review-shaped output, where the nested
    # shape has to be rebuilt from flat columns. With JSONL input the original
    # object is passed through untouched and this is ignored.
    "unflatten": {
        "ringing_prefix": "ringing_",
        "nested": {
            "age_written": ("age", "written"),
            "age_assumed": ("age", "assumed"),
            "place_mbs_dict_place": ("place_mbs_dict", "place"),
            "place_mbs_dict_id": ("place_mbs_dict", "id"),
        },
        "skip": ["custom_id", "batch_id", "row_count"],
    },

    # --- rules ----------------------------------------------------------
    "rules": {
        # R1: rare ring-number block that can be remapped by one digit edit
        # into a same-sized hole in the serial's dominant block.
        "orphan_block_gapfit": {
            "enabled": True,
            "max_block_share": 0.30,     # block holds <=30 % of the serial
            "max_cards": 2,              # and lives on at most 2 cards
            "gap_slack": 0,              # 0 = require exact endpoint match
        },
        # R2: serial disagrees with its cross-card contextual neighbours.
        "cross_card_context": {
            "enabled": True,
            "ring_window": 40,           # +/- N ring numbers
            "use_same_date": True,       # also treat same-date rows as context
            "min_context": 3,
            "max_self_share": 0.15,      # flag if own serial <=15 % of context
        },
        # R3: serial used outside the date envelope established elsewhere.
        "era_violation": {
            "enabled": True,
            "min_rows_for_envelope": 8,
            "margin_days": 120,
        },
        # R4: ring sequence breaks across consecutive cards.
        "card_sequence_break": {
            "enabled": True,
            "max_expected_gap": 60,      # numbers legitimately skipped
        },
        # R5: the composite key must be unique.
        "duplicate_key": {"enabled": True},
        # R6: key fields empty or unparseable.
        "missing_key_fields": {"enabled": True},
        # R7: serial is a minority inside its date+ringer+place group AND the
        # ring block does not back it up.
        "group_minority": {"enabled": True, "min_group": 4},
    },
}


# =============================================================================
# LOADING / NORMALISATION
# =============================================================================

def parse_ring_no(raw: str, unit_digits: int) -> float | None:
    """
    Ring numbers are written <thousands>.<units>, e.g. '00.002' -> 2,
    '05.034' -> 5034, '02.05' -> 2050 (the exporter drops trailing zeros).

    A value with NO separator is not a small ring number -- it is a whole
    thousand whose decimals were all zeros and were lost when the exporter
    round-tripped the field through a float: '02.000' -> 2.0 -> '2'.
    Reading such a value as units silently misplaces it by a factor of 1000,
    which then shows up downstream as a bogus duplicate key. So: '2' -> 2000.
    """
    s = str(raw).strip()
    if not s or not re.fullmatch(r"\d{1,3}(\.\d{1,3})?", s):
        return None
    if "." in s:
        thousands, units = s.split(".", 1)
        return int(thousands) * (10 ** unit_digits) + int(units.ljust(unit_digits, "0"))
    return int(s) * (10 ** unit_digits)


def format_ring_no(value, prefix_digits: int, unit_digits: int) -> str:
    """Canonical zero-padded notation for review output: 2 -> '00.002'."""
    if value is None or pd.isna(value) or value < 0:
        return ""
    n = int(value)
    thousands, units = divmod(n, 10 ** unit_digits)
    return f"{thousands:0{prefix_digits}d}.{units:0{unit_digits}d}"


def parse_date(raw: str, formats: list[str]):
    s = str(raw).strip()
    for fmt in formats:
        try:
            return pd.to_datetime(s, format=fmt)
        except (ValueError, TypeError):
            continue
    return pd.NaT


def card_order_key(card_id: str) -> tuple:
    """Sort cards by their trailing integer so img_3_9 precedes img_3_10."""
    nums = [int(n) for n in re.findall(r"\d+", str(card_id))]
    return tuple(nums) if nums else (0,)


def flatten_dict(d: dict, parent_key: str = "", sep: str = "_") -> dict:
    """Flatten nested dicts, e.g. age.written -> age_written."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            if v:
                items.extend(flatten_dict(v, new_key, sep=sep).items())
            else:
                items.append((new_key, None))
        else:
            items.append((new_key, v))
    return dict(items)


def _as_text(value) -> str:
    """Match the CSV round-trip exactly: None -> '', everything else -> str()."""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value)


def parse_jsonl(text: str, source_name: str) -> list[dict]:
    """
    JSONL text -> list of dicts. Invalid lines are skipped and reported, the
    same behaviour as parse_jsonl() in the AI post-eval scripts. A leading BOM
    is dropped, so text from a utf-8-sig file parses either way.
    """
    records, invalid_lines = [], []
    for line_no, line in enumerate(text.lstrip("\ufeff").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            invalid_lines.append(line_no)
    if invalid_lines:
        print(f"warning: {source_name}: {len(invalid_lines)} invalid JSON line(s) "
              f"skipped, first: {invalid_lines[:10]}")
    return records


def load_review_keys(cfg: dict, review_path=None,
                     review_jsonl=None) -> set[tuple[str, str]] | None:
    """
    (custom_id, id_row) pairs already sitting in review -- the black list.
    None = the review filter is off for this run.

    filter_review_data in CONFIG is the only switch: False -> no filtering,
    whatever was passed in. True -> the review is taken from, in this order:
      1. review_jsonl       -- rows handed over in a variable (the workflow
                               passes this run's review rows)
      2. review_path        -- a file passed as a parameter
      3. CONFIG review_path -- a file, for the standalone run
    Once review_jsonl is given, no file is read -- not even when it is empty.
    """
    if not cfg["filter_review_data"]:
        return None

    if review_jsonl is not None:
        if isinstance(review_jsonl, str):
            records = parse_jsonl(review_jsonl, "review_jsonl (str)")
        else:
            records = list(review_jsonl)
        source = "review rows from the input variable"
    else:
        review_path = review_path or cfg["review_path"]
        if not review_path or not os.path.exists(review_path):
            print(f"warning: review filter is on but the file was not found: "
                  f"{review_path!r} - nothing filtered")
            return set()
        with open(review_path, "r", encoding="utf-8") as fh:
            records = parse_jsonl(fh.read(), str(review_path))
        source = str(review_path)

    keys = set()
    for record in records:
        card = record.get("custom_id")
        # id_row lives inside 'species_entry'; tolerate a flat variant too
        entry = record.get("species_entry") or {}
        row_id = entry.get("id_row", record.get("id_row"))
        if card is not None and row_id is not None:
            keys.add((str(card), str(row_id)))
    print(f"review filter ({source}): {len(keys)} unique records black-listed")
    return keys

def read_csv_rows(path, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """Flat CSV -- the format the converter emits. No source objects to keep."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False,
                     encoding=cfg["encoding"])
    return df, {}


def read_jsonl_rows(records: list[dict]) -> tuple[pd.DataFrame, dict]:
    """
    Raw batch JSONL records -> the same flat frame the converter would have
    written, plus the untouched source objects keyed by (custom_id, id_row) so
    the JSONL output can hand back the original nested species_entry verbatim.
    """
    rows, sources = [], {}
    for record in records:
        card = record.get("custom_id")
        base = {k: v for k, v in record.items() if k != "data"}

        for item in record.get("data", []):
            row_id = item.get("id_row")
            sources[(str(card), str(row_id))] = {
                "batch_id": record.get("batch_id"),
                "species_entry": item,
            }
            item_base = {k: v for k, v in item.items() if k != "ringing"}
            for ringing in (item.get("ringing") or [{}]):
                rows.append({**base, **item_base,
                             **flatten_dict(ringing, parent_key="ringing")})

    df = pd.DataFrame(rows)
    for column in df.columns:                       # same dtypes as read_csv
        df[column] = df[column].map(_as_text)
    return df, sources


def read_input(cfg: dict, input_path=None, input_jsonl=None):
    """
    Same input contract as prepare_input() in the AI post-eval scripts: exactly
    one of input_path / input_jsonl. input_jsonl is JSONL text (str) or a list
    of dicts -- the workflow passes the results records it has just written.
    input_path may also be the flat CSV (input_format decides, "auto" = by the
    file extension).

    Returns (df, sources, input_count, source_name). input_count is the number
    of JSON records, as in the AI scripts; for CSV input the number of cards.
    """
    if (input_path is None) == (input_jsonl is None):
        raise ValueError("Provide exactly one of input_path / input_jsonl")

    if input_jsonl is not None:
        fmt = "jsonl"
        if isinstance(input_jsonl, str):
            source_name = "input_jsonl (str)"
            records = parse_jsonl(input_jsonl, source_name)
        else:
            source_name = "input_jsonl (list)"
            records = list(input_jsonl)
    else:
        source_name = str(input_path)
        fmt = cfg["input_format"]
        if fmt == "auto":
            fmt = "jsonl" if source_name.lower().endswith((".jsonl", ".json")) else "csv"
        if fmt not in ("csv", "jsonl"):
            raise ValueError(f"unknown input_format: {fmt}")
        if fmt == "jsonl":
            with open(input_path, "r", encoding=cfg["encoding"]) as fh:
                records = parse_jsonl(fh.read(), source_name)

    if fmt == "csv":
        df, sources = read_csv_rows(input_path, cfg)
        if df.empty:
            raise ValueError(f"{source_name}: no rows found")
        card_col = cfg["columns"]["card"]
        input_count = int(df[card_col].nunique()) if card_col in df.columns else 0
    else:
        if not records:
            raise ValueError(f"{source_name}: no JSON records found")
        df, sources = read_jsonl_rows(records)
        input_count = len(records)

    print(f"input: {source_name} ({fmt}) - {input_count} "
          f"{'cards' if fmt == 'csv' else 'JSON records'}")
    return df, sources, input_count, source_name


def load(cfg: dict, df: pd.DataFrame, review_keys) -> pd.DataFrame:
    """Check columns, optionally drop review rows, then normalise. Format-agnostic."""
    col = cfg["columns"]
    missing = [c for c in col.values() if c not in df.columns]
    if missing:
        raise ValueError(f"missing expected columns: {missing}")

    if review_keys:
        keep = ~df.apply(
            lambda r: (str(r[col["card"]]), str(r[col["row"]])) in review_keys,
            axis=1)
        print(f"review filter: dropped {int((~keep).sum())} of {len(df)} rows")
        df = df[keep].reset_index(drop=True)

    # .fillna("") so a blank serial is always the empty string, never NaN --
    # every downstream blank test is then a plain truth test.
    df["_serial"] = df[col["serial"]].str.upper().str.strip().fillna("")
    df["_ring"] = df[col["ring_no"]].apply(
        lambda v: parse_ring_no(v, cfg["ring_no_unit_digits"]))
    df["_date"] = df[col["date"]].apply(lambda v: parse_date(v, cfg["date_formats"]))
    df["_block"] = (df["_ring"] // (10 ** cfg["ring_no_unit_digits"]))
    df["_ring_norm"] = df["_ring"].apply(
        lambda v: format_ring_no(v, cfg["ring_no_prefix_digits"],
                                 cfg["ring_no_unit_digits"]))
    df["_card_key"] = df[col["card"]].apply(card_order_key)
    df["_row_i"] = pd.to_numeric(df[col["row"]], errors="coerce")

    return df.sort_values(["_card_key", "_row_i"]).reset_index(drop=True)


# =============================================================================
# STRUCTURE DISCOVERY -- what does "normal" look like in this file?
# =============================================================================

def discover_structure(df: pd.DataFrame, cfg: dict) -> dict:
    """Infer, per serial: dominant number blocks, date envelope, sites, ringers."""
    col = cfg["columns"]
    struct = {}
    for serial, g in df.groupby("_serial"):
        # A blank serial is a NULL, not a series: giving it an entry here
        # would hand it a dominant block, a date envelope and a colour of its
        # own, and let R1 / R3 hunt for structure inside a non-value.
        if cfg["blank_serial_is_null"] and not serial:
            continue
        valid = g.dropna(subset=["_ring"])
        blocks = Counter(valid["_block"].astype(int))
        total = max(len(valid), 1)
        struct[serial] = {
            "n_rows": len(g),
            "blocks": {int(b): {"n": n,
                                "share": n / total,
                                "cards": sorted(set(valid.loc[valid["_block"] == b,
                                                              col["card"]]))}
                       for b, n in blocks.items()},
            # Dominance is decided by how many distinct CARDS a block appears
            # on, not by row count. A whole-card misread contributes many rows
            # but only one card, so a row-count majority can crown the error
            # itself -- which is exactly what SS 01.xxx (23 rows, 1 card) would
            # do against the genuine SS 92.xxx (10 rows, 3 cards).
            "dominant_block": (
                max(blocks,
                    key=lambda b: (len(set(valid.loc[valid["_block"] == b,
                                                     col["card"]])), blocks[b]))
                if blocks else None),
            "date_min": g["_date"].min(),
            "date_max": g["_date"].max(),
            "places": set(g[col["place"]].str.strip()) - {""},
            "ringers": set(g[col["ringer"]].str.strip()) - {""},
            "occupied": set(valid["_ring"].astype(int)),
        }
    return struct


def find_gaps(numbers: set[int]) -> list[tuple[int, int, int]]:
    """Holes inside an occupied number range -> [(start, end, size), ...]."""
    ordered = sorted(numbers)
    gaps = []
    for lo, hi in zip(ordered, ordered[1:]):
        if hi - lo > 1:
            gaps.append((lo + 1, hi - 1, hi - lo - 1))
    return gaps


def digit_variants(block: int) -> set[int]:
    """
    Candidate true values of a block prefix under common OCR digit errors:
      - substitution within confusable pairs (5/8/6/0/3, 1/7, 2/7, 4/9...)
      - dropping a spurious leading digit (51 -> 1)
    Applied to the printed prefix, so '05' misread as '08' is covered by 8 -> 5.
    """
    confusable = {"0": "68", "1": "7", "2": "7", "3": "589", "4": "9",
                  "5": "368", "6": "058", "7": "12", "8": "0356", "9": "4"}
    text = str(block)
    out = set()
    for i, ch in enumerate(text):                       # substitutions
        for alt in confusable.get(ch, ""):
            out.add(int(text[:i] + alt + text[i + 1:]))
    for i in range(len(text)):                          # deletions
        rest = text[:i] + text[i + 1:]
        if rest:
            out.add(int(rest))
    return {b for b in out if b != block}


# =============================================================================
# RULES
# =============================================================================

def _flag(rows, card, row_id, field, rule, severity, evidence, ring_norm=""):
    rows.append({"custom_id": card, "id_row": row_id, "ring_no_norm": ring_norm,
                 "field": field, "rule": rule, "severity": severity,
                 "evidence": evidence})


def block_remap_candidates(df, struct, cfg, serial):
    """
    Rare ring-number blocks of `serial` that a single-digit edit of the block
    prefix would drop into a hole in the serial's dominant block.

    Shared by rule R1 and by the remap chart, so the picture on screen and the
    flags in the CSV can never drift apart.
    """
    p = cfg["rules"]["orphan_block_gapfit"]
    info = struct[serial]
    dominant = info["dominant_block"]
    if dominant is None:
        return []
    step = 10 ** cfg["ring_no_unit_digits"]
    dom_numbers = {n for n in info["occupied"] if n // step == dominant}
    gaps = find_gaps(dom_numbers)
    found = []

    for block, bi in info["blocks"].items():
        if block == dominant:
            continue
        if bi["share"] > p["max_block_share"] or len(bi["cards"]) > p["max_cards"]:
            continue
        members = df[(df["_serial"] == serial) & (df["_block"] == block)]
        lo, hi, n = int(members["_ring"].min()), int(members["_ring"].max()), len(members)

        for candidate in digit_variants(block):
            if candidate != dominant:
                continue
            shift = (candidate - block) * step
            new_lo, new_hi = lo + shift, hi + shift
            if set(range(new_lo, new_hi + 1)) & dom_numbers:
                continue                                  # would collide
            for g_lo, g_hi, g_size in gaps:
                fits = (abs(new_lo - g_lo) <= p["gap_slack"]
                        and abs(new_hi - g_hi) <= p["gap_slack"])
                inside = g_lo <= new_lo and new_hi <= g_hi
                if not (fits or inside):
                    continue
                found.append({"block": block, "lo": lo, "hi": hi, "n": n,
                              "cards": len(bi["cards"]), "candidate": candidate,
                              "new_lo": new_lo, "new_hi": new_hi,
                              "gap": (g_lo, g_hi, g_size), "exact": fits,
                              "members": members})
                break
    return found


def rule_orphan_block_gapfit(df, struct, cfg, flags):
    """
    A rare ring-number block confined to one or two cards is suspicious when a
    single-digit edit of its prefix drops the whole run into a same-sized hole
    in the serial's dominant block. Exact endpoint alignment across ~20 rows is
    not something coincidence produces.

    This is the rule that catches whole-card ring_no misreads, which no
    within-card check can see.
    """
    if not cfg["rules"]["orphan_block_gapfit"]["enabled"]:
        return
    col = cfg["columns"]
    dom_of = {s: i["dominant_block"] for s, i in struct.items()}

    for serial in struct:
        for c in block_remap_candidates(df, struct, cfg, serial):
            g_lo, g_hi, g_size = c["gap"]
            ev = (f"block {c['block']}.xxx holds {c['n']} rows on {c['cards']} "
                  f"card(s); remap {c['block']}->{c['candidate']} gives "
                  f"{c['new_lo']}-{c['new_hi']}, "
                  f"{'exactly filling' if c['exact'] else 'fitting inside'} "
                  f"free gap {g_lo}-{g_hi} (size {g_size}) of dominant block "
                  f"{dom_of[serial]}.xxx; no collisions")
            for _, r in c["members"].iterrows():
                _flag(flags, r[col["card"]], r[col["row"]], col["ring_no"],
                      "orphan_block_gapfit", "high" if c["exact"] else "medium",
                      ev, r["_ring_norm"])


def rule_cross_card_context(df, struct, cfg, flags):
    """
    Vote on each row's serial using neighbours from OTHER cards -- near ring
    numbers, or the same ringing date. Same-card rows are deliberately excluded:
    a systematic card-level misread is unanimous locally and would otherwise
    vote for itself.
    """
    p = cfg["rules"]["cross_card_context"]
    if not p["enabled"]:
        return
    col = cfg["columns"]
    work = df.dropna(subset=["_ring"])
    # Blank rows leave in both roles at once: they are not judged against the
    # context, and they do not dilute the vote that judges everyone else.
    if cfg["blank_serial_is_null"]:
        work = work[work["_serial"] != ""]

    for idx, r in work.iterrows():
        near = work["_ring"].sub(r["_ring"]).abs() <= p["ring_window"]
        other = work[col["card"]] != r[col["card"]]
        mask = near & other
        if p["use_same_date"]:
            mask |= (work[col["date"]] == r[col["date"]]) & other
        ctx = work[mask]
        if len(ctx) < p["min_context"]:
            continue
        counts = ctx["_serial"].value_counts()
        share = counts.get(r["_serial"], 0) / len(ctx)
        if counts.index[0] != r["_serial"] and share <= p["max_self_share"]:
            ev = (f"{len(ctx)} cross-card neighbours: "
                  f"{ {k: int(v) for k, v in counts.items()} }; "
                  f"own value '{r['_serial']}' at {share:.0%} -> expected "
                  f"'{counts.index[0]}'")
            _flag(flags, r[col["card"]], r[col["row"]], col["serial"],
                  "cross_card_context", "high", ev, r["_ring_norm"])


def rule_era_violation(df, struct, cfg, flags):
    """A serial used far outside the date window it occupies elsewhere."""
    p = cfg["rules"]["era_violation"]
    if not p["enabled"]:
        return
    col = cfg["columns"]
    margin = pd.Timedelta(days=p["margin_days"])

    for serial, info in struct.items():
        if info["n_rows"] < p["min_rows_for_envelope"]:
            continue
        g = df[(df["_serial"] == serial) & df["_date"].notna()]
        for _, r in g.iterrows():
            # exclude the whole card: a card-wide misread would otherwise
            # widen the envelope with its own rows and hide itself
            trimmed = g[g[col["card"]] != r[col["card"]]]["_date"]
            if trimmed.empty:
                continue
            lo, hi = trimmed.min() - margin, trimmed.max() + margin
            if lo <= r["_date"] <= hi:
                continue
            # Only meaningful if some OTHER serial both covers this date and
            # owns this ring block -- otherwise we are just looking at the
            # first or last card of the series (a harmless edge effect).
            better = [s2 for s2, i2 in struct.items()
                      if s2 != serial
                      and pd.notna(i2["date_min"])
                      and i2["date_min"] - margin <= r["_date"] <= i2["date_max"] + margin
                      and not pd.isna(r["_block"])
                      and int(r["_block"]) in i2["blocks"]]
            if not better:
                continue
            ev = (f"date {r['_date'].date()} outside the {serial} envelope "
                  f"{trimmed.min().date()}..{trimmed.max().date()} "
                  f"(+/-{p['margin_days']} d, other cards only); block "
                  f"{r['_block']:.0f}.xxx + this date fit {better} instead")
            _flag(flags, r[col["card"]], r[col["row"]], col["date"],
                  "era_violation", "medium", ev, r["_ring_norm"])


def rule_card_sequence_break(df, struct, cfg, flags):
    """
    Cards are chronological and ring numbers run across card boundaries.
    Report boundaries where the first row of a card does not continue the
    previous card -- a cheap way to surface a card whose serial or ring block
    was mis-transcribed as a whole.
    """
    p = cfg["rules"]["card_sequence_break"]
    if not p["enabled"]:
        return
    col = cfg["columns"]
    # Both branches below reason about the serial, so a blank one has
    # nothing to offer either: "" == "" is not evidence that the series
    # continued, and struct holds no entry to look the blocks up in.
    # Filtering here rather than at the boundary means a card whose first
    # rows happen to be blank is still compared on its first real row.
    work = df[df["_serial"] != ""] if cfg["blank_serial_is_null"] else df
    cards = sorted(work[col["card"]].unique(), key=card_order_key)

    for prev_id, cur_id in zip(cards, cards[1:]):
        prev = work[(work[col["card"]] == prev_id)].dropna(subset=["_ring"])
        cur = work[(work[col["card"]] == cur_id)].dropna(subset=["_ring"])
        if prev.empty or cur.empty:
            continue
        prev_last, cur_first = prev.iloc[-1], cur.iloc[0]
        delta = cur_first["_ring"] - prev_last["_ring"]
        same_serial = prev_last["_serial"] == cur_first["_serial"]
        if 0 < delta <= p["max_expected_gap"] and not same_serial:
            ev = (f"ring sequence continues from {prev_id} "
                  f"({prev_last[col['ring_no']]}, {prev_last['_serial']}) to "
                  f"{cur_first[col['ring_no']]} but serial changes to "
                  f"{cur_first['_serial']}")
            _flag(flags, cur_id, cur_first[col["row"]], col["serial"],
                  "card_sequence_break", "high", ev, cur_first["_ring_norm"])
        elif same_serial and prev_last["_block"] != cur_first["_block"]:
            # A block change inside one serial is only newsworthy when one of
            # the blocks is rare; FF legitimately runs 0.xxx -> 1.xxx -> 2.xxx.
            blocks = struct[cur_first["_serial"]]["blocks"]
            rare = [int(b) for b in (prev_last["_block"], cur_first["_block"])
                    if not pd.isna(b) and len(blocks.get(int(b), {}).get("cards", [])) <= 2]
            if not rare:
                continue
            ev = (f"block changes {prev_last['_block']:.0f}.xxx -> "
                  f"{cur_first['_block']:.0f}.xxx between {prev_id} "
                  f"({prev_last[col['ring_no']]}) and {cur_id} "
                  f"({cur_first[col['ring_no']]}) inside serial "
                  f"{cur_first['_serial']}; block(s) {rare} rare")
            _flag(flags, cur_id, cur_first[col["row"]], col["ring_no"],
                  "card_sequence_break", "low", ev, cur_first["_ring_norm"])


def rule_duplicate_key(df, struct, cfg, flags):
    """serial + ring_no is the composite identity of a bird; it must be unique."""
    if not cfg["rules"]["duplicate_key"]["enabled"]:
        return
    col = cfg["columns"]
    work = df.dropna(subset=["_ring"]).copy()
    work["_key"] = work["_serial"] + work["_ring_norm"]
    for key, g in work.groupby("_key"):
        if len(g) < 2:
            continue
        where = ", ".join(f"{r[col['card']]}#{r[col['row']]}" for _, r in g.iterrows())
        for _, r in g.iterrows():
            _flag(flags, r[col["card"]], r[col["row"]], "serial+ring_no",
                  "duplicate_key", "medium", f"key {key} appears {len(g)}x: {where}",
                  r["_ring_norm"])


def rule_missing_key_fields(df, struct, cfg, flags):
    if not cfg["rules"]["missing_key_fields"]["enabled"]:
        return
    col = cfg["columns"]
    for _, r in df.iterrows():
        if (not cfg["blank_serial_is_null"]
                and not str(r[col["serial"]]).strip()):
            _flag(flags, r[col["card"]], r[col["row"]], col["serial"],
                  "missing_key_fields", "high", "serial empty")
        raw = str(r[col["ring_no"]]).strip()
        if pd.isna(r["_ring"]):
            _flag(flags, r[col["card"]], r[col["row"]], col["ring_no"],
                  "missing_key_fields", "high",
                  f"ring_no empty or unparseable: '{raw}'", r["_ring_norm"])


def rule_group_minority(df, struct, cfg, flags):
    """
    Minority serial inside a date+ringer+place group -- but only when the ring
    number does not sit in a block that legitimately belongs to that serial.
    Without that guard this rule fires on every genuine parallel series.
    """
    p = cfg["rules"]["group_minority"]
    if not p["enabled"]:
        return
    col = cfg["columns"]
    keys = [col["date"], col["ringer"], col["place"]]
    # Without this, a group where most rows are blank would crown "" as the
    # majority and report every genuine series in it as the minority.
    votes = df[df["_serial"] != ""] if cfg["blank_serial_is_null"] else df
    for _, g in votes.groupby(keys):
        if len(g) < p["min_group"]:
            continue
        counts = g["_serial"].value_counts()
        if len(counts) < 2:
            continue
        majority = counts.index[0]
        for _, r in g[g["_serial"] != majority].iterrows():
            blocks = struct.get(r["_serial"], {}).get("blocks", {})
            block = r["_block"]
            supported = (not pd.isna(block)) and blocks.get(int(block), {}).get("n", 0) > 1
            if supported:
                continue
            ev = (f"minority serial in group "
                  f"{ {k: int(v) for k, v in counts.items()} }; ring block "
                  f"{block} not otherwise used by {r['_serial']}")
            _flag(flags, r[col["card"]], r[col["row"]], col["serial"],
                  "group_minority", "low", ev, r["_ring_norm"])


RULES = [rule_orphan_block_gapfit, rule_cross_card_context, rule_era_violation,
         rule_card_sequence_break, rule_duplicate_key, rule_missing_key_fields,
         rule_group_minority]


# =============================================================================
# OUTPUT
# =============================================================================

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
RECORD_SHAPES = ("review", "post_eval")
OUTPUT_FORMATS = ("jsonl", "csv")


def _row_sort_key(value):
    """Numeric id_row first, anything unparseable after it -- never NaN."""
    try:
        return (0, float(value))
    except (TypeError, ValueError):
        return (1, str(value))


def flags_frame(flags: list[dict]) -> pd.DataFrame:
    """Flags sorted by severity, then card, then row -- the console order too."""
    out = pd.DataFrame(flags)
    out["_r"] = out["severity"].map(SEVERITY_RANK)
    out["_c"] = out["custom_id"].apply(card_order_key)
    out["_i"] = pd.to_numeric(out["id_row"], errors="coerce")
    return (out.sort_values(["_r", "_c", "_i"])
               .drop(columns=["_r", "_c", "_i"])
               .reset_index(drop=True))


def flagged_rows(out: pd.DataFrame) -> list[tuple[tuple[str, str], list[dict]]]:
    """
    Flags grouped per row -> [((custom_id, id_row), [flags]), ...], ordered by
    the worst severity of the row, then card, then row. Several rules hitting
    the same row become one output record, whatever its shape.
    """
    grouped: dict[tuple[str, str], list[dict]] = {}
    for f in out.to_dict("records"):
        grouped.setdefault((str(f["custom_id"]), str(f["id_row"])), []).append(f)
    return sorted(
        grouped.items(),
        key=lambda kv: (min(SEVERITY_RANK.get(f["severity"], 9) for f in kv[1]),
                        card_order_key(kv[0][0]), _row_sort_key(kv[0][1])))


def build_species_entry(row: pd.Series, cfg: dict) -> dict:
    """
    Fold a flat CSV row back into the nested review shape. Only reached with
    CSV input; JSONL input hands back its own object untouched.
    """
    u = cfg["unflatten"]
    entry, ringing = {}, {}
    for key, value in row.items():
        if key.startswith("_") or key in u["skip"]:
            continue
        value = None if value == "" else value
        if key.startswith(u["ringing_prefix"]):
            path = u["nested"].get(key[len(u["ringing_prefix"]):],
                                   (key[len(u["ringing_prefix"]):],))
            target = ringing
            for part in path[:-1]:
                target = target.setdefault(part, {})
            target[path[-1]] = value
        else:
            entry[key] = value
    entry["ringing"] = [ringing]
    return entry


def build_review_records(out: pd.DataFrame, df: pd.DataFrame, sources: dict,
                         cfg: dict) -> list[dict]:
    """
    record_shape "review": one record per flagged ROW, shaped like
    review-*.jsonl -- custom_id, batch_id, species_entry, reasons, timestamp
    (+ details per rule when jsonl_output.include_details is on).
    """
    col = cfg["columns"]
    rows = {(str(r[col["card"]]), str(r[col["row"]])): r for _, r in df.iterrows()}
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    records = []
    for (card, row_id), group in flagged_rows(out):
        source = sources.get((card, row_id))
        row = rows.get((card, row_id))
        if source is not None:
            entry, batch = source["species_entry"], source["batch_id"]
        else:
            entry = build_species_entry(row, cfg) if row is not None else {}
            batch = (row.get(cfg["batch_column"]) if row is not None else None) or None

        record = {"custom_id": card, "batch_id": batch, "species_entry": entry,
                  "reasons": sorted({f["rule"] for f in group}),
                  "timestamp": stamp}
        if cfg["jsonl_output"]["include_details"]:
            record["details"] = [
                {"rule": f["rule"], "field": f["field"],
                 "severity": f["severity"], "evidence": f["evidence"]}
                for f in sorted(group,
                                key=lambda f: SEVERITY_RANK.get(f["severity"], 9))]
        records.append(record)
    return records


def build_post_eval_records(out: pd.DataFrame, sources: dict,
                            cfg: dict) -> list[dict]:
    """
    record_shape "post_eval": one record per flagged ROW in the shape of the AI
    post-eval output -- custom_id, id_row, suspicious_fields, reasons, plus the
    evidence of every rule as one note (post_eval_output.note_key). The local
    rules propose no corrected value, so there is no suggested_value.
    """
    note_key = cfg["post_eval_output"]["note_key"]
    records = []
    for (card, row_id), group in flagged_rows(out):
        group = sorted(group, key=lambda f: SEVERITY_RANK.get(f["severity"], 9))
        source = sources.get((card, row_id))
        if source is not None:              # JSONL input: id_row as the source has it
            id_row = source["species_entry"].get("id_row")
        else:                               # CSV input: everything is text there
            id_row = int(row_id) if row_id.isdigit() else row_id
        records.append({
            "custom_id": card,
            "id_row": id_row,
            "suspicious_fields": list(dict.fromkeys(f["field"] for f in group)),
            "reasons": sorted({f["rule"] for f in group}),
            note_key: " | ".join(f"[{f['severity']}] {f['rule']}: {f['evidence']}"
                                 for f in group),
        })
    return records


def save_output(output_jsonl: str, out: pd.DataFrame, cfg: dict, output_dir) -> Path:
    """
    Write the result to <output_dir>/<output_stem>_<YYYYmmdd_HHMMSS>.<ext>,
    the same naming pattern as the AI post-eval scripts. output_format picks
    what goes in: the JSONL records, or the CSV with one line per flag.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    fmt = cfg["output_format"]
    path = output_dir / f"{cfg['output_stem']}_{datetime.now():%Y%m%d_%H%M%S}.{fmt}"
    if fmt == "csv":
        out.to_csv(path, index=False, encoding="utf-8")
    else:
        path.write_text(output_jsonl, encoding="utf-8")
    print(f"output saved: {path}")
    return path


# =============================================================================
# RUN
# =============================================================================

def run_silent_errors(input_path=None, input_jsonl=None, save_to_disk=None,
                      output_dir=None, review_path=None, review_jsonl=None,
                      record_shape=None, cfg=None) -> dict:
    """
    Main entry point (standalone and workflow). Takes the input and hands back
    the result the same way as run_post_ai_eval() in openai_post_eval.py /
    gemini_post_eval.py. What happens in between -- structure discovery and
    the rules -- is the logic layer above.

    input_path   - path to the input file (results JSONL, or the flat CSV)
    input_jsonl  - JSONL text (str) or list of dicts (use instead of input_path)
    save_to_disk - None = CONFIG["save_output_to_disk"]
    output_dir   - None = CONFIG["output_dir"]
    review_path  - review JSONL file whose rows are dropped before the
                   analysis (instead of CONFIG review_path)
    review_jsonl - review rows as JSONL text or list of dicts (the workflow
                   passes this run's review rows); wins over any file.
                   Both are used only when CONFIG filter_review_data is True.
    record_shape - "review" | "post_eval"; None = CONFIG["record_shape"]
    cfg          - None = CONFIG

    Returns dict:
        output_jsonl    - flagged records as JSONL text ("" when nothing is flagged)
        output_records  - the same records, parsed (list of dicts)
        output_path     - Path of the saved file, or None (not saved / nothing flagged)
        input_count, output_count
        response_status - always "completed": a local run either finishes or
                          raises. Kept so the result reads like the AI one
                          (post_eval_runner checks it before touching review).
        record_shape    - shape of output_records
        df, struct, flags - the analysed data, for add-ons such as the plots
    """
    cfg = CONFIG if cfg is None else cfg
    if save_to_disk is None:
        save_to_disk = cfg["save_output_to_disk"]
    if output_dir is None:
        output_dir = cfg["output_dir"]
    record_shape = record_shape or cfg["record_shape"]
    # checked up front: a typo should fail before the analysis, not after it
    if record_shape not in RECORD_SHAPES:
        raise ValueError(f"unknown record_shape: {record_shape!r} (use one of {RECORD_SHAPES})")
    if cfg["output_format"] not in OUTPUT_FORMATS:
        raise ValueError(f"unknown output_format: {cfg['output_format']!r} "
                         f"(use one of {OUTPUT_FORMATS})")
    if review_path is not None and review_jsonl is not None:
        raise ValueError("Provide at most one of review_path / review_jsonl")

    df, sources, input_count, source_name = read_input(cfg, input_path, input_jsonl)
    review_keys = load_review_keys(cfg, review_path, review_jsonl)
    df = load(cfg, df, review_keys)
    struct = discover_structure(df, cfg)

    print(f"loaded {len(df)} rows / {df[cfg['columns']['card']].nunique()} cards "
          f"from {source_name}\n")
    print("discovered structure")
    print("-" * 78)
    for serial, info in sorted(struct.items()):
        blocks = ", ".join(
            f"{b}.xxx x{i['n']}" + ("*" if len(i["cards"]) <= 2 else "")
            for b, i in sorted(info["blocks"].items(), key=lambda kv: -kv[1]["n"]))
        span = (f"{info['date_min']:%Y-%m}..{info['date_max']:%Y-%m}"
                if pd.notna(info["date_min"]) else "n/a")
        print(f"  {serial}: {info['n_rows']:>5} rows | {span} | blocks: {blocks}")
    print("  (* = block confined to <=2 cards)\n")

    flags: list[dict] = []
    for rule in RULES:
        before = len(flags)
        rule(df, struct, cfg, flags)
        print(f"  {rule.__name__:<32} -> {len(flags) - before:>4} flags")

    result = {
        "output_jsonl": "",
        "output_records": [],
        "output_path": None,
        "input_count": input_count,
        "output_count": 0,
        "response_status": "completed",
        "record_shape": record_shape,
        "df": df,
        "struct": struct,
        "flags": flags,
    }
    if not flags:
        print("\nno suspicious rows found - no output file written")
        return result

    out = flags_frame(flags)
    if record_shape == "review":
        records = build_review_records(out, df, sources, cfg)
    else:
        records = build_post_eval_records(out, sources, cfg)
    output_jsonl = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    # Parsed back from the text, as the AI scripts do: plain JSON values only,
    # and no shared references to the caller's input objects.
    output_records = [json.loads(line) for line in output_jsonl.splitlines()]
    output_path = save_output(output_jsonl, out, cfg, output_dir) if save_to_disk else None

    print(f"\n{len(out)} flags on {len(records)} rows ({record_shape} shape) -> "
          f"{output_path or 'not saved, returned in the result only'}")
    print("=" * 78)
    for (card, rule, sev), g in out.groupby(["custom_id", "rule", "severity"],
                                            sort=False):
        ordered = sorted(g["id_row"].tolist(),
                         key=lambda v: (pd.to_numeric(v, errors="coerce"), str(v)))
        rows = ", ".join(str(r) for r in ordered)
        print(f"\n[{sev.upper():<6}] {card}  field={g['field'].iloc[0]}  rule={rule}")
        rings = ", ".join(str(v) for v in g["ring_no_norm"].tolist() if v)
        print(f"  id_row: {rows}")
        if rings:
            print(f"  ring:   {rings}")
        print(f"  why:    {g['evidence'].iloc[0]}")

    result.update(output_jsonl=output_jsonl, output_records=output_records,
                  output_path=output_path, output_count=len(output_records))
    return result


def run(cfg: dict) -> tuple[pd.DataFrame, dict, list[dict]]:
    """
    The previous entry point, kept for the plots runner: the same analysis and
    output as run_silent_errors(), input from cfg["input_path"]. Returns
    (df, struct, flags) as before, so an add-on such as the plotting module
    works from exactly the same data the flags were derived from.
    """
    result = run_silent_errors(input_path=cfg["input_path"], cfg=cfg)
    return result["df"], result["struct"], result["flags"]


def main():
    run_silent_errors(input_path=CONFIG["input_path"])


if __name__ == "__main__":
    main()
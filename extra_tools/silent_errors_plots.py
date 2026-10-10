#!/usr/bin/env python3
"""
silent_errors_plots.py -- optional presentation layer.

Section (c) of the original script: each chart type is chosen for what it has
to reveal. Nothing here decides anything -- it only draws the frame, the
structure and the flags that `silent_errors.py` already produced, which is why
the picture on screen and the flags in the output can never drift apart.

This module is an add-on and is not meant to be run on its own; it takes the
(df, struct, flags) triple from the logic layer. Run `silent_errors_run.py`.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from silent_errors import (block_remap_candidates, card_order_key, find_gaps,
                           format_ring_no)

# =============================================================================
# CONFIG -- plotting only; merged over the logic config at call time, so the
# charts read column names, ring notation and rule thresholds from there.
# =============================================================================

PLOT_CONFIG = {
    "plots": {                      # per-chart switches
        "series_map": True,
        "card_ranges": True,
        "block_occupancy": True,
        "block_coverage": True,
        "flag_summary": True,
        "year_serial": True,
        "block_remap": True,
    },
    # Series map appearance. y_digits controls how far the ring number is
    # truncated on the Y axis: 0 -> '05', 1 -> '05.2', 3 -> '05.034' (no
    # aggregation). Fewer digits means rows collapse into buckets, so each
    # marker then carries the number of records behind it.
    "series_map": {
        "y_digits": 1,
        "numeric_spacing": False,   # False: buckets evenly spaced (compact)
                                    # True:  true numeric position, symlog
                                    #        -- keeps the real jumps visible
        "show_counts": True,        # count printed inside each marker
        "marker_size": (175, 175),   # (smallest, largest) marker area
        "max_y_labels": 40,         # thin the tick labels beyond this many
        "legend_outside": True,     # legend to the right of the axes
    },
    # Attention marks (hatching, flag rings, gap shading) must never collide
    # with a category colour. The serial palette is tab10, whose fourth entry
    # is red -- so red is off limits here.
    "plot_style": {
        "highlight_colour": "#7fe600",   # pink, outside tab10
        "hatch_pattern": "///",
    },
    "block_occupancy": {
        "digits": 0,                # 0 -> one bar per block (01.xxx)
                                    # 1 -> split into 01.0xx, 01.1xx, ...
        "hatch_rare": True,         # mark bars belonging to a rare block
    },
    "year_serial": {
        "hatch_flagged": True,      # mark the flagged share of each segment
    },
    # p7: main block on the left, rare blocks parked on the right, with an
    # arrow drawn only where the remap actually lands in a hole.
    "block_remap": {
        "main_width": 100.0,        # synthetic x-width of the main block
        "slot_width": 18.0,         # width of one rare-block slot
        "slot_gap": 9.0,
        "panel_height": 2.1,
        "show_arrows": True,
        "serials": "with_rare",     # "with_rare" -> only serials that have one
                                    # "all"       -> every serial
    },
    "block_coverage": {
        "blocks": "all",            # "all" -> a panel per serial+block
                                    # "dominant" -> main block per serial only
        "min_gap_marked": 5,        # shade holes at least this wide
        "panel_height": 1.25,
    },
    "plots_dir": "plots",
    "plots_show": False,            # True -> open windows instead of saving
    "plots_dpi": 130,
}


# =============================================================================
# AXIS HELPERS -- ring notation for ticks and buckets
# =============================================================================

def ring_bucket(value, y_digits: int, unit_digits: int):
    """Truncate a ring number to `y_digits` of its unit part -> bucket index."""
    if value is None or pd.isna(value):
        return None
    return int(value) // (10 ** max(unit_digits - y_digits, 0))


def bucket_label(bucket: int, y_digits: int, prefix_digits: int,
                 unit_digits: int) -> str:
    """Bucket index -> archive notation, e.g. bucket 52 @1 digit -> '05.2'."""
    value = bucket * (10 ** max(unit_digits - y_digits, 0))
    thousands, units = divmod(value, 10 ** unit_digits)
    head = f"{thousands:0{prefix_digits}d}"
    if y_digits <= 0:
        return head
    return f"{head}.{units:0{unit_digits}d}"[:len(head) + 1 + y_digits]


def ring_label(value, cfg: dict) -> str:
    """Axis-tick label in the archive's own notation, e.g. 5034 -> '05.034'."""
    return format_ring_no(value, cfg["ring_no_prefix_digits"],
                          cfg["ring_no_unit_digits"])


# =============================================================================
# PLOTS -- section (c), each chart type chosen for what it has to reveal
# =============================================================================

def make_plots(df, struct, flags, cfg, plot_cfg=None):
    """Draw the enabled charts and return the paths written."""
    cfg = {**cfg, **(plot_cfg or PLOT_CONFIG)}
    import matplotlib
    if not cfg["plots_show"]:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, NullFormatter, NullLocator

    col = cfg["columns"]
    want = cfg["plots"]
    step = 10 ** cfg["ring_no_unit_digits"]
    os.makedirs(cfg["plots_dir"], exist_ok=True)
    serials = sorted(struct)
    # zip() against a fixed 10-colour list stops at the shorter side and
    # leaves every serial past the tenth without a key. Widen to tab20 once
    # there are more than ten, and wrap so the lookup can never fail.
    cmap = plt.cm.tab10 if len(serials) <= 10 else plt.cm.tab20
    palette = {s: cmap(i % cmap.N) for i, s in enumerate(serials)}
    prefix = cfg["ring_no_prefix_digits"]
    hi_colour = cfg["plot_style"]["highlight_colour"]
    hatch = cfg["plot_style"]["hatch_pattern"]
    saved = []

    def finish(fig, name):
        fig.tight_layout()
        if cfg["plots_show"]:
            plt.show()
        else:
            path = os.path.join(cfg["plots_dir"], name)
            fig.savefig(path, dpi=cfg["plots_dpi"], bbox_inches="tight")
            saved.append(path)
        plt.close(fig)

    # `struct` is the authority on what counts as a serial: a blank one is a
    # deliberate NULL, has no entry there and therefore no colour. Charts that
    # group BY serial drop those rows; P2, which groups by card, keeps them in
    # grey, because the card and its ring numbers are real either way.
    known = df["_serial"].isin(serials)

    # A chart can only be drawn from rows that still carry the field it is
    # about, and the review filter can take those rows away. Each chart states
    # its precondition below and says so when it is not met -- an empty frame
    # is a fact about the data, not a reason for a traceback.
    have_ring_date = df[known].dropna(subset=["_ring", "_date"])
    have_ring = df[known].dropna(subset=["_ring"])
    have_date = df[known].dropna(subset=["_date"])
    have_block = any(i["blocks"] for i in struct.values())

    def skip(name, why):
        print(f"  {name} skipped: {why}")

    work = df.dropna(subset=["_ring", "_date"])
    flagged = {(f["custom_id"], str(f["id_row"])) for f in flags}
    is_flagged = df.apply(
        lambda r: (r[col["card"]], str(r[col["row"]])) in flagged, axis=1)

    # ---- P1 bubble scatter: the series map ------------------------------
    # Year x truncated ring prefix. Truncating collapses rows into buckets, so
    # markers are sized and labelled by how many records sit behind them.
    # numeric_spacing decides between two honest but different pictures:
    #   True  -> real numeric position: the distance between 05.xxx and 08.xxx
    #            is visible, which is what makes an orphan block stand out.
    #   False -> evenly spaced buckets: compact and easy to read, but the
    #            spacing carries no meaning.
    # Colour stays on serial rather than on the prefix: the prefix is already
    # the Y position, so colouring by it would encode the same thing twice.
    if want["series_map"] and have_ring_date.empty:
        skip("p1_series_map", "no row has both a ring number and a date")
    elif want["series_map"]:
        sm = cfg["series_map"]
        digits = sm["y_digits"]
        units = cfg["ring_no_unit_digits"]

        g = have_ring_date.copy()
        g["_year"] = g["_date"].dt.year
        g["_bucket"] = g["_ring"].apply(lambda v: ring_bucket(v, digits, units))
        g["_flagged"] = is_flagged.reindex(g.index, fill_value=False)

        cells = (g.groupby(["_year", "_bucket", "_serial"])
                  .agg(n=("_ring", "size"), flagged=("_flagged", "sum"))
                  .reset_index())

        buckets = sorted(cells["_bucket"].unique())
        scale = 10 ** max(units - digits, 0)
        if sm["numeric_spacing"]:
            ypos = {b: b * scale for b in buckets}
        else:
            ypos = {b: i for i, b in enumerate(buckets)}

        years = list(range(int(g["_year"].min()), int(g["_year"].max()) + 1))
        lo, hi = sm["marker_size"]
        top = max(cells["n"].max(), 1)
        cells["_size"] = lo + (hi - lo) * np.sqrt(cells["n"] / top)

        fig, ax = plt.subplots(figsize=(max(13, len(years) * .62),
                                        max(6, len(buckets) * .30)))
        for serial in serials:
            sub = cells[cells["_serial"] == serial]
            if sub.empty:
                continue
            ax.scatter(sub["_year"], [ypos[b] for b in sub["_bucket"]],
                       s=sub["_size"], color=palette[serial], alpha=.8,
                       edgecolors="white", linewidths=.8,
                       label=f"{serial} (n={int(sub['n'].sum())})", zorder=2)

        hit = cells[cells["flagged"] > 0]
        if not hit.empty:
            ax.scatter(hit["_year"], [ypos[b] for b in hit["_bucket"]],
                       s=hit["_size"] * 1.9, facecolors="none",
                       edgecolors=hi_colour, linewidths=1.8, zorder=3,
                       label="contains flagged rows")

        if sm["show_counts"]:
            for _, c in cells.iterrows():
                ax.text(c["_year"], ypos[c["_bucket"]], str(int(c["n"])),
                        ha="center", va="center", zorder=4,
                        fontsize=min(9, max(5, 3 + c["_size"] / 90)))

        # Scale first: setting it afterwards would replace our ticks with the
        # log locator's own.
        if sm["numeric_spacing"]:
            ax.set_yscale("symlog", linthresh=10 ** units)
            ax.yaxis.set_minor_locator(NullLocator())
        # At full precision there can be thousands of buckets; label a subset.
        keep = max(1, len(buckets) // sm["max_y_labels"] + (1 if len(buckets) >
                                                            sm["max_y_labels"] else 0))
        shown = buckets[::keep]
        ax.set_yticks([ypos[b] for b in shown])
        ax.set_yticklabels([bucket_label(b, digits, cfg["ring_no_prefix_digits"],
                                         units) for b in shown],
                           fontsize=8 if len(shown) <= 45 else 6)
        ax.set_xticks(years)
        ax.set_xticklabels(years, rotation=45, ha="right", fontsize=8)
        ax.set_xlim(years[0] - .8, years[-1] + .8)
        spacing = "true numeric spacing" if sm["numeric_spacing"] else "even spacing"
        ax.set(xlabel="ringing year",
               ylabel=f"ring number, first {digits} unit digit(s)",
               title=f"Series map: ring prefix by year, sized by record count "
                     f"({spacing})")
        if sm["legend_outside"]:
            ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.01, 1),
                      borderaxespad=0, framealpha=.9)
        else:
            ax.legend(fontsize=8, loc="upper left", framealpha=.9)
        ax.grid(alpha=.3, linestyle=":", zorder=0)
        finish(fig, "p1_series_map.png")

    # ---- P2 broken_barh: per-card ring ranges ---------------------------
    # A span is an interval, so draw intervals. Ticks sit on block boundaries
    # and carry the real ring number -- 10^4 tells a ringer nothing, 08.000 does.
    if want["card_ranges"] and work.empty:
        skip("p2_card_ranges", "no row has both a ring number and a date")
    elif want["card_ranges"]:
        cards = sorted(work[col["card"]].unique(), key=card_order_key)
        fig, ax = plt.subplots(figsize=(11, max(6, len(cards) * .12)))
        for y, card in enumerate(cards):
            g = work[work[col["card"]] == card]
            # Blank serials are not in `struct`, so they are not in the
            # palette either; a card that is blank throughout has no mode at
            # all and gets a neutral grey rather than an IndexError.
            modes = g.loc[g["_serial"] != "", "_serial"].mode()
            serial = modes.iloc[0] if not modes.empty else None
            lo, hi = g["_ring"].min(), g["_ring"].max()
            ax.broken_barh([(lo, max(hi - lo, 1))], (y - .4, .8),
                           facecolors=palette.get(serial, "#bbbbbb"), alpha=.85)
        ax.set_xscale("symlog", linthresh=step)
        blocks = sorted({int(b) * step for i in struct.values() for b in i["blocks"]} | {0})
        ax.set_xticks(blocks)
        ax.set_xticklabels([ring_label(b, cfg) for b in blocks],
                           rotation=45, ha="right", fontsize=8)
        ax.xaxis.set_minor_locator(NullLocator())
        ax.set(xlabel="ring number (block boundaries; spacing is symlog)",
               ylabel="card (chronological)",
               title="Per-card ring-number span -- breaks and overlaps in the sequence")
        keep = max(1, len(cards) // 30)
        ax.set_yticks(range(0, len(cards), keep))
        ax.set_yticklabels([cards[i] for i in range(0, len(cards), keep)], fontsize=6)
        ax.invert_yaxis()
        ax.legend(handles=[plt.Line2D([], [], color=palette[s], lw=6, label=s)
                           for s in serials], fontsize=8)
        ax.grid(alpha=.25, axis="x")
        finish(fig, "p2_card_ranges.png")

    # ---- P3 grouped bar: block occupancy per serial ---------------------
    # Counts over a categorical pairing -> bars. `digits` splits each block
    # into finer columns (01.0xx, 01.1xx, ...) without changing what the
    # hatching means: it always marks membership of a rare BLOCK, because
    # that is the unit rule R1 reasons about.
    if want["block_occupancy"] and have_ring.empty:
        skip("p3_block_occupancy", "no row has a usable ring number")
    elif want["block_occupancy"]:
        bo = cfg["block_occupancy"]
        digits, units = bo["digits"], cfg["ring_no_unit_digits"]
        pfx = cfg["ring_no_prefix_digits"]

        g = have_ring.copy()
        g["_bucket"] = g["_ring"].apply(lambda v: ring_bucket(v, digits, units))
        cells = (g.groupby(["_serial", "_bucket"]).size()
                  .reset_index(name="amount").sort_values(["_serial", "_bucket"])
                  .rename(columns={"_serial": "serial", "_bucket": "bucket"}))

        def is_rare(serial, bucket):
            block = int(bucket * 10 ** max(units - digits, 0)) // 10 ** units
            info = struct[serial]["blocks"].get(block)
            return (info is not None and len(info["cards"]) <= 2
                    and block != struct[serial]["dominant_block"])

        tail = "." + "x" * units if digits <= 0 else "x" * (units - digits)
        labels = [f"{r.serial}\n{bucket_label(r.bucket, digits, pfx, units)}{tail}"
                  for r in cells.itertuples()]

        fig, ax = plt.subplots(figsize=(max(11, len(cells) * .42), 5.5))
        bars = ax.bar(labels, cells["amount"],
                      color=[palette[s] for s in cells["serial"]])
        marked = 0
        if bo["hatch_rare"]:
            for bar, r in zip(bars, cells.itertuples()):
                if is_rare(r.serial, r.bucket):
                    bar.set_hatch(hatch)
                    bar.set_edgecolor(hi_colour)
                    bar.set_linewidth(1.2)
                    marked += 1
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.set_yticks([1, 10, 100, 1000])
        ax.tick_params(axis="x", labelsize=7 if len(cells) > 20 else 9)
        if len(cells) > 20:
            plt.setp(ax.get_xticklabels(), rotation=60, ha="right")
        note = f" (hatched = rare block on <=2 cards)" if marked else ""
        ax.set(ylabel="amount (log scale)",
               title=f"Ring-number block occupancy per serial{note}")
        ax.grid(alpha=.25, axis="y")
        finish(fig, "p3_block_occupancy.png")

    # ---- P4 step: coverage and holes per block --------------------------
    # A step over a dense integer axis makes holes literal gaps in the line,
    # which is exactly what rule R1 reasons about. Showing every block -- not
    # just the dominant one -- means the orphan block and the hole it would
    # fill are both on the page.
    if want["block_coverage"] and not have_block:
        skip("p4_block_coverage", "no serial has a ring block left")
    elif want["block_coverage"]:
        bc = cfg["block_coverage"]
        panels = []
        for serial in serials:
            info = struct[serial]
            for block in sorted(info["blocks"]):
                if bc["blocks"] == "dominant" and block != info["dominant_block"]:
                    continue
                nums = sorted(n for n in info["occupied"] if n // step == block)
                if nums:
                    panels.append((serial, block, nums,
                                   block == info["dominant_block"],
                                   len(info["blocks"][block]["cards"])))

        fig, axes = plt.subplots(len(panels), 1,
                                 figsize=(11, bc["panel_height"] * len(panels)),
                                 squeeze=False)
        for ax, (serial, block, nums, is_dom, n_cards) in zip(axes[:, 0], panels):
            axis = np.arange(nums[0], nums[-1] + 1)
            present = np.isin(axis, nums).astype(int)
            ax.step(axis, present, where="mid", color=palette[serial], lw=1)
            ax.fill_between(axis, present, step="mid", alpha=.3,
                            color=palette[serial])
            for g_lo, g_hi, g_size in find_gaps(set(nums)):
                if g_size >= bc["min_gap_marked"]:
                    ax.axvspan(g_lo, g_hi, color=hi_colour, alpha=.18)
                    ax.text((g_lo + g_hi) / 2, 1.05, str(g_size), ha="center",
                            fontsize=7, color=hi_colour)
            pad = max((nums[-1] - nums[0]) * .02, 1)
            ax.set_xlim(nums[0] - pad, nums[-1] + pad)
            ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: ring_label(v, cfg)))
            ax.tick_params(axis="x", labelsize=8)
            rare = (not is_dom
                    and n_cards <= cfg["rules"]["orphan_block_gapfit"]["max_cards"])
            tag = ("main" if is_dom
                   else f"{'rare' if rare else 'secondary'}, {n_cards} card(s)")
            ax.set(ylim=(0, 1.35), yticks=[],
                   ylabel=f"{serial}\n{ring_label(block * step, cfg)[:prefix]}.xxx",
                   xlabel=f"{len(nums)} rings ({tag})")
            if rare:
                for spine in ax.spines.values():
                    spine.set_edgecolor(hi_colour)
                    spine.set_linewidth(1.3)
        axes[0, 0].set_title("Ring-number coverage per serial and block "
                             f"(shaded = hole >={bc['min_gap_marked']}; pink "
                             "frame = rare block, candidate for a remap)")
        finish(fig, "p4_block_coverage.png")

    # ---- P7 remap chart: rare blocks beside the holes they would fill ----
    # Same idea as P4, but laid out to make rule R1 legible: the dominant block
    # spans the left, each rare block is parked in its own slot on the right,
    # and an arrow is drawn ONLY when the single-digit remap really lands in a
    # hole. A rare block with no arrow is rare but not suspicious -- which is
    # the distinction between FV 08.xxx and FV 38.xxx.
    if want["block_remap"] and not have_block:
        skip("p7_block_remap", "no serial has a ring block left")
    elif want["block_remap"]:
        br = cfg["block_remap"]
        rare_max = cfg["rules"]["orphan_block_gapfit"]["max_cards"]
        W, SW, SG = br["main_width"], br["slot_width"], br["slot_gap"]

        rows = []
        for serial in serials:
            info = struct[serial]
            dom = info["dominant_block"]
            if dom is None:
                continue
            rare = [b for b in sorted(info["blocks"])
                    if b != dom and len(info["blocks"][b]["cards"]) <= rare_max]
            if not rare and br["serials"] == "with_rare":
                continue
            rows.append((serial, dom, rare))
        if rows:
            matches = {(s, c["block"]): c for s, _, _ in rows
                       for c in block_remap_candidates(df, struct, cfg, s)}

            fig, axes = plt.subplots(len(rows), 1,
                                     figsize=(13, br["panel_height"] * len(rows)),
                                     squeeze=False)
            for ax, (serial, dom, rare) in zip(axes[:, 0], rows):
                info = struct[serial]
                nums = sorted(n for n in info["occupied"] if n // step == dom)
                lo, hi = nums[0], nums[-1]
                span = max(hi - lo, 1)
                to_x = lambda n: (n - lo) / span * W

                axis = np.arange(lo, hi + 1)
                present = np.isin(axis, nums).astype(int)
                ax.fill_between([to_x(v) for v in axis], present, step="mid",
                                color=palette[serial], alpha=.55, lw=0)

                gap_of = {}
                for g_lo, g_hi, g_size in find_gaps(set(nums)):
                    if g_size >= cfg["block_coverage"]["min_gap_marked"]:
                        ax.axvspan(to_x(g_lo), to_x(g_hi), ymax=.53,
                                   color="#999999", alpha=.30)
                        gap_of[(g_lo, g_hi)] = ((to_x(g_lo) + to_x(g_hi)) / 2, g_size)

                ax.axvline(W + SG / 2, color="#888888", ls="--", lw=1)
                ax.text(W / 2, -.30, f"{ring_label(lo, cfg)} - {ring_label(hi, cfg)}"
                                     f"   ({len(nums)} rings, {ring_label(dom * step, cfg)[:prefix]}.xxx)",
                        ha="center", va="top", fontsize=8, color="#444444")

                for i, block in enumerate(rare):
                    x0 = W + SG + i * (SW + SG)
                    hit = matches.get((serial, block))
                    rn = sorted(n for n in info["occupied"] if n // step == block)
                    r_lo, r_hi = rn[0], rn[-1]
                    ax.add_patch(plt.Rectangle((x0, 0), SW, 1,
                                               facecolor=palette[serial], alpha=.55,
                                               edgecolor=hi_colour if hit else "#888888",
                                               linewidth=1.6 if hit else 1.0,
                                               hatch=hatch if hit else None))
                    ax.text(x0 + SW / 2, -.30,
                            f"{ring_label(r_lo, cfg)} - {ring_label(r_hi, cfg)}\n"
                            f"{len(rn)} rings, {len(info['blocks'][block]['cards'])} card(s)",
                            ha="center", va="top", fontsize=7, color="#444444")

                    if not (hit and br["show_arrows"]):
                        if not hit:
                            ax.text(x0 + SW / 2, 1.20, "no matching gap",
                                    ha="center", fontsize=7, color="#888888",
                                    style="italic")
                        continue

                    g_lo, g_hi, g_size = hit["gap"]
                    ax.axvspan(to_x(g_lo), to_x(g_hi), ymax=.53,
                               color=hi_colour, alpha=.22)
                    target = (to_x(g_lo) + to_x(g_hi)) / 2
                    ax.annotate("", xy=(target, 1.06), xytext=(x0 + SW / 2, 1.06),
                                arrowprops=dict(arrowstyle="-|>", color=hi_colour,
                                                lw=1.8, shrinkA=2, shrinkB=2,
                                                connectionstyle="arc3,rad=0.28"))
                    ax.text((target + x0 + SW / 2) / 2, 1.62,
                            f"{block} -> {hit['candidate']}  =  "
                            f"{ring_label(hit['new_lo'], cfg)}-{ring_label(hit['new_hi'], cfg)}"
                            f"  {'(exact fit)' if hit['exact'] else '(fits inside)'}",
                            ha="center", fontsize=8, color=hi_colour, weight="bold")

                width = W + SG + max(len(rare), 1) * (SW + SG)
                ax.set(xlim=(-2, width), ylim=(-.05, 1.95), xticks=[], yticks=[],
                       ylabel=serial)
                for side in ("top", "right", "bottom", "left"):
                    ax.spines[side].set_visible(False)

            axes[0, 0].set_title("Rare ring-number blocks and the holes they "
                                 "would fill (arrow = single-digit remap lands "
                                 "in a gap)")
            finish(fig, "p7_block_remap.png")

    # ---- P5 horizontal bar: flag summary --------------------------------
    if want["flag_summary"] and flags:
        fdf = pd.DataFrame(flags)
        pivot = fdf.pivot_table(index="rule", columns="severity",
                                values="custom_id", aggfunc="count").fillna(0)
        order = [s for s in ["high", "medium", "low"] if s in pivot.columns]
        fig, ax = plt.subplots(figsize=(9, max(3, len(pivot) * .7)))
        pivot[order].plot(kind="barh", stacked=True, ax=ax,
                          color={"high": "#c0392b", "medium": "#e67e22",
                                 "low": "#95a5a6"})
        ax.set(xlabel="flagged rows", ylabel="", title="Flags by rule and severity")
        ax.grid(alpha=.25, axis="x")
        finish(fig, "p5_flag_summary.png")

    # ---- P6 stacked bar: records per year and serial ---------------------
    # Stacked bars because the question is compositional: which serials were in
    # use in a given season. A serial surfacing in a year it has no business
    # being in is visible instantly -- exactly the img_3_66 signature. Segments
    # holding flagged rows are hatched so the chart doubles as a flag map.
    if want["year_serial"] and have_date.empty:
        skip("p6_year_serial", "no row has a usable date")
    elif want["year_serial"]:
        yg = have_date.copy()
        yg["_year"] = yg["_date"].dt.year
        yg["_flagged"] = is_flagged.reindex(yg.index, fill_value=False)
        years = list(range(int(yg["_year"].min()), int(yg["_year"].max()) + 1))
        counts = (yg.pivot_table(index="_year", columns="_serial", values=col["row"],
                                 aggfunc="count").reindex(years).fillna(0))
        hits = (yg[yg["_flagged"]]
                .pivot_table(index="_year", columns="_serial", values=col["row"],
                             aggfunc="count").reindex(years, fill_value=0))

        fig, ax = plt.subplots(figsize=(13, 6))
        bottom = np.zeros(len(years))
        marked = False
        for serial in serials:
            if serial not in counts:
                continue
            vals = counts[serial].to_numpy()
            ax.bar(years, vals, bottom=bottom, color=palette[serial],
                   label=serial, width=.8)
            flagged_vals = (hits[serial].fillna(0).to_numpy()
                            if serial in hits else np.zeros(len(years)))
            if cfg["year_serial"]["hatch_flagged"] and flagged_vals.any():
                # NaN out the empty years, otherwise a zero-height bar still
                # draws its edge as a stray line along the baseline
                drawn = np.where(flagged_vals > 0, flagged_vals, np.nan)
                ax.bar(years, drawn, bottom=bottom, width=.8,
                       facecolor="none", edgecolor=hi_colour, hatch=hatch,
                       linewidth=1.2)
                marked = True
            bottom += vals
        for x, total in zip(years, bottom):
            if total:
                ax.text(x, total + 3, int(total), ha="center", fontsize=7, color="#555")
        note = " (hatched = rows flagged by a rule)" if marked else ""
        ax.set(xlabel="ringing year", ylabel="amount",
               title=f"Records per year and serial{note}")
        ax.set_xticks(years)
        ax.set_xticklabels(years, rotation=45, ha="right", fontsize=8)
        ax.legend(title="serial", fontsize=8)
        ax.grid(alpha=.25, axis="y")
        finish(fig, "p6_year_serial.png")

    return saved
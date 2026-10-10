#!/usr/bin/env python3
"""
silent_errors_run.py -- entry point that joins the two layers.

    silent_errors.py        logic: load -> discover -> rules -> review output
    silent_errors_plots.py  optional add-on: charts of that same result

The logic module also runs on its own (`python3 silent_errors.py`) and then
simply produces no charts. This runner adds them.

Everything else is configured where it belongs: input, filtering, rules and
output format in silent_errors.CONFIG, chart appearance in
silent_errors_plots.PLOT_CONFIG.
"""

from __future__ import annotations

import silent_errors as core
import silent_errors_plots as plots

# =============================================================================
# CONFIG
# =============================================================================

RUN = {
    "plots_enabled": True,          # master switch for the charts
}


def main():
    cfg = core.CONFIG
    df, struct, flags = core.run(cfg)

    # No flags means no review file and nothing worth drawing -- the logic
    # module has already said so.
    if not flags or not RUN["plots_enabled"]:
        return

    saved = plots.make_plots(df, struct, flags, cfg)
    if saved:
        print("\nplots written:")
        for path in saved:
            print(f"  {path}")


if __name__ == "__main__":
    main()
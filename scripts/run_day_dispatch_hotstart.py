#!/usr/bin/env python3
"""
Run a day of dispatches using disk checkpoints between intervals.

Each dispatch interval is executed in a fresh Python process via
``run_dispatch_hotstart.py`` so that:

- the hot-start semantics match the intended one-process-per-segment workflow;
- terminal checkpoints can be reused later to resume any boundary directly;
- long multi-interval runs do not need to keep all historical data in memory.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd

import hotstart_checkpoint as hcp
import run_dispatch_tds as rdt
from prepare_day_dispatches import enumerate_dispatches
from run_dispatch_hotstart import build_signature


RESULTS = rdt.RESULTS
DEFAULT_DISPATCHES_PER_HOUR = 4
DEFAULT_DISPATCH_INTERVAL = 900
SCRIPT_DIR = Path(__file__).resolve().parent
RUN_DISPATCH_SCRIPT = SCRIPT_DIR / "run_dispatch_hotstart.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dispatch-dir", type=Path, default=RESULTS / "dispatches_day96")
    parser.add_argument("--results-dir", type=Path, default=RESULTS / "day96_hotstart")
    parser.add_argument("--checkpoints-dir", type=Path, default=RESULTS / "checkpoints")
    parser.add_argument("--hour-start", type=int, default=0)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--dispatches-per-hour", type=int, default=DEFAULT_DISPATCHES_PER_HOUR)
    parser.add_argument("--dispatch-interval", type=int, default=DEFAULT_DISPATCH_INTERVAL)
    parser.add_argument("--opf-case", type=Path, default=rdt.DEFAULT_OPF_CASE)
    parser.add_argument("--dyn-case", type=Path, default=rdt.DEFAULT_DYN_CASE)
    parser.add_argument("--stable-dyn-case", type=Path, default=rdt.DEFAULT_STABLE_DYN_CASE)
    parser.add_argument("--curve-file", type=Path, default=rdt.DEFAULT_CURVE_FILE)
    parser.add_argument("--agc-interval", type=int, default=4)
    parser.add_argument("--kp", type=float, default=0.03)
    parser.add_argument("--ki", type=float, default=0.01)
    parser.add_argument("--dispatch-target-ramp-seconds", type=int, default=0)
    parser.add_argument(
        "--governor-target-schedule",
        choices=("step", "boundary_ramp", "midpoint_trajectory"),
        default="midpoint_trajectory",
    )
    parser.add_argument("--init-mode", choices=("dispatch", "first"), default="first")
    parser.add_argument("--wind-prefix", action="append", default=None)
    parser.add_argument("--solar-prefix", action="append", default=None)
    parser.add_argument("--apply-governor-targets", dest="apply_governor_targets", action="store_true")
    parser.add_argument("--no-apply-governor-targets", dest="apply_governor_targets", action="store_false")
    parser.add_argument(
        "--apply-dg-targets",
        dest="apply_dg_targets",
        action="store_true",
        help="Deprecated and ignored. DG/PVD1/ESD1 dispatch targets are not applied.",
    )
    parser.add_argument(
        "--no-apply-dg-targets",
        dest="apply_dg_targets",
        action="store_false",
        help="Deprecated compatibility flag; DG/PVD1/ESD1 dispatch targets are never applied.",
    )
    parser.add_argument("--start-checkpoint", type=Path, default=None,
                        help="Optional checkpoint to use for the first dispatch in the requested sequence.")
    parser.set_defaults(apply_governor_targets=False, apply_dg_targets=False)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.results_dir.mkdir(parents=True, exist_ok=True)
    args.checkpoints_dir.mkdir(parents=True, exist_ok=True)

    wind_prefixes = rdt.normalize_prefixes(args.wind_prefix, rdt.DEFAULT_WIND_PREFIXES)
    solar_prefixes = rdt.normalize_prefixes(args.solar_prefix, rdt.DEFAULT_SOLAR_PREFIXES)
    dyn_case = rdt.adapt_dyn_case(args.dyn_case, args.stable_dyn_case)
    signature = build_signature(
        args,
        dyn_case=dyn_case,
        wind_prefixes=wind_prefixes,
        solar_prefixes=solar_prefixes,
    )
    hcp.ensure_family_manifest(args.checkpoints_dir, signature)

    tasks = enumerate_dispatches(args.hour_start, args.hours, args.dispatches_per_hour)
    previous_checkpoint = args.start_checkpoint
    rows: list[dict[str, object]] = []

    for pos, (hour, dispatch) in enumerate(tasks):
        label = f"h{hour}d{dispatch}"
        dispatch_json = args.dispatch_dir / f"{label}_dispatch.json"
        if not dispatch_json.exists():
            raise RuntimeError(
                f"Missing dispatch JSON for {label}: {dispatch_json}. "
                "Run prepare_day_dispatches.py first."
            )

        next_dispatch_json = None
        if pos + 1 < len(tasks):
            next_hour, next_dispatch = tasks[pos + 1]
            candidate = args.dispatch_dir / f"h{next_hour}d{next_dispatch}_dispatch.json"
            if candidate.exists():
                next_dispatch_json = candidate

        cmd = [
            sys.executable,
            str(RUN_DISPATCH_SCRIPT),
            "--dispatch-json", str(dispatch_json),
            "--label", label,
            "--results-dir", str(args.results_dir),
            "--checkpoints-dir", str(args.checkpoints_dir),
            "--opf-case", str(args.opf_case),
            "--dyn-case", str(args.dyn_case),
            "--stable-dyn-case", str(args.stable_dyn_case),
            "--curve-file", str(args.curve_file),
            "--duration-seconds", str(args.dispatch_interval),
            "--agc-interval", str(args.agc_interval),
            "--kp", str(args.kp),
            "--ki", str(args.ki),
            "--dispatch-target-ramp-seconds", str(args.dispatch_target_ramp_seconds),
            "--governor-target-schedule", args.governor_target_schedule,
            "--init-mode", args.init_mode,
        ]
        if next_dispatch_json is not None:
            cmd.extend(["--next-dispatch-json", str(next_dispatch_json)])
        for prefix in wind_prefixes:
            cmd.extend(["--wind-prefix", prefix])
        for prefix in solar_prefixes:
            cmd.extend(["--solar-prefix", prefix])
        if previous_checkpoint is not None:
            cmd.extend(["--checkpoint-in", str(previous_checkpoint)])
        if args.apply_governor_targets:
            cmd.append("--apply-governor-targets")
        else:
            cmd.append("--no-apply-governor-targets")
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Hot-start dispatch {label} failed with code {completed.returncode}\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )
        summary_csv = args.results_dir / f"{label}_summary.csv"
        row = pd.read_csv(summary_csv).iloc[0].to_dict()
        rows.append(row)

        previous_checkpoint = hcp.checkpoint_dir(args.checkpoints_dir, signature, label)

    summary = pd.DataFrame(rows)
    summary_csv = args.results_dir / "daily_hotstart_summary.csv"
    summary.to_csv(summary_csv, index=False)

    print(f"results_dir={args.results_dir}")
    print(f"checkpoints_dir={hcp.checkpoint_family_dir(args.checkpoints_dir, signature)}")
    print(f"summary_csv={summary_csv}")
    print(f"dispatches={len(summary)}")
    if not summary.empty:
        print(summary[["label", "resume_mode", "checkpoint_in", "end_dae_t", "final_hz"]].to_string(index=False))


if __name__ == "__main__":
    main()

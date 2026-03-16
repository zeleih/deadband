# Deadband Demo Refresh

This directory contains a stable-branch refresh of the original deadband demo.
The goal of the refresh is not to preserve the old notebook layout byte-for-byte,
but to make the workflow reproducible on current ANDES/AMS code while keeping
the key frequency-control behaviors visible.

The scripts here assume an `openandes` workspace where `andes/`, `ams/`, and
`demo/` are sibling folders. If you export this demo elsewhere, set
`OPENANDES_WORKSPACE=/path/to/workspace` so the scripts can still locate the
local source trees.

## What was wrong in the original demo

1. The old README required installing a development-only `andes@pvd2` branch
   because the case still used `PVD2` and `ESD2`.
2. The old workflow was tightly coupled to a live AMS-to-ANDES handoff, which
   became brittle once AMS/ANDES 2.0 internals changed.
3. Each 15-minute dispatch was simulated as a cold start. Adjacent dispatches
   therefore showed an artificial boundary reset, even when the operating point
   should have evolved continuously.
4. After switching initialization from dispatch averages to the first curve
   sample in the interval, some dispatches failed because `CurveInterp.csv`
   contained negative PV samples created by interpolation plus noise.
5. The AGC governor limit path had a sign bug: saturation logic needed to
   preserve the sign of the requested AGC correction while respecting available
   upward and downward headroom.

## What changed

- `scripts/run_dispatch_tds.py`
  - accepts either a precomputed `dispatch JSON -> TDS` workflow or a fresh AMS
    ACOPF recomputation when `--dispatch-json` is omitted;
  - auto-adapts the legacy dynamic workbook from `PVD2/ESD2` to stable
    `PVD1/ESD1`, and inserts `fdbdu` so the upper deadband remains explicit on
    stable ANDES;
  - defaults to `init_mode=first`, `agc_interval=4`, `kp=0.05`, `ki=0.0625`;
  - fixes AGC governor clipping so positive commands are not incorrectly routed
    through the negative limit.
- `scripts/run_day_dispatch_tds.py`
  - generalizes the demo to arbitrary `hXdY` dispatches and full 96-dispatch
    daily sweeps;
  - keeps `init_mode=first` as the default and can retry early failures with a
    fallback init mode if needed.
- `scripts/compare_dispatch_pair_hotstart.py`
  - compares the original cold-stitched workflow against a memory hot-start of
    the second dispatch.
- `scripts/run_dispatch_pair_continuous.py`
  - runs two adjacent dispatches as a single 1800-second simulation and can
    directly plot the result against the cold-stitched baseline.
- `cases/CurveInterp.csv`
  - clips PV samples to non-negative values so `init_mode=first` is physically
    valid and no longer trips over negative irradiance artifacts.

## Stable model migration

The historical deadband case used custom `PVD2` and `ESD2` sheets to work
around missing stable-branch support.

- `ESD2` is no longer needed here because stable `ESD1` is sufficient for this
  demo.
- `PVD2` is migrated to `PVD1` with an explicit `fdbdu` column so the legacy
  deadband intent remains representable on stable ANDES.

The source workbook remains `cases/IL200_dyn_db2.xlsx`. A stable-compatible copy
is generated automatically when the scripts run.

## Reproduce the 5h boundary checks

Run the commands below from `demo/deadband/`.

```bash
python scripts/run_dispatch_tds.py \
  --hour 5 --dispatch 1 \
  --results-dir results/repro_h5_pair \
  --label h5d1

python scripts/run_dispatch_tds.py \
  --hour 5 --dispatch 2 \
  --results-dir results/repro_h5_pair \
  --label h5d2

python scripts/compare_dispatch_pair_hotstart.py \
  --first-dispatch-json results/repro_h5_pair/h5d1_dispatch.json \
  --second-dispatch-json results/repro_h5_pair/h5d2_dispatch.json \
  --first-cold-csv results/repro_h5_pair/h5d1_frequency.csv \
  --second-cold-csv results/repro_h5_pair/h5d2_frequency.csv \
  --kp 0.05 --ki 0.0625 --agc-interval 4 \
  --init-mode first --resume-mode memory \
  --results-dir results/repro_h5_pair \
  --label h5d1_h5d2_default_statehot

python scripts/run_dispatch_pair_continuous.py \
  --first-dispatch-json results/repro_h5_pair/h5d1_dispatch.json \
  --second-dispatch-json results/repro_h5_pair/h5d2_dispatch.json \
  --first-cold-csv results/repro_h5_pair/h5d1_frequency.csv \
  --second-cold-csv results/repro_h5_pair/h5d2_frequency.csv \
  --kp 0.05 --ki 0.0625 --agc-interval 4 \
  --init-mode first \
  --results-dir results/repro_h5_pair \
  --label h5d1_h5d2_default_continuous
```

Notes:

- `resume-mode=memory` is the recommended hot-start path here. Snapshot reloads
  were less stable on this case and are left as an exploratory option.
- The release bundle committed to this repo is intentionally small. For larger
  sweeps, regenerate locally instead of committing the full `results/` tree.

## Validation figures

The committed release artifacts live in `results/release_h5_pair/`.

### 1. Cold-stitched vs memory hot-start second dispatch

Committed figure:

![h5 hotstart vs cold](results/release_h5_pair/h5d1_h5d2_default_statehot_hotstart_vs_cold.png)

Key numbers from
[`results/release_h5_pair/h5d1_h5d2_default_statehot_hotstart_summary.csv`](results/release_h5_pair/h5d1_h5d2_default_statehot_hotstart_summary.csv):

- cold boundary jump: `-0.00620 Hz`
- hot-start boundary jump: `0.00000 Hz`
- immediate post-boundary hot-start step: `+0.00534 Hz`

This shows the cold restart discontinuity is numerical workflow error, not a
physical frequency response. Carrying the terminal dynamic state into the next
dispatch removes that jump.

### 2. Cold-stitched vs continuous 1800-second run

Committed figure:

![h5 continuous vs stitched](results/release_h5_pair/h5d1_h5d2_default_continuous_vs_stitched.png)

Key numbers from
[`results/release_h5_pair/h5d1_h5d2_default_continuous_vs_stitched_summary.csv`](results/release_h5_pair/h5d1_h5d2_default_continuous_vs_stitched_summary.csv):

- cold boundary jump: `-0.00620 Hz`
- continuous `899 s -> 900 s` step: `-0.01295 Hz`

The continuous run does not reset to zero at the dispatch boundary. Instead, it
shows a genuine dynamic step as the second interval begins, which is the
behavior the cold-stitched baseline was masking.

## Scope of this refresh

This refresh focuses on getting the demo onto a stable ANDES/AMS workflow and
making dispatch-to-TDS validation reproducible. It is the foundation for later
deadband sensitivity studies, not the final word on deadband tuning itself.

## License

This subdirectory follows the license in the repository root.

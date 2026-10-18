#!/usr/bin/env bash
# Rebuild every number in the paper from the corpus (about 10-30 min; 20 seeds per mode).
# Writes paper/results/*.json and regenerates paper/paper.tex.   Run from the repo root:  bash paper/reproduce.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export DSTS_MEAN_FROM=refs                 # clean protocol: mean face and tau from reference photos only
python paper/analytic_costs.py             # tau, analytic costs   -> results/clean_analytic.json
export DSTS_TAU=$(python -c "import json;print(json.load(open('paper/results/clean_analytic.json'))['tau'])")   # tau from reference photos only
python paper/run_experiments.py clean      # main 20-seed results  -> results/dacs_clean_runs.json
python paper/run_experiments.py rpsmall    # B=2 and B=5           -> results/routing_small_multiseed.json
python paper/run_experiments.py scale      # scaling               -> results/dacs_scale.json
python paper/run_experiments.py member     # membership leakage    -> results/dacs_membership.json
python paper/run_experiments.py openset    # visitor / open set    -> results/dacs_openset.json
python paper/build_paper.py                # fill template         -> paper/paper.tex
python paper/tools/check_tex.py paper/paper.tex

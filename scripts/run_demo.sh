#!/usr/bin/env bash
# One command for the demo.
#
#   bash scripts/run_demo.sh                 scripted walk, then the real system if a run exists, else the standalone demo
#   bash scripts/run_demo.sh --standalone    scripted walk, then the standalone (offline, fake-backed) demo on :8001
#   bash scripts/run_demo.sh --no-server     scripted walk only
#
# The real system needs a run folder: python manage.py run --seeds 42 --days 1   (or python manage.py pipeline --days 1)
set -euo pipefail
cd "$(dirname "$0")/.."

SEED="${DSTS_SEED:-42}"

echo "== DSTS demo =="
echo "1. Scripted B1 -> B5 walk on the offline demo campus (terminal only: no server, no corpus, no pipeline run)"
python -m scripts.terminal_walk

if [[ "${1:-}" == "--no-server" ]]; then
  exit 0
fi

echo
if [[ "${1:-}" != "--standalone" && -f "runs/seed_${SEED}/state/phase4_results.json" ]]; then
  echo "2. Real system, run seed ${SEED}  (Ctrl+C to stop)"
  echo "   console        http://localhost:8000/"
  echo "   Lane D UI      http://localhost:8000/lane-d/"
  DSTS_DEMO_MODE=1 python manage.py serve --seed "${SEED}"
else
  echo "2. Standalone demo on http://localhost:8001/  (Ctrl+C to stop)"
  echo "   (no run folder runs/seed_${SEED}, or --standalone given; the real system needs: python manage.py run --seeds ${SEED} --days 1)"
  python -m uvicorn dsts.api.standalone:app --port 8001
fi

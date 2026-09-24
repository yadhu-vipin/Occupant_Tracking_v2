"""Consolidated end-to-end pipeline entry points (test_5 restructuring).

One file per pipeline step, run in this order:

    1. pipeline/enroll_and_broadcast.py --all
    2. pipeline/generate_events.py --seed <N>
    3. pipeline/route_and_recognize.py --phase 2
    4. pipeline/route_and_recognize.py --phase 3
    5. pipeline/phase4_state.py
    6. pipeline/query.py --caller .. --query Q6 ..
    7. pipeline/evaluate_pipeline.py

See RUNBOOK.md for details.
"""

"""End-to-end pipeline stages, one file per step. ``python manage.py run --seeds N`` runs them all for each seed,
inside that seed's own folder ``runs/seed_XX/`` (see shared/paths.py).

Run order:

    0. python -m population.build --seed <N>     (who is registered where, roles)
    1. pipeline/enroll_and_broadcast.py --all
    2. pipeline/generate_events.py --seed <N>
    3. pipeline/route_and_recognize.py --phase 2
    4. pipeline/route_and_recognize.py --phase 3
    5. pipeline/phase4_state.py
    6. pipeline/query.py --caller .. --query Q6 ..
    7. evaluation/evaluate_events.py

See README.md for details.
"""

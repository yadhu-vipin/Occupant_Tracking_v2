"""Reusable building blocks for the LSH + Bloom-filter routing pipeline.

- `config`        : all filesystem paths and tunable constants, in one place.
- `lsh`           : preprocessing (mean-centre + L2-normalise) and the
                    random-hyperplane LSH primitives.
- `bloom`         : the compact, one-way per-building Bloom-filter summary.
- `verification`  : occupant reference matrices, centroids, and the
                    per-reference-photo voting verifier.
"""

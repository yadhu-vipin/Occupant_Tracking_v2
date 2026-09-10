# The federation contract

`params.json` and `mean_face.npy` must be **byte-identical for every building**.

If they differ, nothing crashes -- the codes just silently stop matching and
routing quietly fails. That is why `params.json` carries a `params_hash` over
`seed`, `k`, `L`, `embed_dim`, `sizing_occupants` and the SHA-256 of
`mean_face.npy`, and why every artifact carries that hash so `merge_check.py`
can refuse a mismatched set.

**Do not edit either file locally.** Pull the committed versions.

If the contract genuinely has to change (new `K`/`L` from step 06, or a
recomputed mean face because the population shifted), then:

1. update `params.json`
2. `python -m buildinglib.params --resync` and paste the printed hashes
3. bump `schema_version`
4. **every building rebuilds** -- old filters are not compatible

`mean_face.npy` is a copy of `lane_a/artifacts/mean_face.npy`. It is committed
here rather than read from there because `artifacts/` is git-ignored, so a
teammate who clones would not have it. It is an average over 20,000 faces and
identifies nobody.

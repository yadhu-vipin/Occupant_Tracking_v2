# corpus/ — drop the shared data here

Two files, distributed out-of-band (they are git-ignored, not in the repo):

| file | size |
|---|---|
| `emb_arcface.npy` | ~39 MB — the 20,000 x 512 ArcFace embeddings |
| `meta.csv`        | ~2 MB  — the label for each row |

Once both are here, every CLI in `buildings/` finds them automatically:

    python build_building.py --building-id building_3
    python query_node.py --capture-row 16020 --at building_1

Either shipped copy of `meta.csv` works (`artifacts/meta.csv` has occupant_id as
an int, `embeddings_arcface/meta.csv` as a zero-padded string — the code
normalises it).

If you have the full `lane_a/` pipeline tree, the CLIs also fall back to
`../embeddings_arcface/` and `../artifacts/`, so you don't have to copy anything.

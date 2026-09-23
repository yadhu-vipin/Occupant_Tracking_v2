"""Write meta.csv variants that regroup the same 500 occupants into fewer,
larger buildings, for comparing topology scale (10x50 / 5x100 / 2x250).

Only the ``building`` column is remapped -- occupant_id, split, image_path,
and row order are untouched, so the shared corpus/emb_arcface.npy stays
row-aligned and is never copied.

    python build_variant_meta.py
"""
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent


def merged(n_from: int, group_size: int) -> dict[str, str]:
    """building_1..building_n -> building_1..building_(n/group_size), adjacent groups."""
    return {
        f"building_{i}": f"building_{(i - 1) // group_size + 1}"
        for i in range(1, n_from + 1)
    }


def write_variant(meta: pd.DataFrame, name: str, mapping: dict[str, str]) -> None:
    out_dir = HERE / "variants" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    variant = meta.assign(building=meta["building"].map(mapping))
    if variant["building"].isna().any():
        raise ValueError(f"{name}: mapping did not cover every building in meta.csv")
    variant.to_csv(out_dir / "meta.csv", index=False)
    counts = variant.groupby("building")["occupant_id"].nunique().to_dict()
    print(f"{name}: wrote {out_dir / 'meta.csv'} -- occupants per building: {counts}")


def main() -> None:
    meta = pd.read_csv(HERE / "corpus" / "meta.csv")
    write_variant(meta, "b5", merged(10, 2))   # 10x50 -> 5x100
    write_variant(meta, "c2", merged(10, 5))   # 10x50 -> 2x250


if __name__ == "__main__":
    main()

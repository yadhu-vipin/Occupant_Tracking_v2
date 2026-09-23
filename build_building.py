"""Build one building's Bloom filter (or all of them) from the shared corpus.

    python build_building.py --building-id building_3
    python build_building.py --all

Reads the full embeddings + meta, slices out the building, enrolls its
occupants, and writes ``out/<building>.npz`` plus a manifest. That npz is the
deliverable -- commit it and push it.

Exit codes: 0 ok, 2 contract mismatch, 3 enrollment failed, 4 refused overwrite.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from buildinglib.artifact import load_building, save_building          # noqa: E402
from buildinglib.enroll import enroll_building                          # noqa: E402
from buildinglib.params import ContractMismatch, check_vendored, load_params  # noqa: E402
from buildinglib.split import default_emb_path, default_meta_path, list_buildings, split_building            # noqa: E402

DEFAULT_EMB = default_emb_path()
DEFAULT_META = default_meta_path()
DEFAULT_OUT = HERE / "out"


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--building-id", help="build just this building, e.g. building_3")
    target.add_argument("--all", action="store_true", help="build every building in the meta")
    ap.add_argument("--emb", type=Path, default=DEFAULT_EMB, help="full embeddings .npy")
    ap.add_argument("--meta", type=Path, default=DEFAULT_META, help="full meta .csv")
    ap.add_argument("--shared", type=Path, default=None, help="contract dir (default ./shared)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where artifacts land")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing artifact whose contents differ")
    return ap.parse_args(argv)


def build_one(raw, meta, building_id, params, out_dir, force):
    """Enroll one building and write its artifact. Returns the manifest-ish dict."""
    emb_b, meta_b = split_building(raw, meta, building_id)
    bf = enroll_building(emb_b, meta_b, building_id, params)

    path = Path(out_dir) / f"{building_id}.npz"
    if path.exists() and not force:
        existing = load_building(path)
        if existing.params_hash != bf.params_hash:
            raise SystemExit(
                f"[4] {path.name} already exists and was built against a different "
                f"contract\n    existing {existing.params_hash[:16]}...\n"
                f"    current  {bf.params_hash[:16]}...\n"
                f"    Re-run with --force only if you mean to replace the published filter."
            )

    _, manifest_path, digest = save_building(path, bf, params)
    print(f"  {bf.summary()}")
    print(f"  -> {path.name} ({path.stat().st_size} bytes), bits_sha256 {digest[:16]}...")
    return {"building": building_id, "n_occupants": bf.n_occupants,
            "n_items": bf.n_items, "bits_sha256": digest}


def main(argv=None):
    args = parse_args(argv)

    try:
        check_vendored()
        params = load_params(args.shared)
    except ContractMismatch as exc:
        print(f"[2] contract error:\n{exc}", file=sys.stderr)
        return 2

    for p in (args.emb, args.meta):
        if not p.exists():
            print(f"[3] missing input: {p}\n    Pass --emb / --meta, or place the shared "
                  f"corpus at the default paths.", file=sys.stderr)
            return 3

    print(f"contract  {params.summary()}")
    raw = np.load(args.emb)
    meta = pd.read_csv(args.meta)
    print(f"corpus    {raw.shape} from {args.emb.name}, {len(meta)} meta rows")

    if raw.shape[0] != len(meta):
        print(f"[3] emb has {raw.shape[0]} rows but meta has {len(meta)} -- these are "
              f"not a matching pair", file=sys.stderr)
        return 3

    targets = list_buildings(meta) if args.all else [args.building_id]
    print(f"building  {', '.join(targets)}\n")

    try:
        for bid in targets:
            build_one(raw, meta, bid, params, args.out, args.force)
    except (ValueError, KeyError) as exc:
        print(f"[3] enrollment failed: {exc}", file=sys.stderr)
        return 3

    print(f"\nwrote {len(targets)} artifact(s) to {args.out}")
    print("Next: python merge_check.py out/*.npz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

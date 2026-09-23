"""Check that a published artifact really is what its source data produces.

    python verify_building.py out/building_3.npz

Reloads the artifact, re-runs enrollment from the corpus, and asserts the two
bit arrays are identical. This is the determinism check -- run it before you
push, and run it if someone else's filter disagrees with yours.

Without --emb/--meta it does the offline checks only (contract, integrity,
manifest agreement), which is all a reviewer needs to sanity a pushed file.

Exit codes: 0 ok, 2 contract mismatch, 5 re-derivation differs.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from buildinglib.artifact import bits_sha256, load_building, read_manifest  # noqa: E402
from buildinglib.enroll import enroll_building                              # noqa: E402
from buildinglib.params import ContractMismatch, load_params                # noqa: E402
from buildinglib.split import default_emb_path, default_meta_path, split_building                                # noqa: E402

DEFAULT_EMB = default_emb_path()
DEFAULT_META = default_meta_path()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("artifact", type=Path, help="an out/<building>.npz")
    ap.add_argument("--emb", type=Path, default=DEFAULT_EMB)
    ap.add_argument("--meta", type=Path, default=DEFAULT_META)
    ap.add_argument("--shared", type=Path, default=None)
    ap.add_argument("--offline", action="store_true",
                    help="skip re-derivation even if the corpus is available")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    try:
        bf = load_building(args.artifact)          # integrity-checks bits_sha256
    except ContractMismatch as exc:
        print(f"[2] {exc}", file=sys.stderr)
        return 2

    print(f"{args.artifact.name}")
    print(f"  building     {bf.building_id}")
    print(f"  params_hash  {bf.params_hash}")
    print(f"  geometry     m={bf.bloom.m} hashes={bf.bloom.n_hashes} seed={bf.bloom.seed}")
    print(f"  contents     {bf.n_occupants} occupants, {bf.n_items} items, "
          f"fill={bf.fill_ratio:.4f}")
    print("  integrity    bits_sha256 verified")

    manifest = read_manifest(args.artifact)
    if manifest is None:
        print("  manifest     MISSING (expected a .manifest.json sidecar)")
    else:
        drift = [k for k in ("building_id", "params_hash", "m", "n_hashes", "n_items",
                             "n_occupants") if str(manifest.get(k)) != str(getattr(
                                 bf, k, getattr(bf.bloom, k, None)))]
        print(f"  manifest     {'agrees' if not drift else 'DISAGREES on ' + ', '.join(drift)}")

    try:
        params = load_params(args.shared)
    except ContractMismatch as exc:
        print(f"[2] local contract error:\n{exc}", file=sys.stderr)
        return 2

    if params.params_hash != bf.params_hash:
        print(f"\n[2] this artifact was built against a DIFFERENT contract:\n"
              f"    artifact {bf.params_hash}\n    local    {params.params_hash}",
              file=sys.stderr)
        return 2
    print("  contract     matches the local shared/")

    if args.offline or not (args.emb.exists() and args.meta.exists()):
        print("\noffline checks only (corpus not read)")
        return 0

    print(f"\nre-deriving from {args.emb.name}...")
    raw = np.load(args.emb)
    meta = pd.read_csv(args.meta)
    emb_b, meta_b = split_building(raw, meta, bf.building_id)
    rebuilt = enroll_building(emb_b, meta_b, bf.building_id, params)

    a = bits_sha256(np.packbits(bf.bloom.bits, bitorder="big"))
    b = bits_sha256(np.packbits(rebuilt.bloom.bits, bitorder="big"))
    if a != b:
        print(f"[5] re-derivation DIFFERS\n    artifact {a}\n    rebuilt  {b}\n"
              f"    Same contract, different bits -- check you are using the same "
              f"emb_arcface.npy.", file=sys.stderr)
        return 5

    if rebuilt.n_occupants != bf.n_occupants or rebuilt.n_items != bf.n_items:
        print(f"[5] counts differ: {rebuilt.n_occupants}/{rebuilt.n_items} rebuilt vs "
              f"{bf.n_occupants}/{bf.n_items} stored", file=sys.stderr)
        return 5

    print(f"  re-derived   BIT-IDENTICAL ({a[:16]}...)")
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

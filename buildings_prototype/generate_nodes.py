"""Build a building's deployment folder: raw embeddings, config, filters, state DBs.

    python generate_nodes.py --building-id building_3
    python generate_nodes.py --all

For each building, writes ``nodes/<building>/``:

    building.json           manifest
    config.json             merged params, byte-identical across every folder
    mean_face.npy           byte-identical across every folder
    embeddings/
        emb_raw.npy          this building's raw embedding rows       (gitignored)
        meta.csv              this building's meta rows
        refs.npy               (n_occ, R, dim) preprocessed voting tensor  (gitignored)
        occupant_ids.json      the n_occ occupant ids, in refs.npy order
    filters/                 the OTHER 9 buildings' Bloom filters, copied from out/
    state/
        registered.db         empty, table registered_state
        visitor.db             empty, table visitor_state

Requires ``out/*.npz`` (published Bloom filters -- run ``build_building.py --all``
first) and the shared corpus (``--emb`` / ``--meta``).

Exit codes: 0 ok, 2 contract mismatch, 3 missing input / enrollment failed,
4 refused overwrite.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from buildinglib.params import ContractMismatch, check_vendored, load_params  # noqa: E402
from buildinglib.refs import reference_tensor                                  # noqa: E402
from buildinglib.split import (default_emb_path, default_meta_path,             # noqa: E402
                               list_buildings, split_building)
from dsts.state.store import FakeOccupantRegistry                              # noqa: E402
from nodelib.deploy import DeployedBuilding, SplitSqliteStore, write_node_config  # noqa: E402

DEFAULT_EMB = default_emb_path()
DEFAULT_META = default_meta_path()
DEFAULT_SHARED = HERE / "shared"
DEFAULT_OUT = HERE / "nodes"
DEFAULT_FILTERS_SRC = HERE / "out"


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--building-id", help="build just this folder, e.g. building_3")
    target.add_argument("--all", action="store_true", help="build every building in the meta")
    ap.add_argument("--emb", type=Path, default=DEFAULT_EMB, help="full embeddings .npy")
    ap.add_argument("--meta", type=Path, default=DEFAULT_META, help="full meta .csv")
    ap.add_argument("--shared", type=Path, default=DEFAULT_SHARED, help="contract dir")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where folders land")
    ap.add_argument("--filters-src", type=Path, default=DEFAULT_FILTERS_SRC,
                    help="published Bloom filters to copy in (default ./out)")
    ap.add_argument("--force", action="store_true", help="wipe and rebuild an existing folder")
    return ap.parse_args(argv)


def build_one(raw, meta, building_id, all_buildings, params, args):
    target = args.out / building_id
    if target.exists():
        if not args.force:
            raise SystemExit(
                f"[4] {target} already exists. Re-run with --force to rebuild it."
            )
        shutil.rmtree(target)
    (target / "embeddings").mkdir(parents=True)
    (target / "filters").mkdir(parents=True)

    # 1. this building's own raw embeddings
    emb_b, meta_b = split_building(raw, meta, building_id)
    np.save(target / "embeddings" / "emb_raw.npy", emb_b)
    meta_b.to_csv(target / "embeddings" / "meta.csv", index=False)

    # 2. the preprocessed voting tensor, cached
    ids, refs = reference_tensor(raw, meta, building_id, params.mean_face, params.refs_per_occupant)
    np.save(target / "embeddings" / "refs.npy", refs)
    (target / "embeddings" / "occupant_ids.json").write_text(
        json.dumps([str(i) for i in ids]) + "\n", encoding="utf-8", newline="\n",
    )

    # 3. the merged, byte-identical config
    write_node_config(args.shared, target)

    # 4. the other 9 buildings' published filters
    others = [b for b in all_buildings if b != building_id]
    for other in others:
        for suffix in (".npz", ".manifest.json"):
            src = args.filters_src / f"{other}{suffix}"
            if not src.exists():
                raise SystemExit(
                    f"[3] missing {src} -- run build_building.py --all first"
                )
            shutil.copy2(src, target / "filters" / src.name)

    # 5. empty state DBs
    registry = FakeOccupantRegistry(set(str(i) for i in ids))
    SplitSqliteStore(target / "state", registry).close()

    # 6. manifest
    manifest = {
        "building_id": building_id,
        "params_hash": params.params_hash,
        "n_occupants": len(ids),
        "occupant_ids": [str(i) for i in ids],
        "zones": ["z1", "z2", "z3", "z4", "z5", "z6", "z7", "z8", "zT"],
        "filters_held": others,
        "embeddings": {
            "raw": "embeddings/emb_raw.npy", "meta": "embeddings/meta.csv",
            "refs": "embeddings/refs.npy", "ids": "embeddings/occupant_ids.json",
        },
        "state": {"registered_db": "state/registered.db", "visitor_db": "state/visitor.db"},
        "generated_at": pd.Timestamp.now("UTC").strftime("%Y-%m-%d"),
        "generator": "generate_nodes.py",
    }
    (target / "building.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n",
    )

    # 7. round-trip check
    dep = DeployedBuilding.load(target)
    assert dep.building_id == building_id
    assert len(dep.routing.own_ids) == len(ids) == manifest["n_occupants"]
    assert dep.routing.own_refs.shape == (len(ids), params.refs_per_occupant, params.embed_dim)
    assert len(dep.filters) == 9 and building_id not in dep.filters
    dep.close()

    print(f"  {building_id}: {len(ids)} occupants, 9 filters, refs {dep.routing.own_refs.shape}")


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

    raw = np.load(args.emb)
    meta = pd.read_csv(args.meta)
    if raw.shape[0] != len(meta):
        print(f"[3] emb has {raw.shape[0]} rows but meta has {len(meta)} -- these are "
              f"not a matching pair", file=sys.stderr)
        return 3

    all_buildings = list_buildings(meta)
    targets = all_buildings if args.all else [args.building_id]
    print(f"contract  {params.summary()}")
    print(f"building  {', '.join(targets)}\n")

    try:
        for bid in targets:
            build_one(raw, meta, bid, all_buildings, params, args)
    except (ValueError, KeyError) as exc:
        print(f"[3] failed: {exc}", file=sys.stderr)
        return 3

    if args.all:
        hashes = {}
        for bid in all_buildings:
            cfg_bytes = (args.out / bid / "config.json").read_bytes()
            mean_bytes = (args.out / bid / "mean_face.npy").read_bytes()
            hashes[bid] = (cfg_bytes, mean_bytes)
        first = next(iter(hashes.values()))
        assert all(v == first for v in hashes.values()), (
            "config.json / mean_face.npy differ across buildings -- generation is not "
            "byte-stable"
        )
        print("\nconfig.json + mean_face.npy verified byte-identical across all buildings")

    print(f"\nwrote {len(targets)} folder(s) to {args.out}")
    print("Try it: python -m nodelib.deploy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

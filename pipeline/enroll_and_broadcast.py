"""Build one building's Bloom filter + deployment folder (or all of them),
in one step: enroll, publish the filter, then broadcast it to every peer.

    python pipeline/enroll_and_broadcast.py --building-id building_3
    python pipeline/enroll_and_broadcast.py --all

Merges what used to be two manually-sequenced scripts
(``build_building.py`` then ``generate_nodes.py``) into one
``build_and_broadcast(building_id, ...)`` call per building:

  1. slice the shared corpus for this building, enroll it (LSH codes +
     Bloom filter), publish ``out/<building>.npz`` + manifest
     (ex ``build_building.py:build_one``)
  2. build this building's deployment folder under ``nodes/<building>/``:
     raw embeddings, the preprocessed voting tensor, merged config,
     the OTHER 9 buildings' published filters copied in, and empty state
     DBs -- including the new ``state/pointers.db`` for visit pointers
     (ex ``generate_nodes.py:build_one``, the broadcast step)

Requires the shared corpus (``--emb`` / ``--meta``, defaulting to the
buildinglib corpus paths).

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
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from buildinglib.artifact import load_building, save_building                  # noqa: E402
from buildinglib.enroll import enroll_building                                  # noqa: E402
from buildinglib.params import ContractMismatch, check_vendored, load_params   # noqa: E402
from buildinglib.refs import reference_tensor                                   # noqa: E402
from buildinglib.split import (default_emb_path, default_meta_path,            # noqa: E402
                               list_buildings, split_building)
from dsts.legacy_state.store import FakeOccupantRegistry                       # noqa: E402
from nodelib.deploy import DeployedBuilding, PointerStore, SplitSqliteStore, write_node_config  # noqa: E402

DEFAULT_EMB = default_emb_path()
DEFAULT_META = default_meta_path()
DEFAULT_SHARED = ROOT / "shared"
DEFAULT_FILTERS_OUT = ROOT / "out"
DEFAULT_NODES_OUT = ROOT / "nodes"


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--building-id", help="build just this building, e.g. building_3")
    target.add_argument("--all", action="store_true", help="build every building in the meta")
    ap.add_argument("--emb", type=Path, default=DEFAULT_EMB, help="full embeddings .npy")
    ap.add_argument("--meta", type=Path, default=DEFAULT_META, help="full meta .csv")
    ap.add_argument("--shared", type=Path, default=DEFAULT_SHARED, help="contract dir")
    ap.add_argument("--filters-out", type=Path, default=DEFAULT_FILTERS_OUT,
                    help="where published Bloom filter artifacts land (default ./out)")
    ap.add_argument("--nodes-out", type=Path, default=DEFAULT_NODES_OUT,
                    help="where deployment folders land (default ./nodes)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing artifact/folder")
    return ap.parse_args(argv)


def enroll_one(raw, meta, building_id, params, filters_out, force):
    """Enroll one building and publish its Bloom-filter artifact.
    (ex ``build_building.py:build_one``)
    """
    emb_b, meta_b = split_building(raw, meta, building_id)
    bf = enroll_building(emb_b, meta_b, building_id, params)

    path = Path(filters_out) / f"{building_id}.npz"
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


def broadcast_one(raw, meta, building_id, all_buildings, params, shared, filters_out,
                  nodes_out, force):
    """Build this building's deployment folder and copy in every other
    building's published filter. (ex ``generate_nodes.py:build_one``, the
    broadcast step)
    """
    target = nodes_out / building_id
    if target.exists():
        if not force:
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
    write_node_config(shared, target)

    # 4. broadcast: the other 9 buildings' published filters
    others = [b for b in all_buildings if b != building_id]
    for other in others:
        for suffix in (".npz", ".manifest.json"):
            src = filters_out / f"{other}{suffix}"
            if not src.exists():
                raise SystemExit(f"[3] missing {src} -- enroll it first")
            shutil.copy2(src, target / "filters" / src.name)

    # 5. empty state DBs -- registered/visitor + the new pointers.db
    registry = FakeOccupantRegistry(set(str(i) for i in ids))
    SplitSqliteStore(target / "state", registry).close()
    PointerStore(target / "state").close()

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
        "state": {
            "registered_db": "state/registered.db",
            "visitor_db": "state/visitor.db",
            "pointers_db": "state/pointers.db",
        },
        "generated_at": pd.Timestamp.now("UTC").strftime("%Y-%m-%d"),
        "generator": "pipeline/enroll_and_broadcast.py",
    }
    (target / "building.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n",
    )

    # 7. round-trip check
    dep = DeployedBuilding.load(target)
    assert dep.building_id == building_id
    assert len(dep.routing.own_ids) == len(ids) == manifest["n_occupants"]
    assert dep.routing.own_refs.shape == (len(ids), params.refs_per_occupant, params.embed_dim)
    assert len(dep.filters) == len(all_buildings) - 1 and building_id not in dep.filters
    dep.close()

    print(f"  {building_id}: {len(ids)} occupants, {len(all_buildings) - 1} filters, "
          f"refs {refs.shape}")


def build_and_broadcast(raw, meta, building_id, all_buildings, params, args):
    """Enroll + publish, then build the deployment folder + broadcast filters
    for ONE building, in one call. Only safe when every other building in
    ``all_buildings`` has already been enrolled (its ``.npz`` already exists
    under ``args.filters_out``) -- e.g. adding a single new building to an
    already-built campus. For a fresh ``--all`` run, ``main()`` below runs
    every building's ``enroll_one`` first, then every building's
    ``broadcast_one`` -- see its comment for why the two cannot be
    interleaved per-building.
    """
    enroll_one(raw, meta, building_id, params, args.filters_out, args.force)
    broadcast_one(raw, meta, building_id, all_buildings, params, args.shared,
                 args.filters_out, args.nodes_out, args.force)


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

    all_buildings = list_buildings(meta)
    targets = all_buildings if args.all else [args.building_id]
    print(f"building  {', '.join(targets)}\n")

    args.filters_out.mkdir(parents=True, exist_ok=True)

    # Two passes, not one build_and_broadcast() call per building in a single
    # loop: broadcasting building_1's folder needs EVERY other target
    # building's filter already published, so with --all every building must
    # finish enrollment (pass 1) before any building's broadcast (pass 2)
    # can safely copy its peers' filters in. build_and_broadcast() itself
    # stays available as the one-building convenience call for the
    # single-building case, where its peers are assumed already published
    # from an earlier run -- exactly like the old two-script split required.
    try:
        print("--- pass 1/2: enroll + publish filters ---")
        for bid in targets:
            enroll_one(raw, meta, bid, params, args.filters_out, args.force)
        print("\n--- pass 2/2: build deployment folders + broadcast filters ---")
        for bid in targets:
            broadcast_one(raw, meta, bid, all_buildings, params, args.shared,
                         args.filters_out, args.nodes_out, args.force)
    except (ValueError, KeyError) as exc:
        print(f"[3] failed: {exc}", file=sys.stderr)
        return 3

    if args.all:
        hashes = {}
        for bid in all_buildings:
            cfg_bytes = (args.nodes_out / bid / "config.json").read_bytes()
            mean_bytes = (args.nodes_out / bid / "mean_face.npy").read_bytes()
            hashes[bid] = (cfg_bytes, mean_bytes)
        first = next(iter(hashes.values()))
        assert all(v == first for v in hashes.values()), (
            "config.json / mean_face.npy differ across buildings -- generation is not "
            "byte-stable"
        )
        print("\nconfig.json + mean_face.npy verified byte-identical across all buildings")

    print(f"\nwrote {len(targets)} filter artifact(s) to {args.filters_out}")
    print(f"wrote {len(targets)} deployment folder(s) to {args.nodes_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

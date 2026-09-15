"""One deployed building: its own raw embeddings, one shared config, its two
state DBs, the other 9 buildings' filters, and send()/receive().

Adapts the two halves already in ``buildings/`` -- zero edits to either:

    buildinglib/  -- enrollment (Bloom filters) + the identification cascade
    dsts/state/   -- zones, the BSTS probability table, SQLite state storage

``send()`` is what a building calls when it captures a face: it runs the
existing cascade (``query_node.identify``, unchanged) against its own
occupants, its visitor pool, then the other 9 buildings via their
``receive()``. Whatever it resolves to is recorded into *this* building's own
state DB -- ``registered.db`` for one of its own occupants, ``visitor.db`` for
someone routed in or already pooled. ``receive()`` is the other side: another
building asking "is this one of yours?", answered with ``respond_node.respond``.

Try it:

    python -m nodelib.deploy
"""
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent            # nodelib/
_BUILDINGS = _HERE.parent                           # buildings/
import sys                                          # noqa: E402
if str(_BUILDINGS) not in sys.path:
    sys.path.insert(0, str(_BUILDINGS))

import query_node                                    # noqa: E402
import respond_node                                   # noqa: E402
from buildinglib._vendored.lsh import preprocess, random_hyperplanes   # noqa: E402
from buildinglib.artifact import load_building        # noqa: E402
from buildinglib.node import BuildingNode as RoutingNode, RoutingContract, VisitorPool  # noqa: E402
from buildinglib.params import ContractMismatch, Params, compute_params_hash, sha256_file  # noqa: E402
from dsts.state.bsts import StateTable                # noqa: E402
from dsts.state.store import FakeOccupantRegistry, StateRow  # noqa: E402
from dsts.state.zones import ZONES                    # noqa: E402

SCHEMA_SQL = _BUILDINGS / "dsts" / "state" / "schema.sql"
_FILE_FOR_TABLE = {"registered_state": "registered.db", "visitor_state": "visitor.db"}


# --------------------------------------------------------------------------
# config.json -- one merged, byte-identical file per building
# --------------------------------------------------------------------------

def write_node_config(shared_dir, target_dir):
    """Merge ``shared/params.json`` + ``shared/routing_params.json`` into
    ``target_dir/config.json``, and copy ``mean_face.npy`` alongside.

    Called once per building by ``generate_nodes.py`` with the same
    ``shared_dir`` every time, so the bytes it writes are identical across
    every folder -- checked, not just asserted, by the CLI's ``--all`` run.
    """
    shared_dir, target_dir = Path(shared_dir), Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    params = json.loads((shared_dir / "params.json").read_text(encoding="utf-8"))
    routing = json.loads((shared_dir / "routing_params.json").read_text(encoding="utf-8"))
    if routing["buildings_params_hash"] != params["params_hash"]:
        raise ContractMismatch(
            "shared/routing_params.json is pinned to a different enrollment contract:\n"
            f"    routing buildings_params_hash {routing['buildings_params_hash']}\n"
            f"    params  params_hash            {params['params_hash']}"
        )

    merged = {
        "schema_version": 1,
        "seed": params["seed"], "k": params["k"], "L": params["L"],
        "embed_dim": params["embed_dim"], "target_fpr": params["target_fpr"],
        "refs_per_occupant": params["refs_per_occupant"],
        "sizing_occupants": params["sizing_occupants"],
        "mean_face_sha256": params["mean_face_sha256"],
        "params_hash": params["params_hash"],
        "accept_angle_deg": routing["accept_angle_deg"],
        "min_votes": routing["min_votes"],
        "shortlist_k": routing["shortlist_k"],
        "n_probes": routing["n_probes"],
        "buildings_params_hash": routing["buildings_params_hash"],
    }
    (target_dir / "config.json").write_text(
        json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n",
    )
    shutil.copyfile(shared_dir / "mean_face.npy", target_dir / "mean_face.npy")


def load_node_contract(folder):
    """Build a :class:`RoutingContract` entirely from one folder's own
    ``config.json`` + ``mean_face.npy`` -- no path outside the folder is read.
    """
    folder = Path(folder)
    cfg_path, mean_path = folder / "config.json", folder / "mean_face.npy"
    for p in (cfg_path, mean_path):
        if not p.exists():
            raise ContractMismatch(f"missing {p}")

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    actual_mean_sha = sha256_file(mean_path)
    if actual_mean_sha != cfg["mean_face_sha256"]:
        raise ContractMismatch(
            f"{mean_path} does not match config.json's declared mean_face_sha256"
        )

    recomputed = compute_params_hash(
        cfg["seed"], cfg["k"], cfg["L"], cfg["embed_dim"],
        cfg["sizing_occupants"], cfg["mean_face_sha256"],
    )
    if recomputed != cfg["params_hash"]:
        raise ContractMismatch(f"{cfg_path} params_hash does not match its own fields")

    mean_face = np.load(mean_path)
    params = Params(
        schema_version=int(cfg["schema_version"]), seed=int(cfg["seed"]),
        k=int(cfg["k"]), L=int(cfg["L"]), embed_dim=int(cfg["embed_dim"]),
        target_fpr=float(cfg["target_fpr"]), refs_per_occupant=int(cfg["refs_per_occupant"]),
        sizing_occupants=int(cfg["sizing_occupants"]), mean_face_sha256=cfg["mean_face_sha256"],
        params_hash=cfg["params_hash"], mean_face=mean_face,
    )
    planes = random_hyperplanes(params.embed_dim, params.k, params.L, seed=params.seed)
    return RoutingContract(
        params=params, accept_angle_deg=float(cfg["accept_angle_deg"]),
        min_votes=int(cfg["min_votes"]), shortlist_k=int(cfg["shortlist_k"]),
        n_probes=int(cfg["n_probes"]), hyperplanes=planes,
    )


# --------------------------------------------------------------------------
# per-building state: registered.db + visitor.db
# --------------------------------------------------------------------------

def _table_ddl():
    """``{table_name: "CREATE TABLE IF NOT EXISTS ..."}`` parsed from the one
    ``dsts/state/schema.sql`` -- so the DDL has a single source of truth.
    """
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    ddl = {}
    for stmt in (s.strip() for s in text.split(";") if s.strip()):
        m = re.search(r"CREATE TABLE\s+(\w+)", stmt)
        if m:
            ddl[m.group(1)] = stmt.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1) + ";"
    return ddl


class SplitSqliteStore:
    """Duck-types ``dsts.state.store.StateStore``, backed by two SQLite files
    instead of one -- ``registered.db`` holds ``registered_state``,
    ``visitor.db`` holds ``visitor_state``. Routed the same way
    ``dsts.state.store.SqliteStore`` routes: by ``registry.is_registered()``.
    """

    def __init__(self, state_dir, registry):
        self._registry = registry
        state_dir = Path(state_dir)
        state_dir.mkdir(parents=True, exist_ok=True)
        ddl = _table_ddl()
        self._conns = {}
        for table, fname in _FILE_FOR_TABLE.items():
            conn = sqlite3.connect(state_dir / fname)
            conn.row_factory = sqlite3.Row
            with conn:
                conn.execute(ddl[table])
            self._conns[table] = conn

    def _table_for(self, occupant):
        return "registered_state" if self._registry.is_registered(occupant) else "visitor_state"

    def write_state(self, time, occupant, zone, probability):
        table = self._table_for(occupant)
        with self._conns[table] as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO {table} (time, occupant, zone, probability) "
                f"VALUES (?, ?, ?, ?)",
                (time, occupant, zone, probability),
            )

    def read_state(self, time, occupant):
        table = self._table_for(occupant)
        cur = self._conns[table].execute(
            f"SELECT time, occupant, zone, probability FROM {table} "
            f"WHERE time = ? AND occupant = ? ORDER BY zone",
            (time, occupant),
        )
        return [StateRow(r["time"], r["occupant"], r["zone"], r["probability"]) for r in cur.fetchall()]

    def list_state(self, category):
        table = {"registered": "registered_state", "visitor": "visitor_state"}.get(category)
        if table is None:
            raise ValueError(f"Unknown storage category: {category}")
        cur = self._conns[table].execute(
            f"SELECT time, occupant, zone, probability FROM {table} ORDER BY time, occupant, zone"
        )
        return [StateRow(r["time"], r["occupant"], r["zone"], r["probability"]) for r in cur.fetchall()]

    def read_all_state(self):
        return self.list_state("registered") + self.list_state("visitor")

    def close(self):
        for conn in self._conns.values():
            conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# --------------------------------------------------------------------------
# the deployed building
# --------------------------------------------------------------------------

@dataclass
class DeployedBuilding:
    """One ``nodes/building_N/`` folder, loaded and ready to send/receive."""

    building_id: str
    routing: RoutingNode                # own_ids, own_refs (n_occ,R,dim), pool
    filters: dict                       # {building_id: BloomFilter} -- the other 9
    store: SplitSqliteStore
    bsts: StateTable
    contract: RoutingContract           # built from THIS folder's own config.json
    folder: Path

    @classmethod
    def load(cls, folder):
        folder = Path(folder)
        contract = load_node_contract(folder)

        manifest_path = folder / "building.json"
        if not manifest_path.exists():
            raise ContractMismatch(f"missing {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        building_id = manifest["building_id"]
        if manifest["params_hash"] != contract.params.params_hash:
            raise ContractMismatch(
                f"{building_id}: building.json params_hash does not match config.json"
            )

        emb_dir = folder / "embeddings"
        ids_path, refs_path = emb_dir / "occupant_ids.json", emb_dir / "refs.npy"
        if not refs_path.exists():
            raise FileNotFoundError(
                f"{refs_path} not found -- run: "
                f"python generate_nodes.py --building-id {building_id}"
            )
        ids = json.loads(ids_path.read_text(encoding="utf-8"))
        refs = np.load(refs_path)
        routing = RoutingNode(building_id=building_id, own_ids=np.array(ids),
                              own_refs=refs, pool=VisitorPool())

        filters = {}
        for p in sorted((folder / "filters").glob("*.npz")):
            filters[p.stem] = load_building(p).bloom
        if building_id in filters:
            raise ContractMismatch(f"{building_id} holds its own filter -- should hold only the other 9")
        if len(filters) != 9:
            raise ContractMismatch(f"{building_id}: expected 9 filters, found {len(filters)}")

        registry = FakeOccupantRegistry(set(str(i) for i in ids))
        store = SplitSqliteStore(folder / "state", registry)
        bsts = StateTable(list(ZONES))

        return cls(building_id=building_id, routing=routing, filters=filters,
                   store=store, bsts=bsts, contract=contract, folder=folder)

    # -- SEND: capture a face here, resolve locally or query the other buildings --
    def send(self, capture_emb, peers, time=None, zone="z1"):
        """``capture_emb`` is a raw (unpreprocessed) embedding. ``peers`` is
        ``{building_id: DeployedBuilding}`` for (at least) the buildings in
        this folder's ``filters`` -- their ``receive()`` answers the handoff.

        Runs the existing 6-step cascade unchanged (``query_node.identify``):
        own occupants -> visitor pool -> route -> handoff -> aggregate ->
        abstain. On anything but abstain, records the result into *this*
        building's own state DB before returning.
        """
        q = preprocess(np.asarray(capture_emb, dtype=np.float32)[None], self.contract.mean_face)[0]
        nodes = {bid: p.routing for bid, p in peers.items()}
        ident = query_node.identify(q, self.routing, self.filters, nodes, self.contract)
        if ident.occupant_id:
            self._record(ident, time=time, zone=zone)
        return ident

    # -- RECEIVE: another building is asking "is this one of yours?" --
    def receive(self, capture_q, from_building):
        return respond_node.respond(capture_q, from_building, self.routing, self.contract)

    def _record(self, ident, time=None, zone="z1"):
        """Land ``ident`` in this building's own state: ``registered_state``
        for one of its own occupants (``ident.source == "local"``),
        ``visitor_state`` for a routed or pooled visitor.
        """
        t = time or datetime.now().strftime("%H:%M")
        fraction = ident.votes / self.contract.params.refs_per_occupant
        self.bsts.apply(t, zone, {ident.occupant_id: fraction}, self.store)

    def close(self):
        self.store.close()


def list_building_dirs(nodes_dir):
    """Every ``building_N`` folder under ``nodes_dir``, in building order."""
    names = [p.name for p in Path(nodes_dir).iterdir() if p.is_dir()]

    def key(n):
        tail = n.rsplit("_", 1)[-1]
        return (0, int(tail)) if tail.isdigit() else (1, n)

    return sorted(names, key=key)


if __name__ == "__main__":
    import pandas as pd

    nodes_dir = _BUILDINGS / "nodes"
    ids = list_building_dirs(nodes_dir)
    if len(ids) < 2:
        raise SystemExit(
            f"need at least 2 generated folders under {nodes_dir} -- "
            f"run: python generate_nodes.py --all"
        )

    deployed = {bid: DeployedBuilding.load(nodes_dir / bid) for bid in ids}
    at_id = ids[0]
    other_id = next(b for b in ids if b != at_id)
    at, other = deployed[at_id], deployed[other_id]

    meta_other = pd.read_csv(other.folder / "embeddings" / "meta.csv")
    test_rows = meta_other.index[meta_other["split"] == "test"].tolist()
    row = test_rows[0]
    true_occ = str(meta_other.loc[row, "occupant_id"]).zfill(7)
    capture = np.load(other.folder / "embeddings" / "emb_raw.npy")[row]

    print(f"sending {other_id}'s occupant {true_occ} (row {row}) to {at_id} ...")
    ident = at.send(capture, peers=deployed, zone="z2")
    ident.correct = (ident.occupant_id == true_occ) if ident.occupant_id else None
    print("  " + ident.line())

    if ident.occupant_id:
        category = "registered" if ident.source == "local" else "visitor"
        rows = [r for r in at.store.list_state(category) if r.occupant == ident.occupant_id]
        print(f"  {at_id}/state/{category}.db rows for {ident.occupant_id}:")
        for r in rows:
            print(f"    {r.time} {r.zone} p={r.probability:.4f}")
        assert rows, f"expected a {category}_state row after send(), found none"
        other_cat = "visitor" if category == "registered" else "registered"
        assert not [r for r in at.store.list_state(other_cat) if r.occupant == ident.occupant_id], \
            f"identification leaked into {other_cat}_state as well"

    for d in deployed.values():
        d.close()
    print("OK")

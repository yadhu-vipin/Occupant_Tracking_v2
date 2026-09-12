"""The routing runtime: a building as a node, and the visitor pool.

All 10 buildings run locally as function calls -- there is no network yet -- but
the split is drawn along the line it would later be cut on:

    query side  (query_node.py)   : capture -> local vote -> pool vote -> route -> handoff
    receive side (respond_node.py): capture -> local vote -> reply {+ 20 refs on a match}

What crosses the (future) boundary:
    out  : the captured embedding + which building is asking
    back : {matched, occupant_id, votes, home_building, refs (R, dim)}

The returned R reference vectors are the home building's enrolled data. Holding
them in another building is a real departure from the project's "no building ever
holds another building's raw biometric data" premise -- see PIPELINE.md. It is
mitigated by transience: a pool entry is evicted the moment the visitor leaves.
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ._vendored.lsh import random_hyperplanes
from .params import ContractMismatch, Params, load_params
from .refs import reference_tensor
from .verify import vote

SHARED_DIR = Path(__file__).resolve().parent.parent / "shared"
ROUTING_SCHEMA_VERSION = 1


# --------------------------------------------------------------------------
# contract
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class RoutingContract:
    """Everything a node needs to route and verify, loaded and cross-checked once."""

    params: Params                     # seed, k, L, embed_dim, mean_face, space
    accept_angle_deg: float
    min_votes: int
    shortlist_k: int
    n_probes: int
    hyperplanes: np.ndarray            # (k*L, embed_dim) -- same planes as enrollment

    @property
    def mean_face(self):
        return self.params.mean_face.astype(np.float32)

    @property
    def space(self):
        return self.params.space

    def summary(self):
        return (f"accept={self.accept_angle_deg} min_votes={self.min_votes} "
                f"shortlist_k={self.shortlist_k} n_probes={self.n_probes} "
                f"| enrollment {self.params.params_hash[:12]}...")


def load_routing_contract(shared_dir=None):
    """Read ``shared/routing_params.json`` + the enrollment contract, verify the pairing."""
    shared = Path(shared_dir) if shared_dir else SHARED_DIR
    rp_path = shared / "routing_params.json"
    if not rp_path.exists():
        raise ContractMismatch(f"missing {rp_path}")

    rp = json.loads(rp_path.read_text(encoding="utf-8"))
    if rp.get("schema_version") != ROUTING_SCHEMA_VERSION:
        raise ContractMismatch(
            f"routing_params.json schema_version={rp.get('schema_version')}, "
            f"this package speaks {ROUTING_SCHEMA_VERSION}"
        )

    params = load_params(shared)
    if rp["buildings_params_hash"] != params.params_hash:
        raise ContractMismatch(
            "routing_params.json is pinned to a different enrollment contract:\n"
            f"    routing_params buildings_params_hash {rp['buildings_params_hash']}\n"
            f"    live shared/params.json params_hash   {params.params_hash}\n"
            "The published filters and these thresholds are out of sync."
        )

    planes = random_hyperplanes(params.embed_dim, params.k, params.L, seed=params.seed)
    return RoutingContract(
        params=params,
        accept_angle_deg=float(rp["accept_angle_deg"]),
        min_votes=int(rp["min_votes"]),
        shortlist_k=int(rp["shortlist_k"]),
        n_probes=int(rp["n_probes"]),
        hyperplanes=planes,
    )


# --------------------------------------------------------------------------
# visitor pool
# --------------------------------------------------------------------------

@dataclass
class Visitor:
    """A person identified on an earlier capture and still in the building."""

    occupant_id: str
    home_building: str
    refs: np.ndarray                   # (R, dim) preprocessed -- borrowed from home
    vote_fraction: float               # e.g. 15/20 at admission
    entered_at: float = field(default_factory=time.time)

    def key(self):
        return (self.occupant_id, self.home_building)


class VisitorPool:
    """Transient store of identified visitors currently present in a building."""

    def __init__(self):
        self._visitors: list[Visitor] = []

    def __len__(self):
        return len(self._visitors)

    def __iter__(self):
        return iter(self._visitors)

    def admit(self, visitor):
        """Add a visitor (or refresh an existing one's timestamp / refs)."""
        for i, v in enumerate(self._visitors):
            if v.key() == visitor.key():
                self._visitors[i] = visitor
                return
        self._visitors.append(visitor)

    def match(self, query, contract):
        """``(Visitor, votes)`` for the best-matching pooled visitor, or ``None``.

        Votes the capture against every pooled visitor's R refs at once, same
        12/20 rule as own-occupant verification.
        """
        if not self._visitors:
            return None
        refs = np.stack([v.refs for v in self._visitors])          # (P, R, dim)
        w, votes, accepted = vote(refs, query, contract.accept_angle_deg, contract.min_votes)
        return (self._visitors[w], votes) if accepted else None

    def depart(self, occupant_id, home_building):
        """Evict a visitor. Returns True if one was removed."""
        before = len(self._visitors)
        self._visitors = [v for v in self._visitors
                          if v.key() != (occupant_id, home_building)]
        return len(self._visitors) < before

    def clear(self):
        self._visitors.clear()


# --------------------------------------------------------------------------
# building node
# --------------------------------------------------------------------------

@dataclass
class BuildingNode:
    """One building: its own occupants' refs + whoever is visiting right now."""

    building_id: str
    own_ids: np.ndarray                # (n_occ,)
    own_refs: np.ndarray               # (n_occ, R, dim) preprocessed
    pool: VisitorPool = field(default_factory=VisitorPool)

    @property
    def n_occupants(self):
        return len(self.own_ids)

    @property
    def security_handler(self):
        """Lazy-loaded zero-trust security handler bound to this building node."""
        if not hasattr(self, '_security_handler') or self._security_handler is None:
            try:
                from nodelib.security_handler import SecureBuildingNodeHandler
                self._security_handler = SecureBuildingNodeHandler()
                self._security_handler.register_node(self.building_id)
            except Exception:
                self._security_handler = None
        return self._security_handler

    @classmethod
    def from_corpus(cls, embeddings, meta, building_id, contract):
        ids, refs = reference_tensor(embeddings, meta, building_id,
                                     contract.mean_face, contract.params.refs_per_occupant)
        return cls(building_id=building_id, own_ids=ids, own_refs=refs)


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------

@dataclass
class Reply:
    """A shortlisted building's answer to a handoff."""

    matched: bool
    building_id: str
    occupant_id: str | None = None
    votes: int = 0
    home_building: str | None = None
    refs: np.ndarray | None = None      # (R, dim) -- only on a match
    candidate_distances: dict | None = None
    # {occupant_id: minimum reference angle in degrees} for every one of this
    # node's own occupants -- the same per-occupant angular evidence already
    # computed (and voted on) inside respond(), just not previously returned.
    # Populated only on a match; see buildings/dsts/state/probability.py.


@dataclass
class Identification:
    """The querying building's final answer, plus the trace of how it got there."""

    source: str                        # "local" | "pool" | "routed" | "abstain"
    occupant_id: str | None
    home_building: str | None
    votes: int
    at_building: str
    shortlist: list = field(default_factory=list)
    replies: list = field(default_factory=list)
    local_votes: int = 0
    pool_votes: int = 0
    filter_scores: dict = field(default_factory=dict)
    correct: bool | None = None        # set by a caller that knows ground truth

    def line(self):
        who = "unknown" if self.occupant_id is None else f"occupant {self.occupant_id}"
        home = "" if self.home_building is None else f" (home {self.home_building})"
        tag = "" if self.correct is None else f"  [{'CORRECT' if self.correct else 'WRONG'}]"
        return f"{self.source.upper()}: {who}{home}, {self.votes} votes{tag}"


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))   # lane_a/
    import pandas as pd
    from core import config as cfg                                            # noqa: E402
    from ._vendored.lsh import preprocess

    c = load_routing_contract()
    print(f"routing contract OK: {c.summary()}")
    print(f"  hyperplanes {c.hyperplanes.shape}, mean_face {c.mean_face.shape}")

    raw = np.load(cfg.EMB_FILE)
    meta = pd.read_csv(cfg.META_FILE)
    node = BuildingNode.from_corpus(raw, meta, "building_3", c)
    print(f"  building_3 node: {node.n_occupants} occupants, refs {node.own_refs.shape}")

    # pool: admit occupant 0 of building_3 as a fake "visitor", match a 2nd capture
    test = meta[meta["split"] == "test"].copy()
    test["occ"] = test["occupant_id"].astype(str).str.zfill(7)
    occ0 = str(node.own_ids[0])
    caps = test[test["occ"] == occ0]
    v = Visitor(occupant_id=occ0, home_building="building_3",
                refs=node.own_refs[0], vote_fraction=1.0)
    node.pool.admit(v)
    assert len(node.pool) == 1

    q = preprocess(raw[caps.iloc[1].name][None], c.mean_face)[0]
    hit = node.pool.match(q, c)
    assert hit is not None and hit[0].occupant_id == occ0, "pool did not re-match the visitor"
    print(f"  pool re-matched the visitor with {hit[1]}/{c.params.refs_per_occupant} votes")

    assert node.pool.depart(occ0, "building_3")
    assert node.pool.match(q, c) is None, "pool still matched after depart()"
    print("  depart() evicted the visitor")
    print("OK")

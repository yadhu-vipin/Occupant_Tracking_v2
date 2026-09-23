"""A querying building: identify a captured face, routing to other buildings if needed.

    python query_node.py --capture-row 12345 [--at building_3] [--repeat 2]

The cascade (see PIPELINE.md):

    1. own occupants   -- vote the capture against this building's 50 occupants
    2. visitor pool    -- vote against visitors already identified and still here
    3. route           -- score the capture's LSH codes against the other 9 filters
    4. handoff         -- send the capture to the top-SHORTLIST_K buildings
    5. aggregate       -- take the highest-vote reply, admit that visitor to the pool
    6. abstain         -- nobody reached min_votes

Exit codes: 0 identified/abstained, 2 contract mismatch, 3 bad input.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from buildinglib._vendored.lsh import encode_with_margins, preprocess   # noqa: E402
from buildinglib.artifact import load_building                          # noqa: E402
from buildinglib.node import (BuildingNode, Identification, Visitor,     # noqa: E402
                              load_routing_contract)
from buildinglib.params import ContractMismatch                         # noqa: E402
from buildinglib.route import route                                     # noqa: E402
from buildinglib.split import default_emb_path, default_meta_path, list_buildings                            # noqa: E402
from buildinglib.verify import vote                                     # noqa: E402
from respond_node import respond                                        # noqa: E402

DEFAULT_EMB = default_emb_path()
DEFAULT_META = default_meta_path()
DEFAULT_OUT = HERE / "out"


def identify(capture_q, at_node, filters, nodes, contract):
    """Run the full cascade for one capture. ``capture_q`` is preprocessed (dim,).

    ``filters`` : {building_id: BloomFilter} for the buildings this node can route
                  to (its own excluded).
    ``nodes``   : {building_id: BuildingNode} -- the local stand-in for the network;
                  a real deployment replaces the handoff loop with RPC.
    """
    ident = Identification(source="abstain", occupant_id=None, home_building=None,
                           votes=0, at_building=at_node.building_id)

    # 1. own occupants
    w, v, ok = vote(at_node.own_refs, capture_q, contract.accept_angle_deg, contract.min_votes)
    ident.local_votes = v
    if ok:
        ident.source = "local"
        ident.occupant_id = str(at_node.own_ids[w])
        ident.home_building = at_node.building_id
        ident.votes = v
        return ident

    # 2. visitor pool
    hit = at_node.pool.match(capture_q, contract)
    if hit is not None:
        visitor, votes = hit
        ident.pool_votes = votes
        ident.source = "pool"
        ident.occupant_id = visitor.occupant_id
        ident.home_building = visitor.home_building
        ident.votes = votes
        return ident

    # 3. route
    codes, margins = encode_with_margins(capture_q[None], contract.hyperplanes, contract.params.k)
    reachable = [b for b in filters if b != at_node.building_id]
    shortlist, scores = route(codes[0], margins[0], filters, reachable,
                              contract.space, contract.n_probes, contract.shortlist_k)
    ident.shortlist = shortlist
    ident.filter_scores = scores

    # 4. handoff (with zero-trust envelope encryption & signature verification)
    replies = []
    for b in shortlist:
        if at_node.security_handler and hasattr(nodes[b], "security_handler") and nodes[b].security_handler:
            envelope = at_node.security_handler.seal_handoff_request(
                source_building=at_node.building_id,
                dest_building=b,
                visitor_id="UNRESOLVED_VISITOR",
            )
            ok, meta, reason = nodes[b].security_handler.unseal_handoff_request(b, envelope)
            if not ok:
                continue
        r = respond(capture_q, at_node.building_id, nodes[b], contract)
        replies.append(r)
    ident.replies = replies

    # 5. aggregate
    matched = [r for r in replies if r.matched]
    if matched:
        best = max(matched, key=lambda r: r.votes)
        at_node.pool.admit(Visitor(
            occupant_id=best.occupant_id, home_building=best.home_building,
            refs=best.refs, vote_fraction=best.votes / contract.params.refs_per_occupant,
        ))
        ident.source = "routed"
        ident.occupant_id = best.occupant_id
        ident.home_building = best.home_building
        ident.votes = best.votes
        return ident

    # 6. abstain
    return ident


def _pick_at(truth_building, all_buildings, explicit):
    if explicit:
        return explicit
    for b in all_buildings:                       # first building that isn't home
        if b != truth_building:
            return b
    return all_buildings[0]


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture-row", type=int, required=True,
                    help="row index into the corpus to use as the captured face")
    ap.add_argument("--at", dest="at_building", default=None,
                    help="building where the visitor is seen (default: not their home)")
    ap.add_argument("--repeat", type=int, default=1,
                    help="run the same capture N times at the same node (shows the pool)")
    ap.add_argument("--emb", type=Path, default=DEFAULT_EMB)
    ap.add_argument("--meta", type=Path, default=DEFAULT_META)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="dir of building_*.npz")
    ap.add_argument("--shared", type=Path, default=None)
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        contract = load_routing_contract(args.shared)
    except ContractMismatch as exc:
        print(f"[2] {exc}", file=sys.stderr)
        return 2

    for p in (args.emb, args.meta):
        if not p.exists():
            print(f"[3] missing input: {p}", file=sys.stderr)
            return 3

    raw = np.load(args.emb)
    meta = pd.read_csv(args.meta)
    if not (0 <= args.capture_row < len(raw)):
        print(f"[3] --capture-row {args.capture_row} out of range 0..{len(raw) - 1}",
              file=sys.stderr)
        return 3

    truth = meta.iloc[args.capture_row]
    true_occ = str(truth["occupant_id"]).zfill(7)
    true_home = truth["building"]

    buildings = list_buildings(meta)
    at = _pick_at(true_home, buildings, args.at_building)

    print(f"capture row {args.capture_row}: really occupant {true_occ} of {true_home} "
          f"({truth['split']})")
    print(f"seen at: {at}   contract: {contract.summary()}\n")

    filters = {b: load_building(args.out / f"{b}.npz").bloom for b in buildings}
    nodes = {b: BuildingNode.from_corpus(raw, meta, b, contract) for b in buildings}
    at_node = nodes[at]
    q = preprocess(raw[args.capture_row][None], contract.mean_face)[0]

    for i in range(1, args.repeat + 1):
        if args.repeat > 1:
            print(f"--- encounter {i} ---")
        pool_before = len(at_node.pool)
        ident = identify(q, at_node, filters, nodes, contract)
        ident.correct = (ident.occupant_id == true_occ) if ident.occupant_id else None

        print(f"[1] local vote ({at_node.n_occupants} own occupants): "
              f"best {ident.local_votes}/{contract.params.refs_per_occupant}"
              + ("  -> match" if ident.source == "local" else "  -> no match"))
        if ident.source == "local":
            print(_result(ident, true_occ, true_home)); continue

        print(f"[2] visitor pool ({pool_before} present): "
              + (f"best {ident.pool_votes}/{contract.params.refs_per_occupant}  -> match"
                 if ident.source == "pool"
                 else ("empty" if pool_before == 0 else
                       f"best {ident.pool_votes}/{contract.params.refs_per_occupant}  -> no match")))
        if ident.source == "pool":
            print(_result(ident, true_occ, true_home)); continue

        top = sorted(ident.filter_scores.items(), key=lambda kv: kv[1], reverse=True)[:6]
        print("[3] route: filter scores  " + "  ".join(f"{b} {s}/{contract.params.L}"
                                                       for b, s in top))
        print(f"    shortlist: {', '.join(ident.shortlist)}")
        print("[4] handoff:")
        for r in ident.replies:
            print(f"    {r.building_id:<12} "
                  + (f"MATCH occupant {r.occupant_id}, {r.votes}/"
                     f"{contract.params.refs_per_occupant}" if r.matched else "no match"))
        if ident.source == "routed":
            print(f"[5] winner: {ident.home_building}, occupant {ident.occupant_id}, "
                  f"{ident.votes}/{contract.params.refs_per_occupant}  "
                  f"-> admitted to {at}'s visitor pool")
        print(_result(ident, true_occ, true_home))

    return 0


def _result(ident, true_occ, true_home):
    if ident.source == "abstain":
        reachable = true_home in ident.shortlist
        return (f"\nRESULT: abstained. true building "
                f"{'was' if reachable else 'was NOT'} shortlisted.\n")
    verdict = "CORRECT" if ident.correct else "MISIDENTIFIED"
    return (f"\nRESULT: {ident.source}. identified as occupant {ident.occupant_id} "
            f"(home {ident.home_building}). {verdict}.\n")


if __name__ == "__main__":
    raise SystemExit(main())

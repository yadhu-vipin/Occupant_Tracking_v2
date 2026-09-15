"""A receiving building: given a captured embedding, does it belong to one of MY occupants?

    python respond_node.py --building-id building_9 --capture-row 12345

The querying building sends the capture + its own id. This node votes the
capture against its 50 occupants' reference vectors. If a winner reaches
min_votes it replies with the identity, the vote count, and -- the part that
leaves the building -- that occupant's 20 reference vectors.

Exit codes: 0 (replied, match or no match), 2 contract mismatch, 3 bad input.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from buildinglib._vendored.lsh import preprocess                     # noqa: E402
from buildinglib.node import BuildingNode, Reply, load_routing_contract  # noqa: E402
from buildinglib.params import ContractMismatch                       # noqa: E402
from buildinglib.split import default_emb_path, default_meta_path  # noqa: E402
from buildinglib.verify import vote_evidence                          # noqa: E402

DEFAULT_EMB = default_emb_path()
DEFAULT_META = default_meta_path()


def respond(capture_q, from_building, node, contract):
    """The receive-side function. ``capture_q`` is already preprocessed (dim,).

    Returns a :class:`Reply`. On a match, ``reply.refs`` is the matched
    occupant's ``(R, dim)`` reference tensor -- this is what crosses the boundary.

    Winner selection and acceptance are unchanged from ``buildinglib.verify.vote``
    (most votes, angle as tie-break, accept at ``min_votes``); this just also
    keeps the per-occupant angle evidence ``vote`` already computes internally
    and discarded, so a match can additionally report each of this node's own
    occupants' minimum reference angle -- the biometric distance evidence
    Definition 3.3 probability generation (``dsts/state/probability.py``) needs.
    """
    angles, votes = vote_evidence(node.own_refs, capture_q, contract.accept_angle_deg)
    w = int(np.lexsort((angles.min(axis=1), -votes))[0])
    accepted = bool(votes[w] >= contract.min_votes)
    if not accepted:
        return Reply(matched=False, building_id=node.building_id)
    candidate_distances = {
        str(occupant_id): float(occupant_angles.min())
        for occupant_id, occupant_angles in zip(node.own_ids, angles)
    }
    return Reply(
        matched=True,
        building_id=node.building_id,
        occupant_id=str(node.own_ids[w]),
        votes=int(votes[w]),
        home_building=node.building_id,
        refs=node.own_refs[w],
        candidate_distances=candidate_distances,
    )


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--building-id", required=True, help="which building this node is")
    ap.add_argument("--capture-row", type=int, required=True,
                    help="row index into the corpus to use as the captured face")
    ap.add_argument("--from", dest="from_building", default="building_?",
                    help="which building is asking (metadata only)")
    ap.add_argument("--emb", type=Path, default=DEFAULT_EMB)
    ap.add_argument("--meta", type=Path, default=DEFAULT_META)
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
    node = BuildingNode.from_corpus(raw, meta, args.building_id, contract)
    q = preprocess(raw[args.capture_row][None], contract.mean_face)[0]

    reply = respond(q, args.from_building, node, contract)

    print(f"capture row {args.capture_row}: really occupant "
          f"{str(truth['occupant_id']).zfill(7)} of {truth['building']} ({truth['split']})")
    print(f"asked of {node.building_id} ({node.n_occupants} occupants), from {args.from_building}\n")

    if reply.matched:
        print(f"REPLY: matched -> occupant {reply.occupant_id}, {reply.votes}/"
              f"{contract.params.refs_per_occupant} votes")
        print(f"       would send back {reply.refs.shape} reference vectors")
        right = reply.occupant_id == str(truth['occupant_id']).zfill(7)
        print(f"       {'correct' if right else 'WRONG occupant'}")
    else:
        print("REPLY: no match (no occupant reached min_votes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

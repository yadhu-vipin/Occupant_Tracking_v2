"""Two halves of one building's role in the federated system.

ENROLLMENT -- occupant embeddings -> one publishable Bloom filter
    params   : the federation contract (seed, k, L, mean face) + drift guards
    split    : carve the shared corpus down to one building, index-aligned
    enroll   : reference photos -> centroids -> codes -> items -> BloomFilter
    artifact : save/load the ~7 KB npz that gets pushed and merged

ROUTING -- a captured face -> an identification (local, cached, or routed)
    refs     : rebuild one building's per-occupant reference tensor from the corpus
    verify   : vote() -- does the capture match a set of occupants?
    route    : score the capture's LSH codes against the published filters
    node     : RoutingContract, BuildingNode, VisitorPool -- the query-time runtime

    _vendored/ : byte-for-byte copies of lane_a/core/{lsh,bloom}.py so this
                 package runs standalone. Never edit these -- edit lane_a/core/
                 and re-copy; params.check_vendored() enforces it.

The published artifact is still the Bloom filter only. Routing rebuilds the
reference vectors locally each run -- they are never written or published.
"""

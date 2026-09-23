"""
v6/dsts/ordering.py — HLC merge and total order (Eq 12 + extension)
=====================================================================
The 2020 paper's ordering assumes no inter-BSTS communication, so
synchronised physical clocks suffice. Distributing identification
creates genuine cross-BSTS causal edges, requiring an HLC.

Key test: test_hlc_reduces_to_physical_when_no_messages — with zero
inter-BSTS messages, l stays 0 and the key collapses to exactly the
2020 ordering.
"""

from .events import HLC


def hlc_send(local: HLC, node: str) -> HLC:
    """
    Update HLC on sending a message.
    l' = max(l, local.l) + 1, pt' = max(pt, wall_clock)
    """
    import time
    wall = time.time()
    pt = max(local.pt, wall)
    l = local.l + 1
    return HLC(pt=pt, l=l, node=node)


def hlc_receive(local: HLC, remote: HLC, node: str) -> HLC:
    """
    Update HLC on receiving a message.
    Merges local and remote clocks.
    """
    import time
    wall = time.time()
    pt = max(local.pt, remote.pt, wall)
    if pt == local.pt == remote.pt:
        l = max(local.l, remote.l) + 1
    elif pt == local.pt:
        l = local.l + 1
    elif pt == remote.pt:
        l = remote.l + 1
    else:
        l = 0
    return HLC(pt=pt, l=l, node=node)


def hlc_local_tick(local: HLC, node: str) -> HLC:
    """
    Update HLC on a local event (no message).
    When there are no messages, l stays 0 and ordering
    reduces to the 2020 paper's physical-clock ordering.
    """
    import time
    wall = time.time()
    if wall > local.pt:
        return HLC(pt=wall, l=0, node=node)
    else:
        return HLC(pt=local.pt, l=local.l + 1, node=node)

"""
v6/node/building_node.py — Unified building node
====================================================
There is exactly ONE building_node.py, parameterised by --building.
v5's copy-paste-per-building is what let B0 (606 L) silently diverge
from B1–B4 (416 L, no θ gate).

This node integrates:
  - BSTS (Layer 1) for state management
  - Gallery + Router (Layer 2) for distributed identification
  - Framing + TLS (Layer 3) for secure transport
  - Authorize seam (Layer 4) for future RBAC
"""

import json
import logging
import queue
import threading
import time
from typing import Dict, Optional, List

from ..dsts.bsts import BSTS
from ..dsts.events import HLC
from ..dsts.ordering import hlc_local_tick, hlc_receive
from ..identify.router import IdentificationRouter
from ..identify.simhash import SimHasher
from ..security.authorize import authorize, audit

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s %(message)s'
)


class BuildingNode:
    """
    Unified building node for the distributed DSTS.

    One binary, parameterised by --building. All buildings run
    identical code — no per-building divergence possible.
    """

    def __init__(
        self,
        building_id: str,
        occupant_ids: List[str],
        profile: Dict,
        hasher: SimHasher,
    ):
        self.building_id = building_id
        self.logger = logging.getLogger(f"Node-{building_id}")

        # Layer 1 — DSTS formalism
        self.bsts = BSTS(building_id, occupant_ids)

        # Layer 2 — identification
        self.hasher = hasher
        self.router = IdentificationRouter(building_id, hasher)

        # Layer 3 — network config from deployment profile
        self.profile = profile
        my_info = profile["nodes"][building_id]
        self.host = my_info["host"]
        self.port = my_info["port"]
        self.peers = {
            bid: info for bid, info in profile["nodes"].items()
            if bid != building_id
        }

        # HLC
        self.hlc = HLC(pt=time.time(), l=0, node=building_id)

        # State persistence — single writer thread (fixes v5's corruption bug)
        self._write_queue: queue.Queue = queue.Queue()
        self._running = False

        # Replay protection
        self._nonce_set: set = set()
        self._nonce_window = 120.0  # seconds

        # Online peers
        self.online_peers: Dict[str, float] = {}

        self.logger.info(
            f"Initialised: {len(occupant_ids)} occupants, "
            f"{len(self.peers)} peers, {self.host}:{self.port}"
        )

    def process_detection(
        self,
        occupant_id: str,
        zone: str,
        probability: float,
        sim_time: float,
    ) -> dict:
        """
        Process a face detection event.

        Applies BSTS state transition and records the event.
        Authorization chokepoint called before state update.
        """
        # RBAC seam
        authorize(
            principal=None,
            verb="DETECT",
            target=f"state:{self.building_id}:{occupant_id}",
            building_id=self.building_id,
        )

        # Update HLC
        self.hlc = hlc_local_tick(self.hlc, self.building_id)

        # Apply BSTS transition
        event = self.bsts.process_event(
            occupant_id=occupant_id,
            zone=zone,
            probability=probability,
            sim_time=sim_time,
            hlc=self.hlc,
        )

        self.logger.info(
            f"Detection: {occupant_id} at {zone} (p={probability:.3f}, "
            f"state={self.bsts.event_counter})"
        )

        return {
            "status": "ok",
            "event_index": event.event_index,
            "building": self.building_id,
        }

    def handle_identify_seek(self, sketch: List[int]) -> dict:
        """
        Handle Phase A — SEEK.
        No biometric data received — only 96 bytes of hash keys.
        Check local Bloom summary and respond with hit count.
        """
        authorize(
            principal=None,
            verb="SEEK",
            target=f"summary:{self.building_id}",
            building_id=self.building_id,
        )

        # Check sketch against our local Bloom summary
        if hasattr(self, '_local_bloom') and self._local_bloom is not None:
            hits = self._local_bloom.count_hits(sketch)
            return {"maybe": hits > 0, "hits": hits}
        return {"maybe": True, "hits": 0}  # conservative: say maybe

    def handle_identify_resolve(
        self, probe_blob: bytes, dim: int = 512
    ) -> dict:
        """
        Handle Phase B — RESOLVE.
        Receives a 1 KB float16 probe, matches against local gallery.
        """
        import numpy as np
        from ..net.framing import blob_to_embed

        authorize(
            principal=None,
            verb="RESOLVE",
            target=f"gallery:{self.building_id}",
            building_id=self.building_id,
        )

        probe = blob_to_embed(probe_blob, dim).flatten()

        if hasattr(self, '_gallery') and self._gallery is not None:
            result = self._gallery.recognize(probe)
            if result:
                oid, dist, prob = result
                return {
                    "identity": oid,
                    "home": self.building_id,
                    "distance": round(dist, 4),
                    "prob": round(prob, 4),
                }
        return {"identity": None}

    def get_state_snapshot(self) -> dict:
        """Return current state table as a serialisable dict."""
        return {
            "building": self.building_id,
            "event_count": self.bsts.event_counter,
            "state": self.bsts.get_state_snapshot(),
        }

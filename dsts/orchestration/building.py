"""
dsts/orchestration/building.py — Person D, Lane D (Wiring & Interface).

One Building object per node. This file owns no recognition math, no
routing math, and no persistence logic — it only wires Lane A's gallery +
ranker, Lane C's store, and this lane's transport together, exactly as the
implementation plan specifies: "no handoff in the middle" for A's pipeline,
and no business logic here that belongs to another lane.

Reference: Menon, Jayaraman & Govindaraju (2011), "The Three R's of
Cyberphysical Spaces" — Recognition and Reasoning are what this class calls
into (via A's and C's components); Retrieval is served one layer up, in
api/server.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from dsts.contracts import BuildingRank, Event, Recognition, Transport

logger = logging.getLogger("dsts.building")


@dataclass
class Building:
    building_id: str
    occupant_ids: list[str]
    store: object  # Lane C's SqliteStore, or testing.InMemoryStore
    recogniser: object  # Lane A's Recogniser, or testing.FakeRecogniser
    ranker: object  # Lane A's Ranker, or testing.FakeRanker
    transport: Transport
    recognition_threshold: float = 0.4
    on_event: callable | None = field(default=None, repr=False)  # dashboard hook

    def handle_event(self, event: Event) -> Recognition:
        """My camera saw a face. Identify it locally, then push the
        resulting probability distribution into the state layer."""
        recognition = self.recogniser.recognise(event.embedding, self.occupant_ids)

        if recognition.accepted:
            for candidate in recognition.candidates:
                self.store.write_state(event.time, candidate.occupant_id, event.zone, candidate.probability)
        else:
            # No local match — this is the B5-sees-a-stranger case. Ask the
            # network who this occupant's home building is.
            self._route_and_confirm(event)

        if self.on_event:
            self.on_event(self.building_id, event, recognition)
        return recognition

    def identify_probe(self, embedding: list) -> Recognition:
        """Another building is asking: is this one of yours? Pure local
        recognition, no state writes — the asking building owns what
        happens with the answer."""
        return self.recogniser.recognise(embedding, self.occupant_ids)

    def _route_and_confirm(self, event: Event) -> None:
        ranking: list[BuildingRank] = self.ranker.rank(event.embedding, home_building=None)
        if not ranking or ranking[0].hits == 0:
            logger.info("building=%s no routing candidate for stranger at zone=%s", self.building_id, event.zone)
            return

        home = ranking[0].building_id
        if home == self.building_id:
            return  # ranked itself first: nothing to hand off

        reply = self.transport.send(
            home,
            "identify_probe",
            {"embedding": event.embedding, "requesting_building": self.building_id},
        )
        if reply.get("accepted"):
            occupant_id = reply["best_id"]
            self.store.write_state(event.time, occupant_id, event.zone, reply.get("confidence", 0.5))
            logger.info(
                "building=%s confirmed occupant=%s via home=%s -> visitor record created",
                self.building_id, occupant_id, home,
            )

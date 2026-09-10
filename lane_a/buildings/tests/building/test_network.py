"""Tests for independent building-level BSTS composition and routing."""

import math

from dsts.building.network import BuildingNetwork
from dsts.building.node import BuildingNode


class Event:
    """Small test event compatible with BuildingNetwork routing."""

    def __init__(self, time: str, building_id: str, zone: str, probs: dict[str, float]):
        self.time = time
        self.building_id = building_id
        self.detected_zone = zone
        self.event_probs = probs


def make_network() -> tuple[BuildingNetwork, BuildingNode, BuildingNode, BuildingNode]:
    network = BuildingNetwork()
    b001 = BuildingNode("B001", {"O001", "O002"})
    b002 = BuildingNode("B002", {"O003", "O004"})
    b003 = BuildingNode("B003", {"O005", "O006"})
    for building in (b001, b002, b003):
        network.add_building(building)
    return network, b001, b002, b003


def test_buildings_have_independent_bsts_stores_and_registries() -> None:
    network, b001, b002, b003 = make_network()

    assert network.get_building("B001") is b001
    assert b001.bsts is not b002.bsts
    assert b001.store is not b002.store
    assert b001.registry is not b002.registry
    assert b001.registry.is_registered("O001")
    assert not b002.registry.is_registered("O001")
    assert b003.registry.is_registered("O006")


def test_routing_updates_only_the_target_building() -> None:
    network, b001, b002, b003 = make_network()

    network.route_event(Event("10:00", "B001", "z3", {"O001": 0.8}))

    assert len(b001.store._registered_state) == len(b001.zones)
    assert b002.store._registered_state == []
    assert b003.store._registered_state == []

    network.route_event(Event("10:10", "B002", "z6", {"O003": 0.9}))

    assert len(b002.store._registered_state) == len(b002.zones)
    assert b003.store._registered_state == []
    assert math.isclose(sum(b002.bsts.get_state("O003").values()), 1.0)


def test_remote_occupant_is_stored_as_a_visitor_in_destination() -> None:
    network, b001, b002, _ = make_network()

    network.route_event(Event("10:00", "B001", "z3", {"O001": 0.8}))
    network.route_event(Event("10:20", "B002", "z3", {"O001": 0.7}))
    network.route_event(Event("10:25", "B002", "z4", {"O001": 0.6}))

    assert b001.store._registered_state
    assert b001.store._visitor_state == []
    assert b002.store._registered_state == []
    assert len(b002.store._visitor_state) == len(b002.zones) * 2
    assert math.isclose(sum(b002.bsts.get_state("O001").values()), 1.0)

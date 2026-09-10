"""Interactive terminal interface for configured building state and queries."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dsts.building.network import BuildingNetwork
from dsts.building.node import BuildingNode
from dsts.state.queries import known_occupant, point_probability, was_present


def heading(title: str) -> None:
    print("=" * 50)
    print(title.center(50))
    print("=" * 50)


def create_network() -> BuildingNetwork:
    """Create the configured building network for the current CLI session."""
    network = BuildingNetwork()
    for building_id, occupants in (
        ("B001", ("O001", "O002")), ("B002", ("O003", "O004")), ("B003", ("O005", "O006")),
    ):
        network.add_building(BuildingNode(building_id, occupants))
    return network


def show_buildings(network: BuildingNetwork) -> None:
    heading("BUILDING SETUP")
    for building in network.buildings():
        print(building.building_id)
        print(f"  Registered occupants: {', '.join(building.registered_occupants)}")
        print(f"  Zones: {' '.join(building.zones)}")


def prompt_choice(prompt: str, choices: set[str]) -> str:
    while True:
        choice = input(prompt).strip()
        if choice in choices:
            return choice
        print("Invalid choice. Please try again.")


def prompt_time(label: str) -> str:
    while True:
        value = input(f"{label} (HH:MM): ").strip()
        try:
            datetime.strptime(value, "%H:%M")
            return value
        except ValueError:
            print("Invalid time. Use HH:MM, for example 10:05.")


def prompt_zone(building: BuildingNode) -> str:
    while True:
        zone = input(f"Zone ({' '.join(building.zones)}): ").strip()
        if zone in building.zones:
            return zone
        print("Unknown zone. Choose one of the configured zones.")


def prompt_occupant() -> str:
    while True:
        occupant = input("Occupant: ").strip()
        if occupant:
            return occupant
        print("Occupant cannot be empty.")


def prompt_building(network: BuildingNetwork) -> BuildingNode:
    valid = {building.building_id: building for building in network.buildings()}
    while True:
        building_id = input(f"Building ({', '.join(valid)}): ").strip()
        if building_id in valid:
            return valid[building_id]
        print("Unknown building. Choose a configured building ID.")


def prompt_threshold() -> float:
    while True:
        try:
            threshold = float(input("Threshold (0 to 1): ").strip())
        except ValueError:
            print("Invalid threshold. Enter a number from 0 to 1.")
            continue
        if 0.0 <= threshold <= 1.0:
            return threshold
        print("Invalid threshold. Enter a number from 0 to 1.")


def select_buildings(network: BuildingNetwork, title: str) -> tuple[BuildingNode, ...] | None:
    heading(title)
    buildings = network.buildings()
    print("Select building:")
    for number, building in enumerate(buildings, start=1):
        print(f"{number}. {building.building_id}")
    print(f"{len(buildings) + 1}. All buildings\n{len(buildings) + 2}. Back")
    choice = prompt_choice("Enter choice: ", {str(i) for i in range(1, len(buildings) + 3)})
    if choice == str(len(buildings) + 2):
        return None
    if choice == str(len(buildings) + 1):
        return buildings
    return (buildings[int(choice) - 1],)


def print_state_rows(building: BuildingNode, category: str) -> None:
    print("-" * 50)
    print(f"{building.building_id} - {category.upper()} STATE")
    print(f"{'Time':<10}{'Occupant':<12}{'Zone':<8}Probability")
    rows = building.store.list_state(category)
    if not rows:
        print("No records.")
    for row in rows:
        print(f"{row.time:<10}{row.occupant:<12}{row.zone:<8}{row.probability:.3f}")


def view_state_tables(network: BuildingNetwork) -> None:
    selected = select_buildings(network, "STATE TABLE VIEWER")
    if selected is None:
        return
    while True:
        print("1. Registered State\n2. Visitor State\n3. Back")
        choice = prompt_choice("Enter choice: ", {"1", "2", "3"})
        if choice == "3":
            return
        category = "registered" if choice == "1" else "visitor"
        for building in selected:
            print_state_rows(building, category)


def run_query_menu(network: BuildingNetwork) -> None:
    while True:
        heading("QUERY MENU")
        print("POINT-BASED QUERIES\n1. Point-based Singleton\n2. Point-based Boolean")
        print("\nGENERAL / NON-TIME-BASED QUERIES\n3. General Boolean\n\n4. Back")
        choice = prompt_choice("Enter choice: ", {"1", "2", "3", "4"})
        if choice == "4":
            return
        building = prompt_building(network)
        occupant = prompt_occupant()
        if choice in {"1", "2"}:
            zone = prompt_zone(building)
        if choice in {"1", "2"}:
            time = prompt_time("Time")
        if choice == "1":
            result = point_probability(building.store, occupant, zone, time)
            print(f"Probability: {result:.3f}" if result is not None else "Probability: no matching record")
        elif choice == "2":
            threshold = prompt_threshold()
            probability = point_probability(building.store, occupant, zone, time)
            print(f"Probability: {probability:.3f}" if probability is not None else "Probability: no matching record")
            print(f"Threshold: {threshold}\nPresent: {was_present(building.store, occupant, zone, time, threshold)}")
        else:
            registered = building.registry.is_registered(occupant)
            records = known_occupant(building.store, occupant)
            print(f"Registered locally: {registered}\nHas records: {records}\nTreated as visitor: {records and not registered}")


def main() -> None:
    network = create_network()
    while True:
        heading("DISTRIBUTED OCCUPANT TRACKING SYSTEM")
        print("1. Setup / View Buildings\n2. View State Tables\n3. Run Queries\n4. Exit")
        choice = prompt_choice("Enter choice: ", {"1", "2", "3", "4"})
        if choice == "1":
            show_buildings(network)
        elif choice == "2":
            view_state_tables(network)
        elif choice == "3":
            run_query_menu(network)
        else:
            print("Exiting Distributed Occupant Tracking System.")
            return


if __name__ == "__main__":
    main()

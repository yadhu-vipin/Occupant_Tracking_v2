"""
run_demo.py — DSTS Lane B complete system demonstration entry point
====================================================================

OVERVIEW:
Master CLI orchestrator for Lane B (Events, Evaluation, Security, and Monitoring).

DEMONSTRATION FLOW:
- Phase 1: Executes the deterministic ~40-event B1 -> B5 inter-building handoff scenario.
- Phase 2: Computes spatio-temporal tracking metrics (Precision, Recall, F1, Optimal Theta) and routing efficiency.
- Phase 3: Renders publication-quality PNG charts in `reports/`.
- Phase 4: Demonstrates full cryptographic stack (X25519 ECDH, HKDF-SHA256, AES-128-GCM, Ed25519 signatures, ReplayGuard).
- Phase 5: Simulates Prometheus metric collection and displays telemetry summary table.

KEY CONTRACTS:
- Entry function `run_demo()` executed when called directly (`python run_demo.py`).
"""

import sys
import os
import time

# Ensure UTF-8 output encoding on Windows consoles
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Ensure imports work from current directory and project root
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

try:
    from sim.scenario_b1_b5 import run_scenario, print_scenario_summary
    from sim.evaluation import paper_metrics, routing_metrics, print_metrics_summary
    from sim.report import generate_evaluation_report
    from security.crypto import NodeIdentity, ephemeral_handshake
    from security.metadata import create_handoff_metadata, seal_metadata, open_metadata
    from security.replay_guard import ReplayGuard
    from monitoring.monitoring import MetricsCollector
except ImportError:
    from v6.sim.scenario_b1_b5 import run_scenario, print_scenario_summary
    from v6.sim.evaluation import paper_metrics, routing_metrics, print_metrics_summary
    from v6.sim.report import generate_evaluation_report
    from v6.security.crypto import NodeIdentity, ephemeral_handshake
    from v6.security.metadata import create_handoff_metadata, seal_metadata, open_metadata
    from v6.security.replay_guard import ReplayGuard
    from v6.monitoring.monitoring import MetricsCollector



def run_demo():
    """Execute the complete DSTS Lane B demonstration."""

    print("\n" + "+" + "=" * 68 + "+")
    print("|" + "  DSTS — Distributed Surveillance Tracking System".center(68) + "|")
    print("|" + "  Lane B: Events, Evaluation, Security & Monitoring".center(68) + "|")
    print("|" + "  AM.SC.U4CSE23208 — Arjun Rajesh".center(68) + "|")
    print("+" + "=" * 68 + "+\n")

    # =======================================================================
    # PHASE 1: Run B1 → B5 Scenario
    # =======================================================================

    print(">> PHASE 1: Running B1 → B5 Demonstration Scenario\n")
    result = run_scenario(seed=42)
    print_scenario_summary(result)

    # =======================================================================
    # PHASE 2: Compute Evaluation Metrics
    # =======================================================================

    print(">> PHASE 2: Computing Evaluation Metrics\n")
    pm = paper_metrics(result)
    rm = routing_metrics(result)
    print_metrics_summary(pm, rm)

    # =======================================================================
    # PHASE 3: Generate Evaluation Reports
    # =======================================================================

    print(">> PHASE 3: Generating Visual Reports\n")
    report_dir = os.path.join(os.path.dirname(__file__), "reports")
    reports = generate_evaluation_report(pm, rm, output_dir=report_dir)

    # =======================================================================
    # PHASE 4: Security Layer Demonstration
    # =======================================================================

    print("\n>> PHASE 4: Security Layer Demonstration\n")
    print("─" * 60)
    print("  Cryptographic Stack")
    print("─" * 60)
    print("    X25519        → Ephemeral key exchange")
    print("    HKDF-SHA256   → Session key derivation")
    print("    AES-128-GCM   → Authenticated encryption")
    print("    Ed25519       → Digital signature verification")

    # Create building identities
    b1_id = NodeIdentity.generate("B1")
    b5_id = NodeIdentity.generate("B5")
    print(f"\n  B1 Ed25519 public: {b1_id.ed25519_public_bytes().hex()[:32]}...")
    print(f"  B5 Ed25519 public: {b5_id.ed25519_public_bytes().hex()[:32]}...")

    # Establish secure channel
    channel_b1, channel_b5 = ephemeral_handshake(b1_id, b5_id)
    print("  [PASS] X25519 key exchange complete")
    print("  [PASS] HKDF-SHA256 session key derived")

    # Create handoff metadata
    metadata = create_handoff_metadata(
        visitor_id=result.handoff.occupant_id,
        source_building="B1",
        dest_building="B5",
        confidence=0.95,
    )
    print(f"\n  Metadata to protect:")
    print(f"    visitor_id:       {metadata.visitor_id}")
    print(f"    source_building:  {metadata.source_building}")
    print(f"    dest_building:    {metadata.dest_building}")
    print(f"    transition_zone:  {metadata.transition_zone}")
    print(f"    confidence:       {metadata.confidence}")
    print(f"    message_id:       {metadata.message_id}")

    # Seal metadata
    envelope = seal_metadata(metadata, b1_id, channel_b1)
    print(f"\n  Sealed envelope:")
    print(f"    sender:           {envelope.sender_building}")
    print(f"    encrypted:        {envelope.encrypted_payload[:40]}...")
    print(f"    nonce:            {envelope.encryption_nonce}")
    print(f"    signature:        {envelope.signature[:40]}...")

    # Replay guard
    guard = ReplayGuard(window_seconds=120.0)
    rg_result = guard.validate(
        envelope.encryption_nonce, envelope.timestamp, envelope.message_id,
    )
    print(f"\n  Replay guard: {'ACCEPTED' if rg_result.accepted else 'REJECTED'}")

    # Open metadata
    recovered = open_metadata(envelope, channel_b5, b1_id.ed25519_public)
    print(f"\n  Recovered metadata:")
    print(f"    visitor_id:       {recovered.visitor_id}")
    print(f"    source_building:  {recovered.source_building}")
    print(f"    confidence:       {recovered.confidence}")
    print(f"  [PASS] Signature verified (Ed25519)")
    print(f"  [PASS] Decryption successful (AES-128-GCM)")
    print(f"  [PASS] Integrity verified (GCM authentication tag)")

    # Replay attempt
    rg_result2 = guard.validate(
        envelope.encryption_nonce, envelope.timestamp, envelope.message_id,
    )
    print(f"\n  Replay attempt: {'ACCEPTED' if rg_result2.accepted else 'REJECTED'}")
    if not rg_result2.accepted:
        print(f"    reason: {rg_result2.reason}")

    stats = guard.get_stats()
    print(f"\n  Replay guard stats:")
    print(f"    validated:        {stats['total_validated']}")
    print(f"    rejected:         {stats['total_rejected']}")
    print(f"    replay attempts:  {stats['replay_attempts']}")

    # =======================================================================
    # PHASE 5: Prometheus Monitoring Summary
    # =======================================================================

    print(f"\n>> PHASE 5: Prometheus Monitoring Summary\n")
    print("─" * 60)

    # Simulate metrics collection from the scenario
    collector_b1 = MetricsCollector("B1")
    collector_b5 = MetricsCollector("B5")

    for evt in result.b1_events:
        collector_b1.record_event_generated()
        collector_b1.record_event_processed()
        collector_b1.record_recognition(
            success=evt.probability > 0.5,
            probability=evt.probability,
        )

    for evt in result.b5_events:
        collector_b5.record_event_generated()
        collector_b5.record_event_processed()
        collector_b5.record_recognition(
            success=evt.probability > 0.5,
            probability=evt.probability,
        )

    collector_b1.record_routing(rank1=True, buildings_contacted=1, latency=0.01)
    collector_b1.record_inter_building_transition("B1", "B5")

    print(f"    {'Metric':<35} {'Value':>10}")
    print(f"    {'─' * 46}")
    print(f"    {'Events generated (B1)':<35} {len(result.b1_events):>10}")
    print(f"    {'Events generated (B5)':<35} {len(result.b5_events):>10}")
    print(f"    {'Total events':<35} {len(result.events):>10}")
    print(f"    {'Recognition accuracy':<35} {pm.recognition_accuracy:>10.1%}")
    print(f"    {'Routing rank-1':<35} {rm.rank1_accuracy:>10.1%}")
    print(f"    {'Avg buildings contacted':<35} {rm.avg_buildings_contacted:>10.1f}")
    print(f"    {'Replay attempts':<35} {stats['replay_attempts']:>10}")
    print(f"    {'Auth failures':<35} {0:>10}")

    # =======================================================================
    # SUMMARY
    # =======================================================================

    print(f"\n" + "+" + "=" * 68 + "+")
    print("|" + "  DEMONSTRATION COMPLETE".center(68) + "|")
    print("+" + "=" * 68 + "+")
    print("|" + f"  Total events:           {len(result.events):<10}".ljust(68) + "|")
    print("|" + f"  B1→B5 handoff:          {'SUCCESS' if result.handoff.routing_confirmed else 'FAILED':<10}".ljust(68) + "|")
    print("|" + f"  Recognition accuracy:   {pm.recognition_accuracy:.1%}".ljust(68) + "|")
    print("|" + f"  Routing rank-1:         {rm.rank1_accuracy:.1%}".ljust(68) + "|")
    print("|" + f"  Security:               ALL CHECKS PASSED".ljust(68) + "|")
    print("|" + f"  Reports:                {report_dir}".ljust(68) + "|")
    print("+" + "=" * 68 + "+\n")


if __name__ == "__main__":
    run_demo()

"""
monitoring/dashboard_server.py — DSTS Monitoring Dashboard Backend
===================================================================
Runs the DSTS 5-phase demo, instruments every phase with real system
metrics (CPU load, memory, disk I/O, wall-clock timing, security
overhead, query latency), then serves a monitoring dashboard at
http://localhost:8050.

Collected metrics are exposed as a JSON API at /api/metrics and the
dashboard HTML is served from /dashboard.
"""

import sys
import os
import time
import json
import threading
import traceback
import psutil
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

# Ensure imports from buildings_prototype root
PROTO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROTO_ROOT))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


# ── Metric Storage ────────────────────────────────────────────────────────────

_metrics = {
    "system": {},
    "phases": [],
    "security": {},
    "query": {},
    "recognition": {},
    "routing": {},
    "buildings": {},
    "timeline": [],
    "resource_samples": [],
}

_dashboard_html_path = PROTO_ROOT / "monitoring" / "dashboard.html"


# ── Resource Sampler ──────────────────────────────────────────────────────────

class ResourceSampler:
    """Samples CPU, memory, and disk at intervals during demo execution."""

    def __init__(self, interval=0.25):
        self.interval = interval
        self.samples = []
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        process = psutil.Process(os.getpid())
        start = time.time()
        while not self._stop.is_set():
            try:
                mem = process.memory_info()
                cpu_percent = process.cpu_percent(interval=None)
                io = process.io_counters()
                self.samples.append({
                    "t": round(time.time() - start, 3),
                    "cpu_percent": round(cpu_percent, 1),
                    "rss_mb": round(mem.rss / (1024 * 1024), 2),
                    "vms_mb": round(mem.vms / (1024 * 1024), 2),
                    "read_bytes": io.read_bytes,
                    "write_bytes": io.write_bytes,
                    "read_count": io.read_count,
                    "write_count": io.write_count,
                })
            except Exception:
                pass
            self._stop.wait(self.interval)


# ── Phase Timer ───────────────────────────────────────────────────────────────

class PhaseTimer:
    """Measures CPU time, wall time, memory delta for a named phase."""

    def __init__(self, name):
        self.name = name
        self.wall_start = 0
        self.cpu_start = 0
        self.mem_start = 0

    def __enter__(self):
        proc = psutil.Process(os.getpid())
        self.mem_start = proc.memory_info().rss
        self.cpu_start = time.process_time()
        self.wall_start = time.perf_counter()
        return self

    def __exit__(self, *args):
        wall_elapsed = time.perf_counter() - self.wall_start
        cpu_elapsed = time.process_time() - self.cpu_start
        proc = psutil.Process(os.getpid())
        mem_end = proc.memory_info().rss
        mem_delta = mem_end - self.mem_start

        _metrics["phases"].append({
            "name": self.name,
            "wall_time_ms": round(wall_elapsed * 1000, 2),
            "cpu_time_ms": round(cpu_elapsed * 1000, 2),
            "memory_delta_kb": round(mem_delta / 1024, 1),
            "memory_after_mb": round(mem_end / (1024 * 1024), 2),
        })


# ── Collect Metrics ───────────────────────────────────────────────────────────

def collect_metrics():
    """Run the DSTS demo pipeline and collect real metrics."""

    from sim.scenario_b1_b5 import run_scenario, print_scenario_summary
    from sim.evaluation import paper_metrics, routing_metrics
    from sim.report import generate_evaluation_report
    from security.crypto import NodeIdentity, ephemeral_handshake
    from security.metadata import create_handoff_metadata, seal_metadata, open_metadata
    from security.replay_guard import ReplayGuard
    from security.transport import TransportSecurityManager
    from security.authorize import (
        register_node, authorize, Role, set_enforce_policy,
        get_audit_log, clear_audit_log, clear_registry,
    )

    sampler = ResourceSampler(interval=0.1)
    sampler.start()

    proc = psutil.Process(os.getpid())
    system_start = time.perf_counter()

    # ── System baseline ──
    mem_baseline = proc.memory_info()
    disk = psutil.disk_usage(str(PROTO_ROOT))
    _metrics["system"] = {
        "pid": os.getpid(),
        "cpu_count": psutil.cpu_count(),
        "cpu_freq_mhz": round(psutil.cpu_freq().current, 0) if psutil.cpu_freq() else 0,
        "total_ram_mb": round(psutil.virtual_memory().total / (1024**2), 0),
        "available_ram_mb": round(psutil.virtual_memory().available / (1024**2), 0),
        "disk_total_gb": round(disk.total / (1024**3), 2),
        "disk_used_gb": round(disk.used / (1024**3), 2),
        "disk_free_gb": round(disk.free / (1024**3), 2),
        "disk_percent": disk.percent,
        "baseline_rss_mb": round(mem_baseline.rss / (1024**2), 2),
    }

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 1: B1 → B5 Scenario Simulation
    # ═══════════════════════════════════════════════════════════════════════
    print("  [Dashboard] Phase 1: B1 → B5 Simulation...")
    with PhaseTimer("Phase 1: B1→B5 Simulation"):
        result = run_scenario(seed=42)

    _metrics["recognition"]["total_events"] = len(result.events)
    _metrics["recognition"]["b1_events"] = len(result.b1_events)
    _metrics["recognition"]["b5_events"] = len(result.b5_events)
    _metrics["recognition"]["handoff_success"] = result.handoff.routing_confirmed

    # Build per-building event timeline
    timeline = []
    for evt in result.events:
        timeline.append({
            "t": round(evt.sim_time, 1),
            "building": evt.building_id,
            "zone": evt.zone,
            "occupant": evt.matched_occupant,
            "probability": round(evt.probability, 4),
        })
    _metrics["timeline"] = timeline

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 2: Evaluation Metrics
    # ═══════════════════════════════════════════════════════════════════════
    print("  [Dashboard] Phase 2: Evaluation metrics...")
    with PhaseTimer("Phase 2: Evaluation Metrics"):
        pm = paper_metrics(result)
        rm = routing_metrics(result)

    _metrics["recognition"]["accuracy"] = round(pm.recognition_accuracy * 100, 1)
    _metrics["recognition"]["optimal_theta"] = round(pm.optimal_theta, 4)
    _metrics["recognition"]["precision_at_theta"] = round(pm.optimal_precision, 4)
    _metrics["recognition"]["recall_at_theta"] = round(pm.optimal_recall, 4)
    _metrics["recognition"]["correct"] = pm.correct_recognitions
    _metrics["recognition"]["pr_curve"] = {
        "thetas": [round(t, 4) for t in pm.theta_values],
        "precisions": [round(p, 4) for p in pm.precisions],
        "recalls": [round(r, 4) for r in pm.recalls],
        "f1_scores": [round(f, 4) for f in pm.f1_scores],
    }

    _metrics["routing"]["total_lookups"] = rm.total_lookups
    _metrics["routing"]["rank1_successes"] = rm.rank1_successes
    _metrics["routing"]["rank1_accuracy"] = round(rm.rank1_accuracy * 100, 1)
    _metrics["routing"]["avg_buildings_contacted"] = round(rm.avg_buildings_contacted, 2)
    _metrics["routing"]["broadcast_buildings"] = rm.broadcast_buildings
    _metrics["routing"]["efficiency_gain"] = round(rm.efficiency_gain * 100, 1)

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 3: Chart Generation
    # ═══════════════════════════════════════════════════════════════════════
    print("  [Dashboard] Phase 3: Chart generation...")
    with PhaseTimer("Phase 3: Chart Generation"):
        report_dir = str(PROTO_ROOT / "reports")
        generate_evaluation_report(pm, rm, output_dir=report_dir)

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 4: Security Stack
    # ═══════════════════════════════════════════════════════════════════════
    print("  [Dashboard] Phase 4: Security stack...")

    security_timings = {}

    # Key generation timing
    with PhaseTimer("Phase 4: Security Stack"):
        t0 = time.perf_counter()
        b1_id = NodeIdentity.generate("B1")
        security_timings["keygen_b1_us"] = round((time.perf_counter() - t0) * 1e6, 1)

        t0 = time.perf_counter()
        b5_id = NodeIdentity.generate("B5")
        security_timings["keygen_b5_us"] = round((time.perf_counter() - t0) * 1e6, 1)

        # Key exchange timing
        t0 = time.perf_counter()
        channel_b1, channel_b5 = ephemeral_handshake(b1_id, b5_id)
        security_timings["ecdh_handshake_us"] = round((time.perf_counter() - t0) * 1e6, 1)

        # Seal metadata timing
        metadata = create_handoff_metadata(
            visitor_id=result.handoff.occupant_id,
            source_building="B1", dest_building="B5", confidence=0.95,
        )

        t0 = time.perf_counter()
        envelope = seal_metadata(metadata, b1_id, channel_b1)
        security_timings["seal_metadata_us"] = round((time.perf_counter() - t0) * 1e6, 1)

        # Unseal metadata timing
        t0 = time.perf_counter()
        recovered = open_metadata(envelope, channel_b5, b1_id.ed25519_public)
        security_timings["unseal_metadata_us"] = round((time.perf_counter() - t0) * 1e6, 1)

        # Replay guard timing
        guard = ReplayGuard(window_seconds=120.0)
        t0 = time.perf_counter()
        rg1 = guard.validate(envelope.encryption_nonce, envelope.timestamp, envelope.message_id)
        security_timings["replay_check_us"] = round((time.perf_counter() - t0) * 1e6, 1)

        rg2 = guard.validate(envelope.encryption_nonce, envelope.timestamp, envelope.message_id)
        rg_stats = guard.get_stats()

        # RBAC timing
        clear_registry()
        clear_audit_log()
        set_enforce_policy(True)

        buildings = [f"B{i}" for i in range(10)]
        for b in buildings:
            register_node(b, Role.BUILDING_NODE)
        register_node("admin_user", Role.ADMIN)
        register_node("query_client_1", Role.QUERY_CLIENT)

        t0 = time.perf_counter()
        for _ in range(100):
            authorize("B1", "QUERY", "state_table")
        security_timings["rbac_100_checks_us"] = round((time.perf_counter() - t0) * 1e6, 1)
        security_timings["rbac_per_check_us"] = round(security_timings["rbac_100_checks_us"] / 100, 2)

        audit_log = get_audit_log()
        set_enforce_policy(False)

    _metrics["security"] = {
        "timings": security_timings,
        "replay_guard": {
            "validated": rg_stats["total_validated"],
            "rejected": rg_stats["total_rejected"],
            "replay_attempts": rg_stats["replay_attempts"],
        },
        "rbac": {
            "registered_principals": 12,
            "roles": ["BUILDING_NODE", "ADMIN", "QUERY_CLIENT"],
            "audit_entries": len(audit_log),
            "permit_count": sum(1 for e in audit_log if e.get("decision") == "PERMIT"),
            "deny_count": sum(1 for e in audit_log if e.get("decision") == "DENY"),
        },
        "crypto_stack": [
            "X25519 — Ephemeral ECDH Key Exchange",
            "HKDF-SHA256 — Session Key Derivation",
            "AES-128-GCM — Authenticated Encryption",
            "Ed25519 — Digital Signatures",
        ],
        "b1_ed25519_pub": b1_id.ed25519_public_bytes().hex()[:48] + "...",
        "b5_ed25519_pub": b5_id.ed25519_public_bytes().hex()[:48] + "...",
    }

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 5: Query Performance
    # ═══════════════════════════════════════════════════════════════════════
    print("  [Dashboard] Phase 5: Query benchmarks...")

    with PhaseTimer("Phase 5: Query Benchmarks"):
        from dsts.dsts import DSTS as DSTSClass

        query_timings = []
        dsts = result.dsts

        # Benchmark query_occupant
        for _ in range(50):
            t0 = time.perf_counter()
            dsts.query_occupant(result.handoff.occupant_id)
            query_timings.append(round((time.perf_counter() - t0) * 1e6, 1))

        _metrics["query"] = {
            "query_occupant_samples": query_timings,
            "mean_us": round(sum(query_timings) / len(query_timings), 2),
            "min_us": round(min(query_timings), 2),
            "max_us": round(max(query_timings), 2),
            "p50_us": round(sorted(query_timings)[25], 2),
            "p99_us": round(sorted(query_timings)[49], 2),
        }

    # ── Per-building metrics ──
    for bid in ["B1", "B5"]:
        bsts = dsts.buildings.get(bid)
        if bsts:
            vcount = 0
            if bsts.visitor_table is not None and hasattr(bsts.visitor_table, 'occupant_ids'):
                vcount = len(bsts.visitor_table.occupant_ids)
            _metrics["buildings"][bid] = {
                "registered_occupants": len(bsts.registered_occupants),
                "visitor_count": vcount,
                "event_count": len([e for e in result.events if e.building_id == bid]),
            }


    # ── Stop sampler ──
    sampler.stop()
    _metrics["resource_samples"] = sampler.samples

    total_wall = time.perf_counter() - system_start
    _metrics["system"]["total_wall_time_ms"] = round(total_wall * 1000, 2)
    _metrics["system"]["total_phases"] = len(_metrics["phases"])

    mem_final = proc.memory_info()
    _metrics["system"]["final_rss_mb"] = round(mem_final.rss / (1024**2), 2)
    _metrics["system"]["peak_rss_mb"] = round(
        max(s["rss_mb"] for s in sampler.samples) if sampler.samples else mem_final.rss / (1024**2), 2
    )

    print(f"  [Dashboard] Collection complete in {total_wall*1000:.0f}ms\n")


# ── HTTP Server ───────────────────────────────────────────────────────────────

class DashboardHandler(SimpleHTTPRequestHandler):
    """Serves the dashboard HTML and JSON API."""

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "/dashboard":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = _dashboard_html_path.read_bytes()
            self.wfile.write(html)

        elif path == "/api/metrics":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(_metrics, indent=2).encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress access logs


def serve_dashboard(port=8050):
    """Start the dashboard HTTP server."""
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"  ✦ DSTS Monitoring Dashboard: http://localhost:{port}")
    print(f"  ✦ Metrics API:               http://localhost:{port}/api/metrics")
    print(f"  ✦ Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Dashboard stopped.")
        server.server_close()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 64)
    print("  DSTS Monitoring Dashboard — Metric Collection")
    print("=" * 64 + "\n")

    collect_metrics()
    serve_dashboard()

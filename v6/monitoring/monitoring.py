"""
v6/monitoring/monitoring.py — Prometheus telemetry & observability metrics
=============================================================================

OVERVIEW:
Provides a complete Prometheus instrumentation subsystem for real-time monitoring and observability across DSTS nodes.

THEORETICAL TELEMETRY ARCHITECTURE:
- Event Telemetry: Tracks event generation rate, processing throughput, and inter-building migration frequencies.
- Biometric Recognition Telemetry: Observes face-match probability distributions, success rates, and rolling accuracy gauges.
- Distributed Routing Telemetry: Measures Rank-1 success rate, building contact histograms, and lookup latency (seconds).
- Security Audit Telemetry: Monitors authorization failure counts, digital signature failures, and replay attack attempts.
- Prometheus Integration: Exposes `/metrics` endpoint via HTTP server or pushes to Prometheus Pushgateway.

KEY CONTRACTS:
- `MetricsCollector`: Per-building instrumentation helper.
- `start_metrics_server(port)`: Starts background HTTP server for Prometheus scraping.
- `push_metrics(gateway, job)`: Pushes simulation metrics to Pushgateway.
"""

import time
from typing import Optional
from prometheus_client import (
    Counter, Histogram, Gauge, Summary,
    CollectorRegistry, generate_latest, start_http_server,
    push_to_gateway, REGISTRY,
)


# ─── Event Telemetry Metrics ──────────────────────────────────────────────────

EVENTS_GENERATED = Counter(
    'dsts_events_generated_total',
    'Total number of recognition events generated',
    ['building_id'],
)

EVENTS_PROCESSED = Counter(
    'dsts_events_processed_total',
    'Total number of events processed by BSTS state transitions',
    ['building_id'],
)

INTER_BUILDING_TRANSITIONS = Counter(
    'dsts_inter_building_transitions_total',
    'Total inter-building transitions through z_T',
    ['source_building', 'dest_building'],
)


# ─── Recognition Telemetry Metrics ───────────────────────────────────────────

RECOGNITION_ATTEMPTS = Counter(
    'dsts_recognition_attempts_total',
    'Total face recognition attempts',
    ['building_id'],
)

RECOGNITION_SUCCESSES = Counter(
    'dsts_recognition_successes_total',
    'Successful face recognitions (prob > threshold)',
    ['building_id'],
)

RECOGNITION_FAILURES = Counter(
    'dsts_recognition_failures_total',
    'Failed face recognitions (prob < threshold)',
    ['building_id'],
)

RECOGNITION_ACCURACY = Gauge(
    'dsts_recognition_accuracy',
    'Current recognition accuracy (rolling)',
    ['building_id'],
)

RECOGNITION_PROBABILITY = Histogram(
    'dsts_recognition_probability',
    'Distribution of recognition probabilities',
    ['building_id'],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99],
)


# ─── Distributed Routing Telemetry Metrics ────────────────────────────────────

ROUTING_REQUESTS = Counter(
    'dsts_routing_requests_total',
    'Total distributed routing requests',
    ['building_id'],
)

ROUTING_RANK1_SUCCESS = Counter(
    'dsts_routing_rank1_success_total',
    'Routing requests resolved on rank-1 (first guess)',
    ['building_id'],
)

ROUTING_RANK1_ACCURACY = Gauge(
    'dsts_routing_rank1_accuracy',
    'Current rank-1 routing accuracy',
)

BUILDINGS_CONTACTED = Histogram(
    'dsts_buildings_contacted_per_lookup',
    'Number of buildings contacted per routing lookup',
    ['building_id'],
    buckets=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
)

ROUTING_LATENCY = Histogram(
    'dsts_routing_latency_seconds',
    'Routing request latency in seconds',
    ['building_id'],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)


# ─── Security Telemetry Metrics ───────────────────────────────────────────────

AUTH_FAILURES = Counter(
    'dsts_auth_failures_total',
    'Total authorization failures',
    ['building_id', 'reason'],
)

SIGNATURE_INVALID = Counter(
    'dsts_signature_invalid_total',
    'Total invalid digital signature events',
    ['building_id'],
)

REPLAY_ATTEMPTS = Counter(
    'dsts_replay_attempts_total',
    'Total replay attack attempts detected',
    ['building_id'],
)

MESSAGES_REJECTED = Counter(
    'dsts_messages_rejected_total',
    'Total messages rejected (any reason)',
    ['building_id', 'reason'],
)


# ─── Per-Building Metrics Collector Class ──────────────────────────────────────

class MetricsCollector:
    """
    Per-building Prometheus telemetry collector.

    Provides high-level helper methods to record recognition events, routing metrics, and security audit flags.
    """

    def __init__(self, building_id: str):
        self.building_id = building_id
        self._recognition_total = 0
        self._recognition_success = 0

    def record_event_generated(self) -> None:
        """Record a single recognition event generation counter increment."""
        EVENTS_GENERATED.labels(building_id=self.building_id).inc()

    def record_event_processed(self) -> None:
        """Record an event processed by BSTS state transitions."""
        EVENTS_PROCESSED.labels(building_id=self.building_id).inc()

    def record_inter_building_transition(
        self, source: str, dest: str,
    ) -> None:
        """Record an inter-building handoff transition through z_T."""
        INTER_BUILDING_TRANSITIONS.labels(
            source_building=source, dest_building=dest,
        ).inc()

    def record_recognition(
        self, success: bool, probability: float,
    ) -> None:
        """
        [BREAKPOINT: Recognition Telemetry Record]
        Updates recognition counters, probability histogram, and rolling accuracy gauge.
        """
        RECOGNITION_ATTEMPTS.labels(building_id=self.building_id).inc()
        RECOGNITION_PROBABILITY.labels(building_id=self.building_id).observe(probability)

        self._recognition_total += 1
        if success:
            RECOGNITION_SUCCESSES.labels(building_id=self.building_id).inc()
            self._recognition_success += 1
        else:
            RECOGNITION_FAILURES.labels(building_id=self.building_id).inc()

        # Update rolling accuracy calculation
        accuracy = self._recognition_success / self._recognition_total
        RECOGNITION_ACCURACY.labels(building_id=self.building_id).set(accuracy)

    def record_routing(
        self,
        rank1: bool,
        buildings_contacted: int,
        latency: float,
    ) -> None:
        """
        [BREAKPOINT: Routing Telemetry Record]
        Records routing request lookup metrics, contact count histogram, and latency.
        """
        ROUTING_REQUESTS.labels(building_id=self.building_id).inc()
        BUILDINGS_CONTACTED.labels(building_id=self.building_id).observe(
            buildings_contacted,
        )
        ROUTING_LATENCY.labels(building_id=self.building_id).observe(latency)

        if rank1:
            ROUTING_RANK1_SUCCESS.labels(building_id=self.building_id).inc()

    def record_auth_failure(self, reason: str) -> None:
        """Record an RBAC authorization failure event."""
        AUTH_FAILURES.labels(
            building_id=self.building_id, reason=reason,
        ).inc()

    def record_invalid_signature(self) -> None:
        """Record an invalid digital signature detection."""
        SIGNATURE_INVALID.labels(building_id=self.building_id).inc()

    def record_replay_attempt(self) -> None:
        """Record a replay attack attempt caught by ReplayGuard."""
        REPLAY_ATTEMPTS.labels(building_id=self.building_id).inc()

    def record_message_rejected(self, reason: str) -> None:
        """Record a rejected message."""
        MESSAGES_REJECTED.labels(
            building_id=self.building_id, reason=reason,
        ).inc()


# ─── Metrics Server & Exporter ────────────────────────────────────────────────

def start_metrics_server(port: int = 9090) -> None:
    """
    [BREAKPOINT: Prometheus HTTP Exporter]
    Launches background HTTP server exposing standard `/metrics` endpoint.
    """
    start_http_server(port)


def push_metrics(
    gateway: str = "localhost:9091",
    job: str = "dsts_simulation",
) -> None:
    """[BREAKPOINT: Pushgateway Exporter] Pushes registry metrics to Prometheus Pushgateway."""
    push_to_gateway(gateway, job=job, registry=REGISTRY)


def get_metrics_text() -> str:
    """Export current metric registry as Prometheus text format string."""
    return generate_latest(REGISTRY).decode('utf-8')


def metrics_snapshot(collector: MetricsCollector) -> dict:
    """Return in-memory snapshot dictionary of key metrics for CLI display."""
    return {
        "building_id": collector.building_id,
        "recognition_total": collector._recognition_total,
        "recognition_success": collector._recognition_success,
        "recognition_accuracy": (
            collector._recognition_success / collector._recognition_total
            if collector._recognition_total > 0 else 0.0
        ),
    }


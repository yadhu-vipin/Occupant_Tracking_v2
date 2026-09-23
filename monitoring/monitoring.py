"""
v6/monitoring/monitoring.py — Prometheus metrics for DSTS
============================================================
Collects runtime metrics from all DSTS components:

  Events:        generated, processed
  Recognition:   attempts, successes, failures, accuracy
  Routing:       requests, rank-1 success, buildings contacted, latency
  Security:      verification failures, replay attempts, auth failures

Metrics are exposed via prometheus_client and can be:
  - Scraped by Prometheus via HTTP /metrics endpoint
  - Pushed to Pushgateway for batch simulation jobs

Architecture:
  DSTS Components → MetricsCollector → Prometheus → Grafana
"""

import time
from typing import Optional
from prometheus_client import (
    Counter, Histogram, Gauge, Summary,
    CollectorRegistry, generate_latest, start_http_server,
    push_to_gateway, REGISTRY,
)


# ─── Custom Registry ─────────────────────────────────────────────────────────
# Use default registry for simplicity; can switch to custom for isolation

# ─── Event Metrics ────────────────────────────────────────────────────────────

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


# ─── Recognition Metrics ─────────────────────────────────────────────────────

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


# ─── Routing Metrics ─────────────────────────────────────────────────────────

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


# ─── Security Metrics ────────────────────────────────────────────────────────

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


# ─── Metrics Collector ───────────────────────────────────────────────────────

class MetricsCollector:
    """
    Instruments DSTS operations with Prometheus metrics.

    Usage:
        collector = MetricsCollector("B1")
        collector.record_event_generated()
        collector.record_recognition(success=True, probability=0.95)
        collector.record_routing(rank1=True, buildings_contacted=1, latency=0.01)
    """

    def __init__(self, building_id: str):
        self.building_id = building_id
        self._recognition_total = 0
        self._recognition_success = 0

    def record_event_generated(self) -> None:
        """Record an event generation."""
        EVENTS_GENERATED.labels(building_id=self.building_id).inc()

    def record_event_processed(self) -> None:
        """Record an event processed by BSTS."""
        EVENTS_PROCESSED.labels(building_id=self.building_id).inc()

    def record_inter_building_transition(
        self, source: str, dest: str,
    ) -> None:
        """Record an inter-building transition."""
        INTER_BUILDING_TRANSITIONS.labels(
            source_building=source, dest_building=dest,
        ).inc()

    def record_recognition(
        self, success: bool, probability: float,
    ) -> None:
        """Record a recognition attempt and its outcome."""
        RECOGNITION_ATTEMPTS.labels(building_id=self.building_id).inc()
        RECOGNITION_PROBABILITY.labels(building_id=self.building_id).observe(probability)

        self._recognition_total += 1
        if success:
            RECOGNITION_SUCCESSES.labels(building_id=self.building_id).inc()
            self._recognition_success += 1
        else:
            RECOGNITION_FAILURES.labels(building_id=self.building_id).inc()

        # Update rolling accuracy
        accuracy = self._recognition_success / self._recognition_total
        RECOGNITION_ACCURACY.labels(building_id=self.building_id).set(accuracy)

    def record_routing(
        self,
        rank1: bool,
        buildings_contacted: int,
        latency: float,
    ) -> None:
        """Record a routing lookup."""
        ROUTING_REQUESTS.labels(building_id=self.building_id).inc()
        BUILDINGS_CONTACTED.labels(building_id=self.building_id).observe(
            buildings_contacted,
        )
        ROUTING_LATENCY.labels(building_id=self.building_id).observe(latency)

        if rank1:
            ROUTING_RANK1_SUCCESS.labels(building_id=self.building_id).inc()

    def record_auth_failure(self, reason: str) -> None:
        """Record an authorization failure."""
        AUTH_FAILURES.labels(
            building_id=self.building_id, reason=reason,
        ).inc()

    def record_invalid_signature(self) -> None:
        """Record an invalid digital signature."""
        SIGNATURE_INVALID.labels(building_id=self.building_id).inc()

    def record_replay_attempt(self) -> None:
        """Record a replay attack attempt."""
        REPLAY_ATTEMPTS.labels(building_id=self.building_id).inc()

    def record_message_rejected(self, reason: str) -> None:
        """Record a rejected message."""
        MESSAGES_REJECTED.labels(
            building_id=self.building_id, reason=reason,
        ).inc()


# ─── Server & Push ────────────────────────────────────────────────────────────

def start_metrics_server(port: int = 9090) -> None:
    """Start an HTTP server exposing /metrics for Prometheus scraping."""
    start_http_server(port)


def push_metrics(
    gateway: str = "localhost:9091",
    job: str = "dsts_simulation",
) -> None:
    """Push metrics to a Prometheus Pushgateway (for batch jobs)."""
    push_to_gateway(gateway, job=job, registry=REGISTRY)


def get_metrics_text() -> str:
    """Return current metrics as Prometheus text format."""
    return generate_latest(REGISTRY).decode('utf-8')


# ─── Convenience: snapshot for display ────────────────────────────────────────

def metrics_snapshot(collector: MetricsCollector) -> dict:
    """
    Return a snapshot of key metrics for display/logging.

    This reads from the collector's internal counters (not Prometheus),
    suitable for printing in the demo script.
    """
    return {
        "building_id": collector.building_id,
        "recognition_total": collector._recognition_total,
        "recognition_success": collector._recognition_success,
        "recognition_accuracy": (
            collector._recognition_success / collector._recognition_total
            if collector._recognition_total > 0 else 0.0
        ),
    }

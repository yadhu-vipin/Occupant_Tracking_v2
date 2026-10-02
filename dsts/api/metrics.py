"""
dsts/api/metrics.py — Person D, Lane D.

Prometheus metrics for DSTS:
- Event generation and processing
- Recognition
- Routing
- Security verification
- Replay detection
- Authentication failures
- Occupancy
"""

from __future__ import annotations

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
)


# ================================================================
# EVENTS
# ================================================================

events_generated_total = Counter(
    "dsts_events_generated_total",
    "Number of mobility events generated",
    ["building_id"],
)


events_processed_total = Counter(
    "dsts_events_processed_total",
    "Number of mobility events processed",
    ["building_id"],
)


# ================================================================
# RECOGNITION
# ================================================================

recognition_attempts_total = Counter(
    "dsts_recognition_attempts_total",
    "Number of recognition attempts",
    ["building_id"],
)


recognition_success_total = Counter(
    "dsts_recognition_success_total",
    "Number of successful recognition attempts",
    ["building_id"],
)


recognition_failure_total = Counter(
    "dsts_recognition_failure_total",
    "Number of failed recognition attempts",
    ["building_id"],
)


# ================================================================
# ROUTING
# ================================================================

routing_requests_total = Counter(
    "dsts_routing_requests_total",
    "Number of inter-building routing or handoff requests",
    ["building_id"],
)


routing_rank1_success_total = Counter(
    "dsts_routing_rank1_success_total",
    "Number of routing lookups where the correct building ranked first",
)


buildings_contacted = Histogram(
    "dsts_buildings_contacted",
    "Number of buildings contacted per routing lookup",
    buckets=(
        1,
        2,
        3,
        5,
        10,
    ),
)


routing_latency_seconds = Histogram(
    "dsts_routing_latency_seconds",
    "End-to-end routing latency in seconds",
)


# ================================================================
# SECURITY
# ================================================================

security_verification_failures_total = Counter(
    "dsts_security_verification_failures_total",
    "Security verification failures by type",
    ["kind"],
)


replay_attempts_detected_total = Counter(
    "dsts_replay_attempts_detected_total",
    "Number of replay attempts detected",
)


auth_failures_total = Counter(
    "dsts_auth_failures_total",
    "Number of authentication failures",
)


# ================================================================
# OCCUPANCY
# ================================================================

occupancy_gauge = Gauge(
    "dsts_occupancy",
    "Current occupant count by building and zone",
    ["building_id", "zone"],
)
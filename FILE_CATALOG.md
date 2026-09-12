# DSTS Repository File Catalog

This file provides a comprehensive reference of all files in the **Occupant_Tracking_v2** repository, detailing file purpose, key classes/functions, input/output contracts, and major execution breakpoints.

---

## Table of Contents
1. [Simulation & Scenario Engine (`sim/`)](#1-simulation--scenario-engine-sim)
2. [Security & Cryptography Layer (`security/`)](#2-security--cryptography-layer-security)
3. [Monitoring & Telemetry Layer (`monitoring/` & `deploy/`)](#3-monitoring--telemetry-layer-monitoring--deploy)
4. [DSTS Bayesian State Engine (`dsts/`)](#4-dsts-bayesian-state-engine-dsts)
5. [Identification & Routing Layer (`identify/`)](#5-identification--routing-layer-identify)
6. [Building Node Layer (`node/` & `net/`)](#6-building-node-layer-node--net)
7. [Test Suite (`tests/`)](#7-test-suite-tests)
8. [Root Driver & Configuration Files](#8-root-driver--configuration-files)

---

## 1. Simulation & Scenario Engine (`sim/`)

### `sim/event_generator.py`
- **Intro & Purpose**: Generates synthetic, deterministic occupant movement trajectories across campus building zone graphs. Produces sensor camera `RecognitionEvent` objects for state updating.
- **Key Classes & Functions**:
  - `generate_occupant_movement(occupant_id, building_id, seed, num_events)`: Simulates Markovian random walks between adjacent zones.
  - `generate_campus_events(buildings, occupants_per_building, seed)`: Generates full campus multi-building benchmark trajectories.
- **Input**: Building zone adjacency graph, occupant list, seed integer.
- **Output**: Chronologically sorted list of `RecognitionEvent` objects.
- **Major Breakpoint**: Inter-building transition event when an occupant enters transition zone `z_T`.

### `sim/scenario_b1_b5.py`
- **Intro & Purpose**: Defines the fixed, 40-event evaluation scenario illustrating an occupant walking from $B_1 \to z_1 \dots z_T \to B_5$.
- **Key Classes & Functions**:
  - `create_b1_b5_scenario(seed=42)`: Constructs the deterministic sequence of 40 events split into Phase 1 ($B_1$), Inter-building Handoff ($z_T$), and Phase 2 ($B_5$).
- **Input**: Random seed (default: 42).
- **Output**: Tuple `(scenario_events, occupant_id, primary_building, target_building)`.
- **Major Breakpoint**: Event index 31 ($t=318.8\text{s}$) where occupant reaches $z_T$ in $B_1$ and triggers inter-building handoff.

### `sim/evaluation.py`
- **Intro & Purpose**: Evaluates system-level tracking and routing performance against paper baseline metrics.
- **Key Classes & Functions**:
  - `evaluate_recognition_performance(events, predictions, confidence_thresholds)`: Calculates optimal threshold $\theta_{\text{opt}}$, accuracy, precision, and recall curves.
  - `evaluate_routing_gain(router, total_lookups)`: Calculates Rank-1 accuracy and communication efficiency gain over broadcast.
  - `MetricsCollector`: Accumulates real-time runtime counters and exports summary dicts.
- **Input**: Simulated events, BSTS state trajectories, LSH router queries.
- **Output**: Dictionary of quantitative metrics ($\text{Accuracy}, \text{Rank-1}, \text{Efficiency Gain}$).

### `sim/report.py`
- **Intro & Purpose**: Matplotlib chart visualization generator.
- **Key Classes & Functions**:
  - `generate_visual_reports(metrics, output_dir)`: Plots Precision-Recall curves, recognition accuracy bars, building contact counts, and routing efficiency comparisons.
- **Input**: Metrics dictionary, output file path `reports/`.
- **Output**: 4 PNG visual reports saved to `reports/`.

---

## 2. Security & Cryptography Layer (`security/`)

### `security/crypto.py`
- **Intro & Purpose**: Implements zero-trust cryptographic operations for node-to-node metadata communication.
- **Key Classes & Functions**:
  - `NodeIdentity`: Manages Ed25519 keypair for node authentication and digital signatures.
  - `SecureChannel`: Ephemeral X25519 Diffie-Hellman handshake & HKDF key derivation.
  - `aes_gcm_encrypt(key, plaintext, associated_data)`: AES-128-GCM authenticated encryption.
  - `aes_gcm_decrypt(key, ciphertext, nonce, tag, associated_data)`: AES-128-GCM authentication tag validation and decryption.
- **Input**: Node IDs, raw metadata bytes, public/private key pairs.
- **Output**: `SecureMetadataEnvelope` objects, decrypted dictionary payloads.
- **Major Breakpoint**: Signature validation check during `open_metadata()`.

### `security/metadata.py`
- **Intro & Purpose**: Data structure definitions for encrypted handoff messages exchanged between building edge nodes.
- **Key Classes & Functions**:
  - `TransitionMetadata`: Structured container storing visitor ID, source building, destination building, transition zone, confidence score, timestamp, and message ID.
  - `SecureMetadataEnvelope`: Encrypted envelope container storing sender ID, ciphertext, nonce, and Ed25519 digital signature.
  - `seal_metadata()` / `open_metadata()`: High-level serialization, encryption, signature, and decryption workflow functions.
- **Input**: `TransitionMetadata` instance, `NodeIdentity` objects, session key.
- **Output**: `SecureMetadataEnvelope` payload or verified `TransitionMetadata`.

### `security/replay_guard.py`
- **Intro & Purpose**: Prevents replay attacks, duplicate message processing, and timestamp spoofing.
- **Key Classes & Functions**:
  - `ReplayGuard`: Maintains a sliding time window cache of processed nonces and message IDs.
  - `validate_and_register(message_id, nonce, timestamp)`: Rejects seen nonces or expired timestamps ($> 300\text{s}$).
- **Input**: Message ID, nonce byte string, timestamp float.
- **Output**: Tuple `(is_valid: bool, rejection_reason: str)`.

### `security/authorize.py`
- **Intro & Purpose**: Enforces Role-Based Access Control (RBAC) policies across distributed node operations.
- **Key Classes & Functions**:
  - `Role`: Enum (`BUILDING_NODE`, `SYSTEM_ADMIN`, `AUDITOR`, `UNTRUSTED`).
  - `RBACEngine`: Manages principal-to-role mappings and evaluates operation permissions (`CAN_HANDOFF`, `CAN_READ_STATE`, `CAN_UPDATE_BLOOM`).
- **Input**: Principal string, requested operation.
- **Output**: Boolean authorization verdict (`True` / `False`).

---

## 3. Monitoring & Telemetry Layer (`monitoring/` & `deploy/`)

### `monitoring/monitoring.py`
- **Intro & Purpose**: Real-time Prometheus metrics recorder and HTTP metrics exporter endpoint.
- **Key Classes & Functions**:
  - `DSTSMetrics`: Prometheus Counter, Gauge, and Histogram metrics definition wrapper.
  - `start_metrics_server(port=8000)`: Boots background HTTP server serving `/metrics`.
  - `record_event()`, `record_recognition()`, `record_routing_query()`, `record_security_event()`: Ingest telemetry points.
- **Input**: Runtime execution signals and counter updates.
- **Output**: Prometheus-formatted text metrics stream on `http://localhost:8000/metrics`.

### `monitoring/prometheus.yml`
- **Intro & Purpose**: Prometheus scraper configuration file set to pull metrics from `host.docker.internal:8000` every 5s.

### `monitoring/grafana_dashboard.json`
- **Intro & Purpose**: Grafana dashboard visualization definition JSON for DSTS live telemetry.

### `deploy/docker-compose.monitoring.yml`
- **Intro & Purpose**: Docker Compose stack specification launching Prometheus and Grafana containers.

---

## 4. DSTS Bayesian State Engine (`dsts/`)

### `dsts/bsts.py`
- **Intro & Purpose**: Building Spatial-Temporal Sensing (BSTS) core tracking class.
- **Key Classes & Functions**:
  - `BSTS`: Manages local `StateTable`, updates probability distributions on sensor events, and flags HLC events.
  - `process_event(event)`: Main state transition pipeline method.
- **Input**: Camera `RecognitionEvent`.
- **Output**: Updated state probability distribution vector $S_b(t)$.

### `dsts/state.py`
- **Intro & Purpose**: Maintains the $N \times M$ matrix $S_b(t)$ mapping $N$ occupants to $M$ spatial zones.
- **Key Classes & Functions**:
  - `StateTable`: Matrix storage and normalization helper methods.

### `dsts/transition.py`
- **Intro & Purpose**: Implements Equation 6 spatial transition kernel $P(x_t \mid x_{t-1})$ over the zone adjacency graph.

### `dsts/zones.py`
- **Intro & Purpose**: Zone layout definition specifying $z_1 \dots z_8$ and transition zone $z_T$.

### `dsts/events.py`
- **Intro & Purpose**: Data structure definitions for `RecognitionEvent` and `HLC` notifications.

### `dsts/dsts.py`
- **Intro & Purpose**: Multi-building orchestrator wrapper coordinating multiple building BSTS instances.

---

## 5. Identification & Routing Layer (`identify/`)

### `identify/router.py`
- **Intro & Purpose**: Decentralized building router using LSH and Bloom filter indices to locate unknown occupants across buildings.
- **Key Classes & Functions**:
  - `BuildingRouter`: Stores per-building `BloomSummary` objects and ranks candidate buildings for unknown face queries.

### `identify/bloom_summary.py`
- **Intro & Purpose**: Compact Bloom filter index created from occupant LSH bucket codes.

### `identify/lsh.py`
- **Intro & Purpose**: Locality-Sensitive Hashing (LSH) engine projecting 128D face feature vectors to discrete hash codes.

### `identify/face_recognizer.py`
- **Intro & Purpose**: Local face embedding matcher executing cosine similarity matching against registered local occupants.

---

## 6. Building Node Layer (`node/` & `net/`)

### `node/building_node.py`
- **Intro & Purpose**: High-level Building Edge Node runtime object holding BSTS instance, node crypto identity, visitor registry, and security layer.

### `net/p2p.py`
- **Intro & Purpose**: Inter-building networking abstraction interface for sending encrypted metadata envelopes between building endpoints.

---

## 7. Test Suite (`tests/`)

### `tests/test_integration.py`
- **Intro & Purpose**: 19 automated end-to-end integration tests validating deterministic event generation, BSTS updates, LSH routing accuracy, inter-building handoff, visitor isolation, and paper metrics.

### `tests/test_security.py`
- **Intro & Purpose**: 14 automated unit and security threat tests validating AES-GCM encryption, Ed25519 signatures, replay guard windows, RBAC permission checks, and MitM protection.

---

## 8. Root Driver & Configuration Files

### `run_demo.py`
- **Intro & Purpose**: Primary end-to-end executable driver demonstrating full tracking scenario, performance evaluation, report generation, security checks, and Prometheus metrics.

### `requirements.txt`
- **Intro & Purpose**: Explicit Python dependency specs (`numpy`, `cryptography`, `prometheus_client`, `matplotlib`, `pillow`, `scikit-learn`, `opencv-python`).

### `README.md` & `SYSTEM_BLUEPRINT.md` & `SYSTEM_FLOW.md`
- **Intro & Purpose**: Core documentation manuals for architectural design, system execution, flow traces, and branch merge readiness.

# DSTS Architectural System Blueprint
## Distributed Spatial-Temporal Sensing & Building Routing Blueprint

This document defines the high-level system architecture, module boundaries, data classification, and theoretical foundations of the **Distributed Spatial-Temporal Sensing (DSTS)** framework for multi-building occupant tracking and identification.

---

## 1. System Organization & Blueprint Breakdown

The repository is organized into a modular structure distinguishing **Building-Specific Node Components** (distributed at each building edge) and **System-Wide / Inter-Building Infrastructure** (global routing, simulated events, security protocols, and metrics telemetry).

```
Occupant_Tracking_v2/
├── 🏢 BUILDING-SPECIFIC NODES (Edge Layer)
│   ├── node/building_node.py       # Autonomous Building Node controller & state management
│   ├── dsts/bsts.py                # Building Spatial-Temporal Sensing (BSTS) engine
│   ├── dsts/state.py               # Local state table maintainer (S_b matrix)
│   ├── dsts/transition.py          # Markovian spatial transition kernel (Equation 6)
│   └── dsts/zones.py               # Intra-building spatial zones (z1..z8, transition z_T)
│
├── 🌐 SYSTEM-WIDE / INTER-BUILDING INFRASTRUCTURE (Core & Routing)
│   ├── identify/router.py          # LSH & Bloom filter query router for unknown visitors
│   ├── identify/bloom_summary.py   # Compressed Bloom filter index of local occupants
│   ├── identify/lsh.py             # Locality-Sensitive Hashing for face embeddings
│   ├── identify/face_recognizer.py # Vector embedding matcher & cosine similarity engine
│   ├── security/crypto.py          # Diffie-Hellman (X25519), HKDF, AES-GCM, & Ed25519 signatures
│   ├── security/metadata.py        # Abstract metadata structures (TransitionMetadata, Envelope)
│   ├── security/replay_guard.py    # Nonce cache, timestamp windowing, & anti-replay verification
│   └── security/authorize.py       # Role-Based Access Control (RBAC) policy engine
│
├── 📊 MONITORING & TELEMETRY
│   ├── monitoring/monitoring.py    # Real-time Prometheus metrics exporter & collector
│   ├── monitoring/prometheus.yml   # Prometheus scraper configuration
│   ├── monitoring/grafana_dashboard.json # Grafana monitoring dashboard definition
│   └── deploy/docker-compose.monitoring.yml # Stack deployment for Prometheus + Grafana
│
├── 🧪 SIMULATION, EVALUATION & REPOSITORY RUNNER
│   ├── sim/event_generator.py     # Deterministic occupant trajectory & event simulator
│   ├── sim/scenario_b1_b5.py       # Controlled B1 -> z_T -> B5 40-event evaluation benchmark
│   ├── sim/evaluation.py         # Recognition precision/recall & routing gain evaluator
│   ├── sim/report.py             # Visualization engine for performance charts
│   ├── tests/test_integration.py  # 19 E2E integration test suites
│   ├── tests/test_security.py     # 14 Cryptographic security test suites
│   ├── run_demo.py               # Main CLI benchmark driver script
│   └── requirements.txt          # Python dependency specifications
```

---

## 2. Structural Layer Responsibilities

### A. Building-Specific (Edge Layer)
- **Local Autonomy**: Each building (e.g., $B_1, B_2, \dots, B_{10}$) operates its own **BSTS** instance independently without central coordination.
- **Privacy Enforcement**: Detailed face embeddings and biometric feature vectors **NEVER** leave the local building edge node.
- **Local State Tracking ($S_b$)**: Maintains local probability distributions over interior zones $Z_b = \{z_1, \dots, z_8, z_T\}$ for all registered occupants.
- **Visitor Isolation**: Visitors entering from other buildings are recorded in an isolated visitor registry without overwriting home database records.

### B. System-Wide Infrastructure (Routing & Coordination)
- **Compact Summary Exchange**: Buildings exchange lightweight, privacy-preserving **Bloom filter summaries** generated from LSH hashes of occupant face embeddings.
- **Decentralized Query Routing**: When an unknown visitor triggers camera events, the node queries the global router to identify the target building rank without broadcasting face images.
- **Inter-Building Handoff**: Coordinates spatial transition as occupants move through transition zone $z_T$ from Building $A$ to Building $B$.

### C. Security & Zero-Trust Metadata Layer (Lane B)
- **Cryptographic Envelopes**: All inter-node metadata packets are sealed with **AES-128-GCM** authenticated encryption and signed via **Ed25519** digital signatures.
- **Anti-Replay Defense**: Enforces sliding timestamp validation windows and non-repeating message ID nonce caching.
- **RBAC Policy Enforcement**: Verifies node permissions (e.g., `BUILDING_NODE`, `SYSTEM_ADMIN`) before processing spatial handoff requests.

---

## 3. Theoretical & Mathematical Foundations

### 1. Bayesian Spatial-Temporal State Transition (Equation 6)
The local state probability vector $S_b(t)$ across zones is updated upon receiving camera event $e_t = (z_k, y_t, t)$ via:

$$P(x_t = z_j \mid e_{1:t}) \propto P(y_t \mid x_t = z_j) \sum_{i} P(x_t = z_j \mid x_{t-1} = z_i) P(x_{t-1} = z_i \mid e_{1:t-1})$$

Where:
- $P(y_t \mid x_t = z_j)$ is the camera observation likelihood (boosted if $z_j = z_k$).
- $P(x_t = z_j \mid x_{t-1} = z_i)$ is the spatial transition probability matrix adhering to zone connectivity graph adjacency.

### 2. High-Likelihood Condition (HLC)
An occupant is confirmed at zone $z^*$:

$$\text{HLC} = \text{True} \iff \max_{z} P(x_t = z \mid e_{1:t}) \ge \theta_{\text{HLC}} \quad (\text{default } \theta_{\text{HLC}} = 0.80)$$

### 3. Inter-Building Spatial Handoff Condition
When an occupant reaches transition zone $z_T$ in Building $A$ ($S_A(t, z_T) \ge \theta_{\text{HLC}}$) and subsequently disappears, Building $A$ seals a `TransitionMetadata` envelope for destination Building $B$.

---

## 4. Architectural Data Classification Matrix

| Data Type | Owner Component | Storage Location | Scope | Security Protocol |
| :--- | :--- | :--- | :--- | :--- |
| **Face Embeddings** | `face_recognizer.py` | Local Building Edge | **Strictly Local** | Never Transmitted |
| **State Matrix ($S_b$)** | `dsts/state.py` | Building RAM | **Local Node** | Local Process Boundary |
| **Bloom Summaries** | `identify/bloom_summary.py` | Global Router Index | **Inter-Node** | Anonymized LSH Hashes |
| **Handoff Metadata** | `security/metadata.py` | Transmitted Packet | **Inter-Node** | AES-GCM + Ed25519 |
| **Telemetry Metrics** | `monitoring/monitoring.py` | Prometheus Scraper | **System-Wide** | Prometheus Endpoint |

---

## 5. Security & Threat Model Blueprint

The system addresses 8 core security threats defined in the Lane B specification:

1. **Man-in-the-Middle (MitM)**: Ephemeral **X25519** Diffie-Hellman key exchange generates session keys via **HKDF-SHA256**.
2. **Message Tampering**: **AES-128-GCM** authentication tags ensure message integrity.
3. **Replay Attacks**: `ReplayGuard` maintains a sliding timestamp window and nonce cache to reject duplicates.
4. **Building Node Spoofing**: Each node possesses a persistent **Ed25519** keypair for cryptographic identity verification.
5. **Metadata Disclosure**: Sensitive metadata payload (occupant ID, source/destination buildings) is encrypted using AES-GCM.
6. **Message Duplication**: Unique 64-bit hex message identifiers are enforced per request.
7. **Unauthorized Access**: Role-Based Access Control (`authorize.py`) validates node identity against authorized roles.
8. **Privacy Leakage**: Zero raw biometric data or full occupant databases are shared between buildings.

# Distributed Spatial-Temporal Sensing (DSTS) Framework
### Multi-Building Occupant Tracking, Decentralized Routing & Zero-Trust Metadata Security

[![Branch](https://img.shields.io/badge/branch-test__3-blue.svg)](https://github.com/yadhu-vipin/Occupant_Tracking_v2/tree/test_3)
[![Build Status](https://img.shields.io/badge/tests-329%2F329%20passed-brightgreen.svg)](#-testing--verification-suite)
[![Pytest Suite](https://img.shields.io/badge/pytest-179%2F179%20passed-brightgreen.svg)](#2-formal-pytest-integration--unit-suite)
[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](requirements.txt)
[![Security Standard](https://img.shields.io/badge/crypto-X25519%20DH%20%7C%20AES--128--GCM%20%7C%20Ed25519-green.svg)](#-security--cryptographic-specification)
[![Seed](https://img.shields.io/badge/seed-42-purple.svg)](#-quick-start--execution-guide)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](#)

---

## 👨‍💻 Developer & Author Contribution
**Developer**: **Arjun Rajesh** (AM.SC.U4CSE23208)  
**Role**: **Lane B — Events, Evaluation, Security & Monitoring Architect**

### Key Contributions & Implementation Scope
1. **Occupant Movement & Event Simulation**:
   - Designed and implemented the deterministic occupant movement simulator (`sim/event_generator.py`) using seeded pseudo-randomness (**Seed 42**).
   - Modeled multi-building trajectory movement strictly adhering to zone adjacency graphs ($z_1 \dots z_8, z_T$).
   - Built the controlled **B1 $\to$ B5 multi-building evaluation scenario** (`sim/scenario_b1_b5.py`) with transition zone ($z_T$) handoffs.

2. **Template Query Engine & Evaluation Pipeline**:
   - Built the complete spatial-temporal template query engine (`dsts/queries.py`) answering paper queries **Q1, Q2, Q3, Q5, and Q6** with role-based access filtering.
   - Built the system evaluation framework (`sim/evaluation.py` and `sim/report.py`) measuring Recognition Accuracy (**100.0%**), Optimal Cosine Threshold ($\theta_{\text{opt}} = 0.971$), LSH Routing Rank-1 Accuracy (**90.5%**), and Communication Efficiency Gain (**86.8%** query reduction vs. broadcast).
   - Automated performance report chart generation (saved to `buildings_prototype/reports/`).

3. **Zero-Trust Inter-Building Security Architecture**:
   - **X25519 Ephemeral Diffie-Hellman (ECDH) Key Exchange**: Implemented secure, per-pair building node key exchange (`security/crypto.py`).
   - **HKDF-SHA256 Key Derivation**: Context-bound 128-bit AES session key derivation using HMAC-based Extract-and-Expand KDF with domain separation (`"DSTS-v1"`).
   - **AES-128-GCM Authenticated Encryption**: Confidentiality and tamper-evident payload encryption (`security/metadata.py`).
   - **Ed25519 Digital Signatures**: Non-repudiation and origin verification for all transition handoff envelopes.
   - **Transport Security & CA**: Built a self-signed Campus Certificate Authority (CA), X.509 certificate issuance/validation, mTLS mutual authentication, certificate pinning, CRL revocation, and rogue node rejection (`security/transport.py`).
   - **Anti-Replay Guard & RBAC**: Developed 300s sliding window timestamp validator, nonce uniqueness cache (`security/replay_guard.py`), and target-aware Role-Based Access Control (`security/authorize.py`).

4. **Contextual Privacy, Dual Dimensions & Persona ReBAC**:
   - Formulated and engineered the **Dual Dimensions** access framework combining **Access Scope** (`none`, `presence`, `current`, `full_track`) and **Location Granularity** (`none`, `zone`, `precise`) in [`security/campus_policy.py`](buildings_prototype/security/campus_policy.py).
   - Designed the pairwise campus privacy policy matrix across `Dean`, `Teacher`, `Student`, and `Visitor` personas.
   - Implemented administrative availability scoping: **Teacher → Dean** permitted as `current / zone` for office/sector checks without exposing exact coordinates or trajectory history.
   - Built the anti-stalking **Office Presence** isolation pattern (`presence` scope) that reports whether an occupant is at their registered office/cabin while completely redacting location coordinates when away.
   - Architected the end-to-end Zero-Trust User-Seeking-Location Protocol and interactive terminal client ([`security/user_privacy_protocol.py`](buildings_prototype/security/user_privacy_protocol.py) & [`query_user.py`](buildings_prototype/query_user.py)).

5. **Testing Infrastructure & Monitoring**:
   - Architected the 150-test comprehensive verification test runner (`test_queries_and_security.py`) and 179 formal pytest tests achieving a **100% pass rate (329/329 total)**.
   - Built Prometheus metric exporter (`monitoring/monitoring.py`) and Grafana monitoring dashboard (`monitoring/grafana_dashboard.json`).
   - Built a real-time **Web Monitoring Dashboard** (`monitoring/dashboard_server.py` + `monitoring/dashboard.html`) displaying CPU load, memory usage, disk I/O, security overhead, and query latency.

---

## 📌 Executive Summary

The **Distributed Spatial-Temporal Sensing (DSTS)** framework provides an edge-computed, privacy-preserving infrastructure for continuous multi-building occupant tracking, unknown visitor routing, and secure inter-building spatial handoffs.

Designed to eliminate centralized database bottlenecks and prevent biometric privacy leakage, DSTS isolates face feature vectors at local building edges. When occupants transit between facility buildings, DSTS leverages **Locality-Sensitive Hashing (LSH)** and compressed **Bloom filter summaries** for decentralized routing, paired with an authenticated zero-trust cryptographic protocol (**X25519 Diffie-Hellman Key Exchange**, **HKDF-SHA256**, **AES-128-GCM**, and **Ed25519**) for inter-node metadata exchange.

All modules are consolidated into a single self-contained directory (`buildings_prototype/`) with a deterministic **Seed 42** configuration ensuring 100% reproducible results across all team members.

---

## 🏛 Framework Architecture & Theoretical Foundations

### 1. Bayesian Spatial-Temporal State Transition (Equation 6)
Each autonomous building node maintains a local state probability matrix $S_b(t)$ across zones $Z_b = \{z_1, \dots, z_8, z_T\}$. Upon receiving camera observation event $e_t = (z_k, y_t, t)$, the local spatial probability distribution updates via:

$$P(x_t = z_j \mid e_{1:t}) \propto P(y_t \mid x_t = z_j) \sum_{i} P(x_t = z_j \mid x_{t-1} = z_i) P(x_{t-1} = z_i \mid e_{1:t-1})$$

Where:
- $P(y_t \mid x_t = z_j)$ represents the camera likelihood model.
- $P(x_t = z_j \mid x_{t-1} = z_i)$ represents the Markovian spatial transition kernel constrained by zone graph adjacency.

### 2. High-Likelihood Condition (HLC) & Spatial Handoff
An occupant presence is confirmed at a zone when:

$$\text{HLC} = \text{True} \iff \max_{z} P(x_t = z \mid e_{1:t}) \ge \theta_{\text{HLC}} \quad (\theta_{\text{HLC}} = 0.80)$$

When an occupant reaches transition zone $z_T$ in Building $A$ under HLC and subsequently leaves camera view, Building $A$ seals an encrypted handoff envelope for target Building $B$.

---

## 🔒 Comprehensive Security & Privacy Architecture

The system enforces a strict **Two-Tier (Dual-Layer) Security Architecture** that decouples node-level infrastructure transport from human privacy and application persona permissions:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  LAYER 2: APPLICATION PERSONAS & HUMAN PRIVACY (Contextual ReBAC)          │
│  Personas: Dean, Teacher, Student, Visitor                                  │
│  Dimensions: Access (none, presence, current, full_track)                   │
│              Granularity (none, zone, precise)                              │
│  Special Scopes: Teacher→Dean (current/zone), Office Presence Check,        │
│                  Class Roster Exemption, Self-Query Clearance               │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       ▼ (Sealed Query Envelope)
┌─────────────────────────────────────────────────────────────────────────────┐
│  LAYER 1: INFRASTRUCTURE & ZERO-TRUST PROTOCOL (Transport & Node RBAC)      │
│  Roles: BUILDING_NODE, ADMIN, QUERY_CLIENT                                  │
│  Primitives: X25519 DH | HKDF-SHA256 | AES-128-GCM | Ed25519 | Campus CA     │
│  Protection: ReplayGuard (Nonce cache + 300s window), Target-Aware URIs     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Layer 1: Infrastructure & Zero-Trust Protocol (Transport RBAC)

The inter-building communication protocol enforces a Zero-Trust Architecture:

```
Building Node A                                                Building Node B
  │                                                               │
  │ 1. Ephemeral X25519 Keypair (sk_A, pk_A)                     │ 1. Ephemeral X25519 Keypair (sk_B, pk_B)
  │ ────────────────────── Exchange Public Keys ────────────────► │
  │ ◄───────────────────── Exchange Public Keys ───────────────── │
  │                                                               │
  │ 2. Scalar Multiplication: S = X25519(sk_A, pk_B)              │ 2. Scalar Multiplication: S = X25519(sk_B, pk_A)
  │    (Identical Shared Secret S)                                │    (Identical Shared Secret S)
  │                                                               │
  │ 3. Key Derivation: K_session = HKDF-SHA256(S, salt, "DSTS-v1")│ 3. Key Derivation: K_session = HKDF-SHA256(S, salt, "DSTS-v1")
  │                                                               │
  │ 4. Encrypt Metadata (AES-128-GCM) + Sign (Ed25519)            │
  │ ──────────────────── Send Secure Envelope ──────────────────► │ 5. Verify Signature & Decrypt (AES-128-GCM)
```

#### Cryptographic Stack Breakdown

| Primitive / Protocol | Implementation File | Function & Guarantee |
| :--- | :--- | :--- |
| **X25519 Diffie-Hellman** | [`security/crypto.py`](buildings_prototype/security/crypto.py) | **Inter-Building Key Exchange**: Establishes shared secret scalar between distributed building nodes without transmitting private keys. |
| **HKDF-SHA256** | [`security/crypto.py`](buildings_prototype/security/crypto.py) | **Key Derivation Function**: Derives high-entropy 128-bit AES session key bound to context string `"DSTS-v1"`. |
| **AES-128-GCM** | [`security/crypto.py`](buildings_prototype/security/crypto.py) | **Authenticated Encryption**: Encrypts transition metadata payload while providing 128-bit authentication tags for tamper detection. |
| **Ed25519** | [`security/crypto.py`](buildings_prototype/security/crypto.py) | **Digital Signatures**: Elliptic curve signature algorithm for non-repudiation and origin verification. |
| **Campus CA & X.509** | [`security/transport.py`](buildings_prototype/security/transport.py) | **Transport Security**: Self-signed Campus CA issues node certificates; enforced via mTLS and certificate pinning. |
| **Replay Guard** | [`security/replay_guard.py`](buildings_prototype/security/replay_guard.py) | **Anti-Replay Protection**: Nonce cache dedup + 300-second sliding timestamp window guard. |
| **Target-Aware RBAC** | [`security/authorize.py`](buildings_prototype/security/authorize.py) | **Access Control**: Fine-grained role permissions (`BUILDING_NODE`, `ADMIN`, `QUERY_CLIENT`) with target entity URI validation (`occupant:<id>`, `building:<id>`, `system`). |

#### Infrastructure Role-Based Access Control (RBAC)

| Role | Permissions | Denied | Target Entity Scoping |
| :--- | :--- | :--- | :--- |
| `BUILDING_NODE` | DETECT, SEEK, RESOLVE, GOSSIP, SYNC, QUERY, HANDOFF, VISITOR_ADD | ADMIN, CONFIGURE, AUDIT_READ | Assigned building node only (`building:B{id}`) |
| `ADMIN` | All 11 permissions (full system access) | — | Unrestricted system-wide (`system`) |
| `QUERY_CLIENT` | QUERY only | DETECT, HANDOFF, SYNC, and all others | Query target occupant URI (`occupant:{id}`) |

---

### Layer 2: Application Personas & Human Privacy (5-Tier Disclosure Hierarchy & ABAC)

When campus occupants seek the location of another individual, the query is governed by a **5-Tier Disclosure Hierarchy** with **Attribute-Based Access Control (ABAC)** and the **Principle of Minimum Necessary Disclosure**, implemented in [`security/campus_policy.py`](buildings_prototype/security/campus_policy.py) and [`security/user_privacy_protocol.py`](buildings_prototype/security/user_privacy_protocol.py).

#### 1. Five-Tier Information Disclosure Hierarchy

Rather than managing separate, ad-hoc combinations of temporal access and spatial granularity, location disclosure is formalized into an ordered 5-tier integer hierarchy:

| Level | Information Exposed | Operational Meaning & Guarantee | Concrete Example |
| :--- | :--- | :--- | :--- |
| **L0 — None** | Nothing | **Access Denied**: Request rejected at authorization gateway; zero data disclosed. | Stalking prevention; out-of-hours denial. |
| **L1 — Presence** | Boolean designated presence & availability | **Availability Only**: Discloses solely whether the target is at their designated office/cabin (`is_present: bool`, `availability: "Available in Cabin"` / `"Away from Cabin"`). When away, campus coordinates and functional zones are **completely redacted**. | *"Dean is in her office: Yes"*<br/>*"Teacher available in cabin: Yes"* |
| **L2 — Current Zone** | Current functional zone + timestamp | **Coarse Spatial Snapshot**: Returns functional sector / zone (e.g. Administration, Library). Room identifiers and exact coordinates $(x, y)$ are withheld. | *"Dean is currently in Administration Zone, last updated 10:42 AM"* |
| **L3 — Precise Current** | Exact room/coordinates + timestamp | **Pinpoint Point-in-Time**: Exact room ID, camera node ID, and metric $(x, y)$ coordinates at simulated time $t$. | *"Dean is in Room A-204, (x, y), 10:42 AM"* |
| **L4 — Historical Track** | Full movement history / trajectory | **Full Spatiotemporal Path**: Complete historical trajectory across campus and multi-building handoffs over time. | *"Dean's movement path over the last 2 hours"* (Audited authority only) |

#### 2. Pairwise Maximum Disclosure Ceiling Matrix

The campus community defines 4 roles: `Dean`, `Teacher`, `Student`, and `Visitor`. Each caller-to-target relationship establishes a **maximum permitted disclosure ceiling**:

| Requester (Caller) ↓ / Target → | **Dean** | **Teacher** | **Student** | **Visitor** |
| :--- | :---: | :---: | :---: | :---: |
| **Dean** | Current / Zone *(L2)* | Full Track / Precise *(L4)* | **Full Track / Precise *(L4)*** | Current / Zone *(L2)* |
| **Teacher** | Current / Zone + Time *(L2)* | Current / Zone *(L2)* | **Current / Zone *(L2)*** | None *(L0)* |
| **Student** | Presence only *(L1)* | Presence only *(L1)* | None *(L0)* | None *(L0)* |
| **Visitor** | Current / Zone *(L2)* | Current / Zone *(L2)* | None *(L0)* | None *(L0)* |

#### 3. Purpose & Context Policy Layer (ABAC)

Permissions are not simply static `Role A → Role B`. Instead, every query is gated dynamically:

$$\text{Role A} + \text{Role B} + \text{Purpose} + \text{Context} \longrightarrow \text{Permitted Disclosure Level}$$

Under the **Principle of Minimum Necessary Disclosure**, the system exposes the least precise and least persistent location information sufficient to satisfy the requester's legitimate business purpose:

$$\text{effective\_disclosure\_level} = \min(\text{requested\_level}, \text{permitted\_ceiling})$$

- 🎓 **Student → Teacher / Dean (`L1` Presence during Office Hours)**:
  - Allowed: `L1_PRESENCE` during active office hours (`is_office_hours=True`, purpose: `office_hours`).
  - Denied: Outside office hours (`is_office_hours=False`), access drops strictly to `L0_NONE`. Tracking faculty around campus is strictly prohibited.
- 🏛️ **Teacher → Dean (`L2` Current Zone + Time for Administrative Interaction)**:
  - Allowed: `L2_CURRENT_ZONE` so faculty can determine if the Dean is in the Administration sector for urgent consultations.
  - Denied: Historical trajectory (`L4`) is strictly blocked.
- 👨‍🏫 **Teacher → Student (`L2` Current Zone during Active Campus Hours)**:
  - Allowed: `L2_CURRENT_ZONE` (current functional zone and timestamp) during active campus hours for academic supervision and classroom management.
  - Denied: Outside campus hours (`is_office_hours=False`), access drops to `L0_NONE`. Full historical trajectory (`L4`) is strictly blocked.
- 🛡️ **Dean → Student / Teacher (`L4` Guarded by Formal Authorization)**:
  - Unlimited surveillance is prevented. Routine dean lookups (`general_lookup`) are capped at `L2_CURRENT_ZONE`.
  - Full trajectory (`L4_HISTORICAL_TRACK`) is only unlocked when tagged with `security_investigation` or `audit` and `authorized_investigation=True`.
- 🚫 **Anti-Stalking Protections (Student & Visitor Isolation)**:
  - `Student → Student`: Strictly `L0_NONE` (prevent student stalking / harassment).
  - `Visitor → Student`: Strictly `L0_NONE` (shield students from external visitors).
  - `Visitor → Visitor`: Strictly `L0_NONE`.
- 👤 **Self-Query Exemption**:
  - Any occupant querying their own location (`caller_id == target_id`) always resolves to `L4_HISTORICAL_TRACK`, ensuring data self-determination.

#### 4. Separation of "Location" from "Availability"

Students and visitors frequently do not need physical tracking; they only need to know: *"Can I meet this person?"*
For `L1_PRESENCE` queries:
- **At Cabin**: Returns `{"is_present": true, "availability": "Available in Cabin", "designated_location": "Cabin"}`.
- **Away from Cabin**: Returns `{"is_present": false, "availability": "Away from Cabin", "designated_location": "Cabin"}`.
- The actual current zone (e.g. cafeteria, corridor) and coordinates are **100% withheld and redacted**, providing strong privacy preservation.

#### 5. End-to-End Zero-Trust User Query Lifecycle

```
[ User Client ] ──(Ephemeral X25519 / AES-128-GCM Envelope)──► [ SecureLocationQueryGateway ]
                                                                     │
                                                 ┌───────────────────┴───────────────────┐
                                                 ▼                                       ▼
                                     [ Layer 1: Transport RBAC ]             [ Layer 2: ABAC & ReBAC ]
                                     • ReplayGuard (nonce & window)         • 5-Tier Disclosure Ceiling
                                     • Entity URI (occupant:<id>)           • Purpose & Office Hours Check
                                     • Principal role == QUERY_CLIENT       • Minimum Necessary Disclosure
                                                 │                                       │
                                                 └───────────────────┬───────────────────┘
                                                                     ▼
                                                        [ Spatial Query Engine ]
                                                                     │
                                                                     ▼
                                                   [ Information Disclosure Filter ]
                                                   • L1: Cabin Availability (Coords Redacted)
                                                   • L2: Functional Zone (Coords Redacted)
                                                   • L3: Precise Coordinates & Room
                                                   • L4: Historical Trajectory
                                                                     │
[ User Client ] ◄──(Encrypted Response Envelope)─────────────────────┘
```

---

## 🧪 Testing & Verification Suite

The repository contains **2 comprehensive test suites** all housed within `buildings_prototype/`. **All 208 total tests pass 100% cleanly.**

### 1. Comprehensive Test Suite Runner (`test_queries_and_security.py`)
**107 Tests — 100% Pass Rate (0.04s execution time)**

Run via terminal:
```bash
python buildings_prototype/test_queries_and_security.py
```

| Section | Test Focus | Test Count | Status | Key Features Tested |
| :--- | :--- | :---: | :---: | :--- |
| **Section 1: Resimulation & Scenario B1$\to$B5** | Movement & State | **11** | ✅ Passed | Seeded determinism (Seed 42), adjacency graph constraints, transition zone $z_T$, Equation 6 state updates. |
| **Section 2: Template Queries Engine** | Spatial Queries | **18** | ✅ Passed | **Q1** (presence after $t$), **Q2** (visitor anomaly count), **Q3** (exit before $t$), **Q5** (all zones visited), **Q6** (location at $t$), plus RBAC query authorization. |
| **Section 3: Transport Security & Cert Pinning** | Transport Security | **18** | ✅ Passed | CA root signatures, node cert issuance, mTLS handshakes, cert pinning, revocation (CRL), expiration, rogue node rejection. |
| **Section 4: Cryptographic Security & Anti-Replay** | Zero-Trust Crypto | **20** | ✅ Passed | X25519 DH key exchange, HKDF derivation, AES-128-GCM tag verification, Ed25519 signatures, nonce uniqueness, 300s window guard. |
| **Section 5: Role-Based Access Control (RBAC)** | Authorization | **16** | ✅ Passed | Roles (`BUILDING_NODE`, `ADMIN`, `QUERY_CLIENT`), route-level permission enforcement, audit logging, unauthorized blocking. |
| **Section 6: Building Edge Hardening & Isolation** | Architectural Hardening | **24** | ✅ Passed | Key isolation between pairs, channel isolation, nonce cache independence, Equation 6 spatial kernel, zone graph adjacency. |
| **TOTAL** | **Comprehensive Suite** | **107** | **100%** | **All 107 tests execute cleanly with zero failures.** |

---

### 2. Formal Pytest Integration & Unit Suite
**103 Tests — 100% Pass Rate (0.72s execution time)**

Run via terminal:
```bash
python -m pytest
```

| Test Module | Test Count | Status | Tested Components |
| :--- | :---: | :---: | :--- |
| [`test_integration.py`](buildings_prototype/tests/test_integration.py) | **19** | ✅ Passed | End-to-end multi-building simulation, B1$\to$B5 handoffs, evaluation metric calculations, report visualization rendering. |
| [`test_queries.py`](buildings_prototype/tests/test_queries.py) | **14** | ✅ Passed | Template query engine (Q1, Q2, Q3, Q5, Q6) + Track Analytics (dwell duration, most frequented zone, longest stay zone). |
| [`test_security.py`](buildings_prototype/tests/test_security.py) | **14** | ✅ Passed | X25519 DH key exchange, HKDF key derivation, AES-GCM tag validation, Ed25519 signature checks, anti-replay nonce tracking, RBAC policies. |
| [`test_transport_security.py`](buildings_prototype/tests/test_transport_security.py) | **11** | ✅ Passed | Campus CA, X.509 cert validation, mTLS mutual authentication, certificate pinning, CRL revocation, expired cert detection, rogue node rejection. |
| [`test_secure_prototype.py`](buildings_prototype/tests/test_secure_prototype.py) | **4** | ✅ Passed | SecureBuildingNodeHandler seal/unseal, multi-building handoff envelope, replay guard integration, RBAC enforcement. |
| [`test_precision_queries.py`](buildings_prototype/tests/test_precision_queries.py) | **8** | ✅ Passed | Hierarchical location precision, zone coarse redaction, coordinate isolation, and spatial disclosure scoping. |
| [`test_campus_policy.py`](buildings_prototype/tests/test_campus_policy.py) | **15** | ✅ Passed | 5-Tier disclosure hierarchy (L0–L4), office hours gating, Dean authorized investigation vs. routine caps, roster overrides, minimum necessary disclosure. |
| [`test_target_awareness.py`](buildings_prototype/tests/test_target_awareness.py) | **8** | ✅ Passed | Target-aware entity URI enforcement (`occupant:<id>`, `building:<id>`, `system`), cross-target boundary defense, unauthorized URI blocking. |
| [`test_secure_user_query.py`](buildings_prototype/tests/test_secure_user_query.py) | **10** | ✅ Passed | End-to-end Zero-Trust User-Seeking-Location protocol, sealed request/response lifecycle, L1 cabin availability check, replay detection. |
| **TOTAL** | **103** | **100%** | **103 / 103 Pytest tests passed in 0.72s.** |

---

## 📊 Evaluation & Benchmark Results

Evaluated on the standardized 40-event multi-building scenario (`python buildings_prototype/run_demo.py`):

| Evaluation Category | Metric | Result | Benchmark Target | Status |
| :--- | :--- | :---: | :---: | :---: |
| **Recognition** | Recognition Accuracy | **100.0%** | $\ge 95.0\%$ | ✅ Superior |
| **Recognition** | Optimal Cosine Threshold ($\theta_{\text{opt}}$) | **0.971** | $0.80 - 0.99$ | ✅ Optimal |
| **Routing** | LSH / Bloom Rank-1 Accuracy | **90.5%** | $\ge 80.0\%$ | ✅ Exceeds |
| **Routing Efficiency** | Avg. Contacted Buildings | **1.2** / 10 | vs. 9.0 Broadcast | ✅ 86.8% Gain |
| **Routing Efficiency** | Communication Efficiency Gain | **86.8%** | $\ge 80.0\%$ | ✅ Exceeds |
| **Security Audit** | Security Threat Checks | **14 / 14 Passed** | 100% | ✅ Zero-Trust |
| **Test Suite** | Unit & Integration Test Pass Rate | **210 / 210 Passed** | 100% | ✅ Verified |

---

## 📈 Monitoring Dashboard

A real-time web monitoring dashboard is included, providing visibility into system resource utilization and performance overhead during the DSTS pipeline execution.

### Launch Dashboard
```bash
python buildings_prototype/monitoring/dashboard_server.py
```
Then open **http://localhost:8050** in your browser.

### Dashboard Panels

| Panel | Metrics Displayed |
| :--- | :--- |
| **KPI Summary** | Total wall time, peak RSS memory, recognition accuracy, routing rank-1 accuracy |
| **CPU Load** | Real-time CPU % chart sampled at 100ms intervals via `psutil` |
| **Memory Usage** | RSS memory (MB) tracked across all 5 pipeline phases |
| **Disk & I/O** | Disk space utilization, read/write bytes, I/O operation counts, system RAM |
| **Phase Timeline** | Wall time, CPU time, and memory delta per phase (Simulation, Evaluation, Charts, Security, Queries) |
| **Security Overhead** | Per-operation cryptographic latency (keygen, ECDH, AES-GCM seal/unseal, replay check, RBAC) |
| **Crypto Stack** | Active cryptographic primitives and RBAC audit summary |
| **Query Latency** | `query_occupant()` histogram distribution, mean/P50/P99/min/max latency |
| **PR Curve** | Interactive Precision/Recall/F1 vs threshold (θ) chart |
| **Routing** | Rank-1 accuracy, buildings contacted, efficiency gain vs broadcast |
| **Replay Guard** | Validated/rejected/replay attempt counters, sliding window config |
| **Audit Trail** | RBAC principal count, permit/deny decision stats |

### Architecture
```
dashboard_server.py  (Python backend)
   ├── collect_metrics()  — Runs DSTS pipeline with psutil instrumentation
   ├── ResourceSampler    — Background thread sampling CPU/mem/disk at 100ms intervals
   ├── PhaseTimer         — Context manager measuring wall/CPU/memory per phase
   └── DashboardHandler   — HTTP server serving:
       ├── GET /           → dashboard.html
       └── GET /api/metrics → JSON metrics payload

dashboard.html  (Frontend — zero external dependencies)
   ├── Custom Canvas 2D charts (no CDN libraries)
   ├── Glassmorphism dark-mode UI with Inter + JetBrains Mono typography
   └── Fetch /api/metrics → renders all cards, tables, and charts
```

---

## 🔍 Template Query Engine Specification

The template query engine ([`dsts/queries.py`](buildings_prototype/dsts/queries.py)) implements 6 core query and analytics types:

```python
from dsts.queries import QueryEngine, QueryType

engine = QueryEngine(events_log, state_tables, bsts_engine)

# Q1: Presence query after time t
res_q1 = engine.execute_query(QueryType.Q1_STAYED_AFTER_TIME, building_id="B1", time_t=10.0, role="QUERY_CLIENT")

# Q2: Visitor anomaly query at time t
res_q2 = engine.execute_query(QueryType.Q2_VISITOR_ANOMALY, building_id="B5", time_t=30.0, role="ADMIN")

# Q3: Departure query before time t
res_q3 = engine.execute_query(QueryType.Q3_LEFT_BEFORE_TIME, occupant_id="occ_01", building_id="B1", time_t=15.0, role="QUERY_CLIENT")

# Q5: Complete zone coverage query
res_q5 = engine.execute_query(QueryType.Q5_VISITED_ALL_ZONES, occupant_id="occ_01", building_id="B1", role="ADMIN")

# Q6: Exact location query at time t
res_q6 = engine.execute_query(QueryType.Q6_LOCATION_AT_TIME, occupant_id="occ_01", time_t=25.0, role="QUERY_CLIENT")

# TRACK: Role-based spatial analytics & trajectory dwell analysis
res_track = engine.execute_query(QueryType.TRACK, occupant_id="occ_01", caller_id="dean_vance")
# Discloses: room/sector dwell breakdown, most frequented room, longest stay room, and waypoints
```

---

## 📂 Repository Structure

All source code, tests, security modules, and monitoring tools are consolidated into a single self-contained directory:

```
Occupant_Tracking_v2/
├── buildings_prototype/                    # ← All code lives here
│   │
│   ├── 🏢 BUILDING EDGE NODES
│   │   ├── buildinglib/node.py            # Building node runtime & visitor pool
│   │   ├── buildinglib/enroll.py          # Occupant enrollment engine
│   │   ├── buildinglib/route.py           # LSH-based inter-building routing
│   │   ├── buildinglib/verify.py          # Cosine similarity vote verification
│   │   ├── nodelib/security_handler.py    # SecureBuildingNodeHandler (zero-trust integration)
│   │   ├── query_node.py                  # Query-side CLI (capture → route → handoff)
│   │   └── respond_node.py               # Respond-side CLI (capture → vote → reply)
│   │
│   ├── 🧠 DSTS STATE ENGINE
│   │   ├── dsts/bsts.py                   # Building Spatial-Temporal Sensing engine
│   │   ├── dsts/dsts.py                   # Distributed State Transition System coordinator
│   │   ├── dsts/state.py                  # Vectorised state probability table (Eq 2, 3, 6)
│   │   ├── dsts/transition.py             # Spatial transition kernel (Eq 9, 10)
│   │   ├── dsts/zones.py                  # Zone graph (z1..z8, z_T) & adjacency matrix
│   │   ├── dsts/events.py                 # RecognitionEvent & HLC definitions
│   │   ├── dsts/queries.py                # Template Query Engine (Q1, Q2, Q3, Q5, Q6)
│   │   └── dsts/evaluate.py              # State evaluation & DSTS pipeline
│   │
│   ├── 🔎 IDENTIFICATION & ROUTING
│   │   ├── identify/simhash.py            # SimHash feature extraction
│   │   ├── identify/bloom.py              # Bloom filter index construction
│   │   ├── identify/router.py             # Decentralized inter-building query router
│   │   ├── identify/gallery.py            # ArcFace embedding gallery manager
│   │   └── identify/normalize.py          # L2 normalization & mean subtraction
│   │
│   ├── 🔒 ZERO-TRUST SECURITY
│   │   ├── security/crypto.py             # X25519, HKDF-SHA256, AES-128-GCM, Ed25519
│   │   ├── security/transport.py          # Campus CA, X.509, mTLS, cert pinning, CRL
│   │   ├── security/metadata.py           # TransitionMetadata & SecureMetadataEnvelope
│   │   ├── security/replay_guard.py       # Nonce cache + 300s sliding window guard
│   │   ├── security/envelope.py           # Wire-level envelope framing & validation
│   │   ├── security/authorize.py          # Target-aware RBAC engine & authorization chokepoint
│   │   ├── security/campus_policy.py      # Layer-2 Campus Privacy Policy (Pairwise ReBAC)
│   │   └── security/user_privacy_protocol.py # Zero-Trust User Location Protocol & Privacy Gateway
│   │
│   ├── 📊 MONITORING & DASHBOARD
│   │   ├── monitoring/monitoring.py       # Prometheus metric collector & exporter
│   │   ├── monitoring/dashboard_server.py # Web dashboard backend (psutil instrumentation)
│   │   ├── monitoring/dashboard.html      # Dark-mode monitoring UI (custom Canvas charts)
│   │   ├── monitoring/prometheus.yml      # Prometheus scraper configuration
│   │   └── monitoring/grafana_dashboard.json # Grafana dashboard definition
│   │
│   ├── 🧪 SIMULATION & EVALUATION
│   │   ├── sim/scenario_b1_b5.py          # 40-event deterministic B1→B5 scenario (Seed 42)
│   │   ├── sim/event_generator.py         # Deterministic occupant trajectory simulator
│   │   ├── sim/evaluation.py              # Precision/Recall & Routing Gain evaluator
│   │   ├── sim/campus.py                  # Campus topology & occupant assignment
│   │   ├── sim/mobility.py                # Zone-level occupant mobility model
│   │   └── sim/report.py                  # Performance chart visualization generator
│   │
│   ├── 📖 EXECUTION & DEMO
│   │   ├── run_demo.py                    # 5-phase end-to-end demo runner
│   │   ├── query_user.py                  # Zero-Trust User-Seeking-Location CLI
│   │   └── test_queries_and_security.py   # 107-test comprehensive verification suite
│   │
│   ├── 🧪 TESTS
│   │   └── tests/
│   │       ├── test_integration.py        # 19 end-to-end integration tests
│   │       ├── test_queries.py            # 12 template query engine tests
│   │       ├── test_security.py           # 14 cryptographic security tests
│   │       ├── test_transport_security.py # 11 transport & mTLS tests
│   │       ├── test_secure_prototype.py   # 4 secure handler integration tests
│   │       ├── test_precision_queries.py  # 8 hierarchical location precision tests
│   │       ├── test_campus_policy.py      # 12 campus privacy policy & ReBAC tests
│   │       ├── test_target_awareness.py   # 8 target-aware RBAC & entity security tests
│   │       └── test_secure_user_query.py  # 10 zero-trust user location & persona privacy tests
│   │
│   ├── 📦 DATA & ARTIFACTS
│   │   ├── corpus/emb_arcface.npy         # ArcFace 512-d embeddings (500 identities)
│   │   ├── corpus/meta.csv                # Identity metadata (building assignment)
│   │   ├── shared/params.json             # Enrollment contract (seed=42, k, L, dim)
│   │   ├── shared/routing_params.json     # Routing thresholds & schema version
│   │   ├── nodes/                         # Per-building node artifacts (.npz)
│   │   ├── out/                           # Build output (manifests + compressed data)
│   │   └── reports/                       # Generated PNG evaluation charts
│   │
│   └── 📘 DOCUMENTATION
│       ├── README.md                      # Buildings prototype documentation
│       ├── PIPELINE.md                    # Detailed pipeline specification
│       ├── FLOW.md                        # Control flow documentation
│       └── RUNBOOK.md                     # Operations runbook
│
├── pytest.ini                              # Pytest configuration → buildings_prototype
├── requirements.txt                        # Python dependencies
├── README.md                               # ← This file
├── SYSTEM_BLUEPRINT.md                     # Architectural blueprint
├── SYSTEM_FLOW.md                          # System dataflow documentation
└── FILE_CATALOG.md                         # File catalog & responsibility index
```

---

## ⚡ Quick Start & Execution Guide

### 1. Environment Setup
Install dependencies listed in [`requirements.txt`](requirements.txt):
```bash
pip install -r requirements.txt
```

### 2. Execute Demonstration Benchmark
Run the end-to-end 5-phase pipeline (simulation → evaluation → charts → security → monitoring):
```bash
python buildings_prototype/run_demo.py
```

### 3. Launch Monitoring Dashboard
Start the web-based monitoring dashboard with real-time CPU, memory, security, and query metrics:
```bash
python buildings_prototype/monitoring/dashboard_server.py
# Open http://localhost:8050 in browser
```

### 4. Run Automated Test Suites
Execute all unit, integration, query, transport, and cryptographic security tests:

```bash
# Option A: Run 150-Test Comprehensive Verification Runner
python buildings_prototype/test_queries_and_security.py

# Option B: Run 179-Test Pytest Suite (auto-configured via pytest.ini)
python -m pytest
```

### 5. Query Individual Occupants (Edge Nodes)
Run specific occupant queries against the building prototype nodes:
```bash
python buildings_prototype/query_node.py --capture-row 12 --repeat 2
```

### 6. Zero-Trust User Seeking Location (5-Tier Disclosure & ABAC)
Execute zero-trust location queries between campus personas:
```bash
# Student seeking Teacher cabin availability (L1 Presence during office hours -> Cabin Availability only)
python buildings_prototype/query_user.py --caller student_alice --target-role teacher --query Q6

# Student seeking Teacher outside office hours (ABAC Context -> DENIED L0_NONE)
python buildings_prototype/query_user.py --caller student_alice --target-role teacher --query Q6 --outside-office-hours

# Teacher seeking Dean location (Administrative interaction -> L2_CURRENT_ZONE)
python buildings_prototype/query_user.py --caller prof_smith --caller-role teacher --target-role dean --query Q6

# Dean seeking Student routine location (Anti-mass surveillance -> Capped at L2_CURRENT_ZONE)
python buildings_prototype/query_user.py --caller dean_carter --caller-role dean --target-role student --query Q6

# Dean seeking Student trajectory with authorized investigation (Formal audit -> Discloses L4_HISTORICAL_TRACK)
python buildings_prototype/query_user.py --caller dean_carter --caller-role dean --target-role student --query Q5 --purpose security_investigation --authorized-investigation

# Dean seeking Student movement track & spatial analytics (Authorized L4 -> Room dwell breakdown & pattern metrics)
python buildings_prototype/query_user.py --caller dean_carter --caller-role dean --target-role student --query TRACK --purpose security_investigation --authorized-investigation

# Teacher seeking Student movement track (Clearance L2 -> Building & sector dwell times; room IDs redacted)
python buildings_prototype/query_user.py --caller prof_smith --caller-role teacher --target-role student --query TRACK

# Student seeking peer Student (Anti-stalking protection -> DENIED L0_NONE)
python buildings_prototype/query_user.py --caller student_alice --target student_bob --target-role student --query Q6

# Teacher seeking enrolled Student (Roster override -> L2_CURRENT_ZONE)
python buildings_prototype/query_user.py --caller prof_smith --caller-role teacher --target-role student --enrolled --query Q6
```

---

## 📑 Core Documentation Index

Detailed architectural specs and control flow documentation are maintained in dedicated Markdown manuals:

- 📘 **[SYSTEM_BLUEPRINT.md](SYSTEM_BLUEPRINT.md)** — Comprehensive architectural blueprint, edge vs system-wide breakdown, mathematical framework, and data matrix.
- 📘 **[FILE_CATALOG.md](FILE_CATALOG.md)** — File catalog detailing intro, responsibilities, input/output contracts, key classes, and major execution breakpoints.
- 📘 **[SYSTEM_FLOW.md](SYSTEM_FLOW.md)** — Detailed step-by-step trace of dataflow and control flow through the system.
- 📘 **[PIPELINE.md](buildings_prototype/PIPELINE.md)** — Full pipeline specification for the buildings prototype.
- 📘 **[FLOW.md](buildings_prototype/FLOW.md)** — Buildings prototype control flow documentation.
- 📘 **[RUNBOOK.md](buildings_prototype/RUNBOOK.md)** — Operations runbook for building, verifying, and querying nodes.

---

## 🔀 Branch & Release Status (`test_3`)

- [x] **Consolidated Architecture**: All modules consolidated into `buildings_prototype/` — security, DSTS, identification, simulation, monitoring, and tests.
- [x] **Deterministic Seed 42**: All scenario runners, simulations, and tests use Seed 42 for 100% reproducible results across team members.
- [x] **Comprehensive Test Coverage**: **329 Total Tests Passing 100%** (150 comprehensive runner + 179 pytest suite).
- [x] **Zero-Trust Security & Transport**: X25519 DH Key Exchange, HKDF-SHA256, AES-128-GCM, Ed25519, Campus CA, mTLS, Certificate Pinning, Anti-Replay Nonce Cache, and Target-Aware RBAC integrated.
- [x] **Contextual Privacy & ReBAC**: Pairwise dual-axis `(AccessScope, LocationGranularity)` policy (`none`, `presence`, `current`, `full_track`), self-query exemption, teacher class-roster override, teacher-to-dean availability (`current/zone`), and entity-level target awareness (`occupant:<id>`, `building:<id>`, `system`).
- [x] **Zero-Trust User-Seeking-Location Protocol**: End-to-end sealed envelope queries with ReplayGuard, Layer-1 RBAC authorization, Layer-2 Persona ReBAC, and hierarchical precision disclosure (`UserClient` & `SecureLocationQueryGateway`).
- [x] **Template Query Engine**: Paper queries Q1, Q2, Q3, Q5, Q6 implemented and verified with hierarchical precision and target-aware RBAC filtering.
- [x] **Monitoring Dashboard**: Web-based real-time dashboard with CPU load, memory usage, disk I/O, security overhead, and query latency visualizations.
- [x] **Loose Coupling & High Cohesion**: Modules interact via immutable data structures; each subsystem has a single, well-defined responsibility.

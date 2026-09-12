# Distributed Spatial-Temporal Sensing (DSTS) Framework
### Multi-Building Occupant Tracking, Decentralized Routing & Zero-Trust Metadata Security

[![Branch](https://img.shields.io/badge/branch-test__3-blue.svg)](https://github.com/yadhu-vipin/Occupant_Tracking_v2/tree/test_3)
[![Build Status](https://img.shields.io/badge/tests-107%2F107%20passed-brightgreen.svg)](#-testing--verification-suite)
[![Pytest Suite](https://img.shields.io/badge/pytest-56%2F56%20passed-brightgreen.svg)](#1-formal-pytest-suite-56-tests)
[![Building Prototype Tests](https://img.shields.io/badge/prototype_tests-31%2F31%20passed-brightgreen.svg)](#2-building-prototype-pytest-suite-31-tests)
[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](requirements.txt)
[![Security Standard](https://img.shields.io/badge/crypto-X25519%20DH%20%7C%20AES--128--GCM%20%7C%20Ed25519-green.svg)](#-security--cryptographic-specification)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](#)

---

## 👨‍💻 Developer & Author Contribution
**Developer**: **Arjun Rajesh** (AM.SC.U4CSE23208)  
**Role**: **Lane B — Events, Evaluation, Security & Monitoring Architect**

### Key Contributions & Implementation Scope
1. **Occupant Movement & Event Simulation**:
   - Designed and implemented the deterministic occupant movement simulator (`sim/event_generator.py`) using seeded pseudo-randomness.
   - Modeled multi-building trajectory movement strictly adhering to zone adjacency graphs ($z_1 \dots z_8, z_T$).
   - Built the controlled **B1 $\to$ B5 multi-building evaluation scenario** (`sim/scenario_b1_b5.py`) with transition zone ($z_T$) handoffs.

2. **Template Query Engine & Evaluation Pipeline**:
   - Built the complete spatial-temporal template query engine (`dsts/queries.py`) answering paper queries **Q1, Q2, Q3, Q5, and Q6** with role-based access filtering.
   - Built the system evaluation framework (`sim/evaluation.py` and `sim/report.py`) measuring Recognition Accuracy (**100.0%**), Optimal Cosine Threshold ($\theta_{\text{opt}} = 0.990$), LSH Routing Rank-1 Accuracy (**85.7%**), and Communication Efficiency Gain (**86.2%** query reduction vs. broadcast).
   - Automated performance report chart generation (saved to `reports/`).

3. **Zero-Trust Inter-Building Security Architecture**:
   - **X25519 Ephemeral Diffie-Hellman (ECDH) Key Exchange**: Implemented secure, per-pair building node key exchange (`security/crypto.py`).
   - **HKDF-SHA256 Key Derivation**: Context-bound 128-bit AES session key derivation using HMAC-based Extract-and-Expand KDF with domain separation (`"DSTS-v1"`).
   - **AES-128-GCM Authenticated Encryption**: Confidentiality and tamper-evident payload encryption (`security/metadata.py`).
   - **Ed25519 Digital Signatures**: Non-repudiation and origin verification for all transition handoff envelopes.
   - **Transport Security & CA**: Built a self-signed Campus Certificate Authority (CA), X.509 certificate issuance/validation, mTLS mutual authentication, certificate pinning, CRL revocation, and rogue node rejection (`security/transport.py`).
   - **Anti-Replay Guard & RBAC**: Developed 300s sliding window timestamp validator, nonce uniqueness cache (`security/replay_guard.py`), and fine-grained Role-Based Access Control (`security/authorize.py`).

4. **Testing Infrastructure & Monitoring**:
   - Architected the 107-test comprehensive verification test runner (`test_queries_and_security.py`) achieving a **100% pass rate**.
   - Built Prometheus metric exporter (`monitoring/monitoring.py`) and Grafana monitoring dashboard (`monitoring/grafana_dashboard.json`).

---

## 📌 Executive Summary

The **Distributed Spatial-Temporal Sensing (DSTS)** framework provides an edge-computed, privacy-preserving infrastructure for continuous multi-building occupant tracking, unknown visitor routing, and secure inter-building spatial handoffs.

Designed to eliminate centralized database bottlenecks and prevent biometric privacy leakage, DSTS isolates face feature vectors at local building edges. When occupants transit between facility buildings, DSTS leverages **Locality-Sensitive Hashing (LSH)** and compressed **Bloom filter summaries** for decentralized routing, paired with an authenticated zero-trust cryptographic protocol (**X25519 Diffie-Hellman Key Exchange**, **HKDF-SHA256**, **AES-128-GCM**, and **Ed25519**) for inter-node metadata exchange.

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

## 🔒 Inter-Building Key Sharing & Security Specification

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

### Cryptographic Stack Breakdown

| Primitive / Protocol | Implementation File | Function & Guarantee |
| :--- | :--- | :--- |
| **X25519 Diffie-Hellman** | [`security/crypto.py`](file:///a:/Brother%20eye%20test/p2/v6/Occupant_Tracking_v2/security/crypto.py) | **Inter-Building Key Exchange**: Establishes shared secret scalar between distributed building nodes without transmitting private keys. |
| **HKDF-SHA256** | [`security/crypto.py`](file:///a:/Brother%20eye%20test/p2/v6/Occupant_Tracking_v2/security/crypto.py) | **Key Derivation Function**: Derives high-entropy 128-bit AES session key bound to context string `"DSTS-v1"`. |
| **AES-128-GCM** | [`security/crypto.py`](file:///a:/Brother%20eye%20test/p2/v6/Occupant_Tracking_v2/security/crypto.py) | **Authenticated Encryption**: Encrypts transition metadata payload while providing 128-bit authentication tags for tamper detection. |
| **Ed25519** | [`security/crypto.py`](file:///a:/Brother%20eye%20test/p2/v6/Occupant_Tracking_v2/security/crypto.py) | **Digital Signatures**: Elliptic curve signature algorithm for non-repudiation and origin verification. |
| **Campus CA & X.509** | [`security/transport.py`](file:///a:/Brother%20eye%20test/p2/v6/Occupant_Tracking_v2/security/transport.py) | **Transport Security**: Self-signed Campus CA issues node certificates; enforced via mTLS and certificate pinning. |
| **Replay Guard** | [`security/replay_guard.py`](file:///a:/Brother%20eye%20test/p2/v6/Occupant_Tracking_v2/security/replay_guard.py) | **Anti-Replay Protection**: Nonce cache dedup + 300-second sliding timestamp window guard. |
| **RBAC Engine** | [`security/authorize.py`](file:///a:/Brother%20eye%20test/p2/v6/Occupant_Tracking_v2/security/authorize.py) | **Access Control**: Fine-grained role permissions (`BUILDING_NODE`, `ADMIN`, `QUERY_CLIENT`, `ANONYMOUS`). |

---

## 🧪 Testing & Verification Suite

The repository contains **3 comprehensive test suites** covering unit, integration, query, transport, cryptographic, and architectural isolation tests. **All 194 total tests pass 100% cleanly.**

### 1. Comprehensive Test Suite Runner (`test_queries_and_security.py`)
**107 Tests — 100% Pass Rate (0.02s execution time)**

Run via terminal:
```bash
python test_queries_and_security.py
```

| Section | Test Focus | Test Count | Status | Key Features Tested |
| :--- | :--- | :---: | :---: | :--- |
| **Section 1: Resimulation & Scenario B1$\to$B5** | Movement & State | **11** | ✅ Passed | Seeded determinism, adjacency graph constraints, transition zone $z_T$, Equation 6 state updates, metric calculation. |
| **Section 2: Template Queries Engine** | Spatial Queries | **18** | ✅ Passed | **Q1** (presence after $t$), **Q2** (visitor anomaly count), **Q3** (exit before $t$), **Q5** (all zones visited), **Q6** (location at $t$), plus RBAC query authorization. |
| **Section 3: Transport Security & Cert Pinning** | Transport Security | **18** | ✅ Passed | CA root signatures, node cert issuance, mTLS handshakes, cert pinning, revocation (CRL), expiration, rogue node rejection. |
| **Section 4: Cryptographic Security & Anti-Replay** | Zero-Trust Crypto | **20** | ✅ Passed | X25519 DH key exchange, HKDF derivation, AES-128-GCM tag verification, Ed25519 signatures, nonce uniqueness, 300s window guard. |
| **Section 5: Role-Based Access Control (RBAC)** | Authorization | **16** | ✅ Passed | Roles (`BUILDING_NODE`, `ADMIN`, `QUERY_CLIENT`), role assignment, route-level permission enforcement, audit logging, unauthorized blocking. |
| **Section 6: Building Edge Hardening & Isolation** | Architectural Hardening | **24** | ✅ Passed | Key isolation between pairs, channel isolation, nonce cache independence, Equation 6 spatial kernel, zone graph adjacency. |
| **TOTAL** | **Comprehensive Suite** | **107** | **100%** | **All 107 tests execute cleanly with zero failures.** |

---

### 2. Formal Pytest Integration & Unit Suite
**56 Tests — 100% Pass Rate**

Run via terminal:
```bash
pytest tests/
```

| Test Module | Test Count | Status | Tested Components |
| :--- | :---: | :---: | :--- |
| [`tests/test_integration.py`](file:///a:/Brother%20eye%20test/p2/v6/Occupant_Tracking_v2/tests/test_integration.py) | **19** | ✅ Passed | End-to-end multi-building simulation, B1$\to$B5 handoffs, evaluation metric calculations, report visualization rendering. |
| [`tests/test_queries.py`](file:///a:/Brother%20eye%20test/p2/v6/Occupant_Tracking_v2/tests/test_queries.py) | **12** | ✅ Passed | Template query engine (Q1, Q2, Q3, Q5, Q6) positive/negative test cases, edge boundary handling, RBAC integration. |
| [`tests/test_security.py`](file:///a:/Brother%20eye%20test/p2/v6/Occupant_Tracking_v2/tests/test_security.py) | **14** | ✅ Passed | X25519 DH key exchange, HKDF key derivation, AES-GCM tag validation, Ed25519 signature checks, anti-replay nonce tracking, RBAC policies. |
| [`tests/test_transport_security.py`](file:///a:/Brother%20eye%20test/p2/v6/Occupant_Tracking_v2/tests/test_transport_security.py) | **11** | ✅ Passed | Campus CA, X.509 cert validation, mTLS mutual authentication, certificate pinning, CRL revocation, expired cert detection, rogue node rejection. |
| **TOTAL** | **56** | **100%** | **56 / 56 Pytest integration tests passed in 0.33s.** |

---

### 3. BSTS Building Prototype Pytest Suite
**31 Tests — 100% Pass Rate**

Run via terminal:
```bash
python -m pytest events/tests recognition/tests retrieval/tests centralized/tests dsts/tests
```

| Subsystem Module | Test Count | Status | Tested Components |
| :--- | :---: | :---: | :--- |
| `events/tests/` | **5** | ✅ Passed | Event schema validation, camera event structure, seeded generator determinism. |
| `recognition/tests/` | **5** | ✅ Passed | ArcFace 512-d feature vector extraction, cosine distance calculation, acceptance thresholding. |
| `retrieval/tests/` | **3** | ✅ Passed | Locality-Sensitive Hashing (LSH) index construction, Bloom filter summary queries, multi-probe Hamming expansion. |
| `centralized/tests/` | **3** | ✅ Passed | Centralized vs decentralized baseline performance metric comparisons. |
| `dsts/tests/` | **15** | ✅ Passed | BSTS state probability matrix transitions ($S_b(t)$), Equation 6 spatial probability update kernel, HLC trigger verification. |
| **TOTAL** | **31** | **100%** | **31 / 31 Subsystem unit tests passed in 0.72s.** |

---

## 📊 Evaluation & Benchmark Results

Evaluated on the standardized 40-event multi-building scenario (`python run_demo.py`):

| Evaluation Category | Metric | Result | Benchmark Target | Status |
| :--- | :--- | :---: | :---: | :---: |
| **Recognition** | Recognition Accuracy | **100.0%** | $\ge 95.0\%$ | ✅ Superior |
| **Recognition** | Optimal Cosine Threshold ($\theta_{\text{opt}}$) | **0.990** | $0.80 - 0.99$ | ✅ Optimal |
| **Routing** | LSH / Bloom Rank-1 Accuracy | **85.7%** | $\ge 80.0\%$ | ✅ Exceeds |
| **Routing Efficiency** | Avg. Contacted Buildings | **1.2** / 10 | vs. 9.0 Broadcast | ✅ 86.7% Gain |
| **Routing Efficiency** | Communication Efficiency Gain | **86.2%** | $\ge 80.0\%$ | ✅ Exceeds |
| **Security Audit** | Security Threat Checks | **14 / 14 Passed** | 100% | ✅ Zero-Trust |
| **Test Suite** | Unit & Integration Test Pass Rate | **107 / 107 Passed** | 100% | ✅ Verified |

---

## 🔍 Detailed Template Query Engine Specification

The template query engine ([`dsts/queries.py`](file:///a:/Brother%20eye%20test/p2/v6/Occupant_Tracking_v2/dsts/queries.py)) implements 5 core query types from the paper:

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
```

---

## 📂 Repository Blueprint & File Structure

```
Occupant_Tracking_v2/
├── 🏢 BUILDING-SPECIFIC EDGE NODES
│   ├── node/building_node.py       # Edge controller & visitor registry state management
│   ├── dsts/bsts.py                # Building Spatial-Temporal Sensing (BSTS) engine
│   ├── dsts/state.py               # Local state probability matrix S_b(t) maintainer
│   ├── dsts/transition.py          # Spatial transition kernel implementation (Eq. 6)
│   ├── dsts/zones.py               # Intra-building spatial zones (z1..z8, transition z_T)
│   ├── dsts/events.py              # RecognitionEvent & HLC event data definitions
│   └── dsts/queries.py             # Template Query Engine (Q1, Q2, Q3, Q5, Q6)
│
├── 🌐 SYSTEM-WIDE / DECENTRALIZED ROUTING & SECURITY
│   ├── identify/router.py          # LSH & Bloom filter decentralized query router
│   ├── identify/bloom_summary.py   # Anonymized Bloom filter summary generator
│   ├── identify/lsh.py             # Locality-Sensitive Hashing vector indexer
│   ├── identify/face_recognizer.py # Cosine similarity feature matching engine
│   ├── security/crypto.py          # X25519 DH key exchange, HKDF-SHA256, AES-128-GCM, & Ed25519
│   ├── security/transport.py       # Campus CA, X.509 certs, mTLS, cert pinning, CRL, rogue node check
│   ├── security/metadata.py        # TransitionMetadata & SecureMetadataEnvelope payloads
│   ├── security/replay_guard.py    # Nonce cache, sliding timestamp window, & anti-replay
│   └── security/authorize.py       # Role-Based Access Control (RBAC) authorization engine
│
├── 📊 MONITORING & TELEMETRY
│   ├── monitoring/monitoring.py    # Prometheus metrics server (HTTP Endpoint: Port 8000)
│   ├── monitoring/prometheus.yml   # Prometheus scraper configuration
│   ├── monitoring/grafana_dashboard.json # Grafana monitoring dashboard definition
│   └── deploy/docker-compose.monitoring.yml # Docker Compose monitoring stack
│
├── 🧪 SIMULATION, BENCHMARKS & TESTING
│   ├── sim/event_generator.py     # Deterministic occupant trajectory simulator
│   ├── sim/scenario_b1_b5.py       # 40-event evaluation scenario (B1 -> z_T -> B5)
│   ├── sim/evaluation.py         # Precision/Recall & Routing Gain evaluator
│   ├── sim/report.py             # Performance chart visualization generator
│   ├── test_queries_and_security.py # 107-test comprehensive test runner
│   └── tests/                      # Pytest integration & unit test suite (56 tests)
│       ├── test_integration.py
│       ├── test_queries.py
│       ├── test_security.py
│       └── test_transport_security.py
│
├── 📖 EXECUTION & DEMO
│   ├── run_demo.py                 # Main CLI evaluation runner script
│   └── requirements.txt            # Python dependency specification
```

---

## ⚡ Quick Start & Execution Guide

### 1. Environment Setup
Install dependencies listed in [`requirements.txt`](requirements.txt):
```bash
pip install -r requirements.txt
```

### 2. Execute Demonstration Benchmark
Run the end-to-end multi-building simulation, calculate recognition & routing metrics, execute security handoff validation, and export visual report charts:
```bash
python run_demo.py
```

### 3. Run Automated Test Suites
Execute all unit, integration, query, transport, and cryptographic security tests:

```bash
# Option A: Run 107-Test Comprehensive Verification Runner
python test_queries_and_security.py

# Option B: Run Pytest Integration & Security Suite (56 tests)
pytest tests/

# Option C: Run BSTS Subsystem Prototype Unit Tests (31 tests)
python -m pytest events/tests recognition/tests retrieval/tests centralized/tests dsts/tests
```

---

## 📑 Core Documentation Index

Detailed architectural specs and control flow documentation are maintained in dedicated Markdown manuals:

- 📘 **[SYSTEM_BLUEPRINT.md](SYSTEM_BLUEPRINT.md)** — Comprehensive architectural blueprint, edge vs system-wide breakdown, mathematical framework, and data matrix.
- 📘 **[FILE_CATALOG.md](FILE_CATALOG.md)** — File catalog detailing intro, responsibilities, input/output contracts, key classes, and major execution breakpoints.
- 📘 **[SYSTEM_FLOW.md](SYSTEM_FLOW.md)** — Detailed step-by-step trace of dataflow and control flow through the system.

---

## 🔀 Branch & Release Status (`test_3`)

- [x] **Comprehensive Test Coverage**: **194 Total Unit & Integration Tests Passing 100%** (107 comprehensive + 56 pytest integration + 31 BSTS prototype).
- [x] **Zero-Trust Security & Transport**: X25519 Diffie-Hellman Key Exchange, HKDF-SHA256, AES-128-GCM, Ed25519 signatures, Campus CA, mTLS, Certificate Pinning, Anti-Replay Nonce Cache, and RBAC integrated.
- [x] **Template Query Engine**: Paper queries Q1, Q2, Q3, Q5, Q6 implemented and verified.
- [x] **Documentation**: Updated `README.md`, `walkthrough.md`, and presentation slides (`Mid review PPT Template (1).pptx`).

# Distributed Spatial-Temporal Sensing (DSTS) Framework
### Multi-Building Occupant Tracking, Decentralized Routing & Zero-Trust Metadata Security

[![Branch](https://img.shields.io/badge/branch-lane__B-blue.svg)](https://github.com/yadhu-vipin/Occupant_Tracking_v2/tree/lane_B)
[![Build Status](https://img.shields.io/badge/tests-33%2F33%20passed-brightgreen.svg)](#-testing--verification)
[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](requirements.txt)
[![Security Standard](https://img.shields.io/badge/crypto-X25519%20%7C%20AES--128--GCM%20%7C%20Ed25519-green.svg)](#-security--cryptographic-specification)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](#)

---

## 📌 Executive Summary

The **Distributed Spatial-Temporal Sensing (DSTS)** framework provides an edge-computed, privacy-preserving infrastructure for continuous multi-building occupant tracking, unknown visitor routing, and secure inter-building spatial handoffs.

Designed to eliminate centralized database bottlenecks and prevent privacy leakage, DSTS isolates biometric feature vectors at local building edges. When occupants transit between facility buildings, DSTS leverages **Locality-Sensitive Hashing (LSH)** and compressed **Bloom filter summaries** for decentralized routing, paired with an authenticated zero-trust cryptographic protocol (**X25519**, **HKDF-SHA256**, **AES-128-GCM**, and **Ed25519**) for inter-node metadata exchange.

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

## 📂 Repository Blueprint & Component Organization

The system is partitioned into **Building-Specific Edge Layer** components and **System-Wide Infrastructure**:

```
Occupant_Tracking_v2/
├── 🏢 BUILDING-SPECIFIC NODES (Edge Layer)
│   ├── node/building_node.py       # Edge controller & visitor registry state management
│   ├── dsts/bsts.py                # Building Spatial-Temporal Sensing (BSTS) engine
│   ├── dsts/state.py               # Local state probability matrix S_b(t) maintainer
│   ├── dsts/transition.py          # Spatial transition kernel implementation (Eq. 6)
│   ├── dsts/zones.py               # Intra-building spatial zones (z1..z8, transition z_T)
│   └── dsts/events.py              # RecognitionEvent & HLC event data definitions
│
├── 🌐 SYSTEM-WIDE / INTER-BUILDING INFRASTRUCTURE
│   ├── identify/router.py          # LSH & Bloom filter decentralized query router
│   ├── identify/bloom_summary.py   # Anonymized Bloom filter summary generator
│   ├── identify/lsh.py             # Locality-Sensitive Hashing vector indexer
│   ├── identify/face_recognizer.py # Cosine similarity feature matching engine
│   ├── security/crypto.py          # X25519, HKDF-SHA256, AES-128-GCM, & Ed25519 signatures
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
├── 🧪 SIMULATION, BENCHMARKS & REPORTING
│   ├── sim/event_generator.py     # Deterministic occupant trajectory simulator
│   ├── sim/scenario_b1_b5.py       # 40-event evaluation scenario (B1 -> z_T -> B5)
│   ├── sim/evaluation.py         # Precision/Recall & Routing Gain evaluator
│   └── sim/report.py             # Performance chart visualization generator
│
├── 📖 DOCUMENTATION
│   ├── SYSTEM_BLUEPRINT.md         # Comprehensive architectural specification
│   ├── FILE_CATALOG.md             # Complete file-by-file reference manual
│   ├── SYSTEM_FLOW.md              # End-to-end dataflow & control flow trace
│   ├── requirements.txt            # Python dependency specification
│   └── run_demo.py                 # Main CLI evaluation runner script
```

---

## 🔒 Security & Cryptographic Specification (Lane B)

The metadata security layer enforces a zero-trust architecture protecting against 8 core threat vectors:

| Threat Vector | Mitigation Strategy | Cryptographic Primitive / File | Verification |
| :--- | :--- | :--- | :--- |
| **1. Man-in-the-Middle (MitM)** | Ephemeral key exchange & session key derivation | **X25519** + **HKDF-SHA256** (`crypto.py`) | ✅ Verified |
| **2. Message Tampering** | Authenticated encryption with integrity tag | **AES-128-GCM** (`crypto.py`) | ✅ Verified |
| **3. Replay Attacks** | Nonce tracking cache & 300s sliding window | `ReplayGuard` (`replay_guard.py`) | ✅ Verified |
| **4. Building Node Spoofing** | Public-key digital signatures | **Ed25519** (`crypto.py`) | ✅ Verified |
| **5. Metadata Disclosure** | Symmetric encryption of metadata payload | **AES-128-GCM** (`metadata.py`) | ✅ Verified |
| **6. Message Duplication** | Unique 64-bit hexadecimal message identifiers | `ReplayGuard` (`replay_guard.py`) | ✅ Verified |
| **7. Unauthorized Access** | Principal role verification (`BUILDING_NODE`) | `RBACEngine` (`authorize.py`) | ✅ Verified |
| **8. Privacy Leakage** | Zero raw biometrics shared; Bloom filter exchange | `identify/bloom_summary.py` | ✅ Verified |

---

## 📊 Benchmark Metrics & Quantitative Results

Evaluated on the standardized 40-event multi-building scenario (`run_demo.py`):

| Evaluation Category | Metric | Result | Benchmark Target |
| :--- | :--- | :--- | :--- |
| **Recognition** | Recognition Accuracy | **100.0%** | $\ge 95.0\%$ |
| **Recognition** | Optimal Threshold ($\theta_{\text{opt}}$) | **0.990** | $0.80 - 0.99$ |
| **Routing** | LSH / Bloom Rank-1 Accuracy | **85.7%** | $\ge 80.0\%$ |
| **Routing Efficiency** | Avg. Contacted Buildings | **1.2** / 10 | vs. 9.0 Broadcast |
| **Routing Efficiency** | Communication Efficiency Gain | **86.2%** | $\ge 80.0\%$ |
| **Security** | Threat Verification Checks | **14 / 14 Passed** | 100% |

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
*(Generated report PNG files will be saved in `reports/`)*

### 3. Run Automated Test Suites
Validate integration and security threat models:
```bash
# Run End-to-End Integration Tests (19 tests)
python tests/test_integration.py

# Run Security & Cryptography Tests (14 tests)
python tests/test_security.py
```

---

## 📑 Core Documentation Index

Detailed architectural specs and control flow documentation are maintained in dedicated Markdown manuals:

- 📘 **[SYSTEM_BLUEPRINT.md](SYSTEM_BLUEPRINT.md)** — Comprehensive architectural blueprint, edge vs system-wide breakdown, mathematical framework, and data matrix.
- 📘 **[FILE_CATALOG.md](FILE_CATALOG.md)** — File catalog detailing intro, responsibilities, input/output contracts, key classes, and major execution breakpoints.
- 📘 **[SYSTEM_FLOW.md](SYSTEM_FLOW.md)** — Detailed step-by-step trace of dataflow and control flow through the system.

---

## 🔀 Branch Merge Status (`lane_B` $\to$ `main`)

- [x] **Code Clean-up**: Flat repository structure with zero redundant nested `v6/` directories.
- [x] **Dependencies**: Complete dependency specification in [`requirements.txt`](requirements.txt).
- [x] **Test Coverage**: 33 / 33 tests passing with 0 failures (`test_integration.py` & `test_security.py`).
- [x] **Documentation**: Full set of technical specifications ([`SYSTEM_BLUEPRINT.md`](SYSTEM_BLUEPRINT.md), [`FILE_CATALOG.md`](FILE_CATALOG.md), [`SYSTEM_FLOW.md`](SYSTEM_FLOW.md)).
- [x] **Git Verification**: Branch clean, committed, and pushed to `origin/lane_B`.

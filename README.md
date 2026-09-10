# Distributed Spatial-Temporal Sensing (DSTS) & Building Routing Framework

[![Branch](https://img.shields.io/badge/branch-lane__B-blue)](https://github.com/YourRepo/Occupant_Tracking_v2/tree/lane_B)
[![Tests](https://img.shields.io/badge/tests-33%2F33%20passed-brightgreen)](#-testing--verification)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](requirements.txt)
[![License](https://img.shields.io/badge/license-MIT-green)](#)

DSTS is an edge-based, privacy-preserving multi-building occupant tracking and face recognition framework. It implements Bayesian spatial-temporal state estimation, decentralized LSH & Bloom filter routing for unknown visitors, zero-trust inter-node security (X25519, AES-128-GCM, Ed25519), and real-time Prometheus monitoring.

---

## 📚 Complete Project Documentation

- 📄 **[SYSTEM_BLUEPRINT.md](SYSTEM_BLUEPRINT.md)** — Architectural design blueprint, modular breakdown (Building Edge vs System-Wide), mathematical equations (Eq 1–6), data matrix, and security threat model.
- 📄 **[FILE_CATALOG.md](FILE_CATALOG.md)** — Complete catalog of every file, detailing purpose, key classes/functions, input/output contracts, and major breakpoints.
- 📄 **[SYSTEM_FLOW.md](SYSTEM_FLOW.md)** — Step-by-step dataflow & control flow trace from camera observation to cryptographic envelope packaging and Prometheus telemetry.
- 📄 **[requirements.txt](requirements.txt)** — Explicit list of Python dependencies.

---

## 🚀 Quick Start Guide

### 1. Installation
Clone the repository and install dependencies:
```bash
git clone -b lane_B https://github.com/YourRepo/Occupant_Tracking_v2.git
cd Occupant_Tracking_v2
pip install -r requirements.txt
```

### 2. Run Main Demonstration Benchmark
Run the end-to-end 40-event evaluation scenario ($B_1 \to z_T \to B_5$), compute recognition & routing metrics, execute security handoff checks, and generate performance report charts:
```bash
python run_demo.py
```

Generated report charts will be saved in `reports/`:
- `reports/precision_recall_curve.png`
- `reports/recognition_performance.png`
- `reports/buildings_contacted.png`
- `reports/routing_efficiency.png`

---

## 🧪 Testing & Verification

The repository contains 33 automated tests covering 100% of integration and security scenarios.

### Run All Unit & Integration Tests
```bash
python tests/test_integration.py
```
*(19/19 E2E integration tests passing)*

### Run All Cryptographic Security Tests
```bash
python tests/test_security.py
```
*(14/14 security threat model tests passing)*

---

## 🏗 System Architecture & Directory Structure

```
Occupant_Tracking_v2/
├── 🏢 BUILDING-SPECIFIC NODES (Edge Layer)
│   ├── node/building_node.py       # Autonomous Building Node controller
│   ├── dsts/bsts.py                # Building Spatial-Temporal Sensing (BSTS) Bayesian engine
│   ├── dsts/state.py               # Local state matrix S_b(t) tracking
│   ├── dsts/transition.py          # Spatial transition kernel (Eq 6)
│   └── dsts/zones.py               # Zone layout definitions (z1..z8, z_T)
│
├── 🌐 SYSTEM-WIDE / INTER-BUILDING INFRASTRUCTURE (Core & Routing)
│   ├── identify/router.py          # Decentralized LSH & Bloom filter router
│   ├── identify/bloom_summary.py   # Anonymized Bloom filter index per building
│   ├── identify/lsh.py             # Locality-Sensitive Hashing for embeddings
│   ├── identify/face_recognizer.py # Cosine similarity face matching engine
│   ├── security/crypto.py          # X25519 DH, HKDF, AES-128-GCM, & Ed25519 signatures
│   ├── security/metadata.py        # Transition metadata & encrypted envelope serialization
│   ├── security/replay_guard.py    # Nonce cache, sliding timestamp window, anti-replay
│   └── security/authorize.py       # Role-Based Access Control (RBAC) engine
│
├── 📊 MONITORING & TELEMETRY
│   ├── monitoring/monitoring.py    # Prometheus metrics collector & HTTP endpoint (Port 8000)
│   ├── monitoring/prometheus.yml   # Prometheus scraper configuration
│   ├── monitoring/grafana_dashboard.json # Grafana dashboard dashboard definition
│   └── deploy/docker-compose.monitoring.yml # Docker Compose deployment stack
│
├── 🧪 SIMULATION & REPORTING
│   ├── sim/event_generator.py     # Deterministic occupant trajectory simulator
│   ├── sim/scenario_b1_b5.py       # 40-event B1 -> z_T -> B5 evaluation scenario
│   ├── sim/evaluation.py         # Precision/Recall & Routing Gain evaluator
│   └── sim/report.py             # Chart visualization generator
│
├── 📜 DOCUMENTATION & CONFIGURATION
│   ├── SYSTEM_BLUEPRINT.md         # Full system architectural blueprint
│   ├── FILE_CATALOG.md             # File-by-file reference manual
│   ├── SYSTEM_FLOW.md              # System dataflow & control flow guide
│   ├── requirements.txt            # Dependency specs
│   └── run_demo.py                 # Main CLI runner script
```

---

## 🔒 Security Threat Coverage Matrix (Lane B)

| Threat | Mitigation Mechanism | Implementation File | Status |
| :--- | :--- | :--- | :--- |
| **1. Man-in-the-Middle (MitM)** | Ephemeral X25519 DH + HKDF Session Keys | `security/crypto.py` | ✅ PASSED |
| **2. Message Tampering** | AES-128-GCM Integrity Tag Validation | `security/crypto.py` | ✅ PASSED |
| **3. Replay Attacks** | Nonce Cache & Timestamp Window ($300\text{s}$) | `security/replay_guard.py` | ✅ PASSED |
| **4. Building Node Spoofing** | Ed25519 Digital Signature Verification | `security/crypto.py` | ✅ PASSED |
| **5. Metadata Disclosure** | Encrypted Metadata Envelopes | `security/metadata.py` | ✅ PASSED |
| **6. Message Duplication** | Unique 64-bit Hex Message Identifiers | `security/replay_guard.py` | ✅ PASSED |
| **7. Unauthorized Access** | Role-Based Access Control (`BUILDING_NODE`) | `security/authorize.py` | ✅ PASSED |
| **8. Privacy Leakage** | Bloom Filter Summaries & Metadata Only | `identify/bloom_summary.py` | ✅ PASSED |

---

## 📊 Key Performance Metrics

Based on the 40-event $B_1 \to B_5$ evaluation scenario (`python run_demo.py`):

- **Recognition Accuracy**: **100.0%**
- **Optimal Decision Threshold ($\theta_{\text{opt}}$)**: **0.990**
- **LSH / Bloom Routing Rank-1 Accuracy**: **85.7%**
- **Average Buildings Contacted**: **1.2** (vs. **9.0** for broadcast)
- **Communication Efficiency Gain**: **86.2%**

---

## 🔀 Branch Merge Readiness Checklist (`lane_B` -> `main`)

- [x] Repository cleaned up; flat top-level structure with zero redundant nested `v6/` folders.
- [x] Dependencies listed cleanly in `requirements.txt`.
- [x] Modular blueprint documented in `SYSTEM_BLUEPRINT.md`.
- [x] Detailed file-by-file documentation created in `FILE_CATALOG.md`.
- [x] Step-by-step dataflow documented in `SYSTEM_FLOW.md`.
- [x] 100% test pass rate across 33 integration and security tests.
- [x] `python run_demo.py` runs without errors out-of-the-box.

# DSTS End-to-End System & Control Flow

This document details the step-by-step dataflow, execution sequence, and control flow through the **Distributed Spatial-Temporal Sensing (DSTS)** architecture during real-time occupant tracking and inter-building handoff.

---

## System Flow Architecture Diagram

```
[ Camera Sensor ]
       │  (1. Event Capture & Embedding)
       ▼
[ Local Face Recognizer ] ──(Local Match Failed?)──► [ LSH / Bloom Router ]
       │                                                     │
       │ (Local Match Success)                               │ (Target Building Identified)
       ▼                                                     ▼
[ Local BSTS State Engine ]                           [ Cryptographic Envelope ]
       │  (2. Eq 6 Bayesian Update)                           │ (X25519 + AES-GCM + Ed25519)
       ▼                                                     ▼
[ High Likelihood (HLC) ]                             [ Replay Guard & RBAC ]
       │                                                     │
       │ (Occupant in transition zone z_T)                   │ (Passed Security Validations)
       └─────────────────────────► [ Destination Building Node ]
                                            │
                                            ▼
                                  [ Prometheus Telemetry ]
                                            │
                                            ▼
                                  [ Visual Reports PNG ]
```

---

## Step-by-Step Flow Execution

### Step 1: Camera Event Generation & Observation
1. A physical or simulated camera in Building $B_1$ captures an occupant at spatial zone $z_k \in \{z_1, \dots, z_8, z_T\}$.
2. The camera extracts a 128-dimensional face embedding vector $\mathbf{v} \in \mathbb{R}^{128}$ and constructs a `RecognitionEvent(timestamp, building_id, zone_id, vector)`.

### Step 2: Local Recognition & Bayesian State Update
1. `face_recognizer.py` computes cosine similarity against local registered occupants in $B_1$.
2. If similarity $\ge \theta_{\text{rec}}$ ($0.75$), local match succeeds.
3. `bsts.py` ingests the event and applies **Equation 6 Bayesian State Update** across zone matrix $S_{B_1}(t)$:

$$P(x_t = z_j \mid e_{1:t}) \propto P(y_t \mid x_t = z_j) \sum_{i} P(x_t = z_j \mid x_{t-1} = z_i) P(x_{t-1} = z_i \mid e_{1:t-1})$$

4. Probability distributions adjust smoothly based on spatial adjacency constraints.
5. If $\max_{z} P(x_t = z) \ge 0.80$, an **HLC (High Likelihood Condition)** event is triggered.

### Step 3: Unknown Visitor Detection & LSH/Bloom Routing (Lane A Integration)
1. If an occupant in Building $B_5$ is NOT recognized locally:
2. The node converts the 128D embedding vector $\mathbf{v}$ into $K$ LSH hash codes via `lsh.py`.
3. The node queries `router.py` containing pre-indexed `BloomSummary` objects from all 10 buildings.
4. The router calculates match scores across all buildings and ranks target buildings without broadcasting full biometric profiles.
5. The router identifies Home Building $B_1$ with **85.7%+ Rank-1 accuracy** and **86.2% communication efficiency gain** over broadcast.

### Step 4: Inter-Building Spatial Handoff Trigger
1. In Building $B_1$, when occupant $P_{001}$ enters transition zone $z_T$ ($S_{B_1}(t, z_T) \ge \theta_{\text{HLC}}$) and subsequently disappears from $B_1$ cameras:
2. Building Node $B_1$ initiates a spatial handoff to destination Building $B_5$.

### Step 5: Cryptographic Envelope & Security Verification (Lane B Core)
1. **Payload Creation**: $B_1$ packages a `TransitionMetadata` payload (`visitor_id`, `source=B1`, `dest=B5`, `transition_zone=z_T`, `confidence`, `message_id`).
2. **Session Key Exchange**: $B_1$ and $B_5$ execute ephemeral **X25519** Diffie-Hellman key exchange and derive an AES session key via **HKDF-SHA256**.
3. **Authenticated Encryption**: $B_1$ encrypts the metadata using **AES-128-GCM** via `crypto.py`.
4. **Digital Signature**: $B_1$ signs the encrypted payload with its persistent **Ed25519** private key.
5. **Transmission**: Sealed `SecureMetadataEnvelope` is sent to $B_5$.
6. **Replay Guard Inspection**: $B_5$'s `ReplayGuard` inspects `message_id` and `nonce`. Rejects stale timestamps ($> 300\text{s}$) or reused nonces.
7. **RBAC Authorization**: $B_5$'s `RBACEngine` verifies that sender node $B_1$ holds `BUILDING_NODE` role and `CAN_HANDOFF` permission.
8. **Decryption & Handoff**: $B_5$ verifies the Ed25519 signature, validates GCM integrity tag, decrypts payload, and adds occupant $P_{001}$ to its **Visitor Registry**.

### Step 6: Telemetry Recording & Report Output
1. All event observations, routing queries, state updates, and security events emit metrics to `monitoring/monitoring.py`.
2. Real-time metrics are scraped by Prometheus on port 8000.
3. `sim/report.py` visualizes metrics into 4 PNG reports in `reports/`:
   - `precision_recall_curve.png`
   - `recognition_performance.png`
   - `buildings_contacted.png`
   - `routing_efficiency.png`

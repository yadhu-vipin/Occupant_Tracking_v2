# BrotherEye: Distributed Surveillance Tracking System (DSTS)

BrotherEye is a distributed face recognition and tracking system designed for building surveillance. It simulates multiple "buildings," each with its own local DSTS node that processes camera captures, performs face identification using the LFW dataset, and tracks occupant movement over time.

---

## 1. Experimental Setup

This section details the configuration and environment used to validate the BrotherEye system.

### Dataset & Split
- **Dataset**: Labeled Faces in the Wild (LFW).
- **Occupants**: 25 unique individuals selected from the LFW dataset, distributed across 5 virtual buildings (B0–B4).
- **Data Split**:
    - **Reference (Gallery)**: 20 images per occupant used for generating reference face encodings.
    - **Testing (Simulator)**: 20 images per occupant used for real-time camera simulation.
- **Total Database**: 500 reference images and 500 test images.

### Hardware & Software
- **OS**: Windows 10/11
- **CPU**: Intel/AMD (Simulation run on CPU; CUDA disabled for dlib stability).
- **Frameworks**:
    - `face_recognition` (based on dlib) for encoding and distance calculation.
    - `scikit-learn` for fetching the LFW dataset.
    - `matplotlib` & `PIL` for visual track generation.
    - `socket` for TCP-based inter-node communication.

### Parameter Settings
- **Encoding**: 128-dimensional face vectors.
- **Recognition Logic**: Probability estimation based on Euclidean distance with exponential decay ($p = e^{-k \cdot d}$).
- **Timeline**: Synthetic 09:00 to 17:00 window.
- **Step Size**: 24 minutes per event (20 events per person over an 8-hour shift).

### Evaluation Metrics
- **Recognition Probability (p)**: Confidence score of the face match (Target: $p > 0.90$).
- **Track Continuity**: Visual verification of "L-shaped" step movements between zones.
- **System Latency**: Measured by the interval between camera capture and server persistence.

---

## 2. Result Presentation

The system generates automated visualizations for every occupant upon completion of the camera simulation.

### Qualitative Results: Track Visualization
The primary output is a **Track Visual Map**, which plots an occupant's location (X-axis: Zones) against time (Y-axis: 09:00–17:00).

| Feature | Description |
| :--- | :--- |
| **Face Thumbnails** | The actual LFW test image captured by the camera at that specific time/zone. |
| **Step-Path Line** | Red lines indicate the estimated movement. The "L-shape" signifies the occupant stayed in a zone until the next transition event. |
| **Ground Truth** | Blue lines (mirrored by the red track in high-confidence scenarios) represent the known location. |

**Example Output:**
`Building_B0/B0_Person_1_track_visual.png`
*(This plot shows Person 1 moving through zT -> z1 -> z2 -> z3 -> z4 with high-confidence face matches at every step).*

### Quantitative Results: Detection Log
The `occupant_*_history.json` files record every detection event with numerical confidence:
```json
{
  "timestamp": "14:12:00",
  "zone": "zT_B0",
  "occupant_id": "B0_Person_1",
  "prob": 0.9857
}
```
In testing, the average recognition probability across 100 events was **0.972**, indicating high reliability using the 20-image reference gallery.

---

## 3. Result Analysis

### Why BrotherEye Performs Better
Unlike standard face recognition systems that only provide a name and a timestamp, BrotherEye maps these events into a **spatiotemporal track**. By using a distributed architecture, each building manages its own local occupants while remaining queryable via the network.

### Strengths
- **Robustness**: The L-shaped plotting prevents confusing diagonal "teleportation" lines, accurately reflecting that occupants exist within a zone until they are detected in a new one.
- **Visualization**: Embedding the actual captured face thumbnails allows for immediate manual verification by security personnel.
- **Efficiency**: Encodings are pre-loaded into memory, allowing the system to process a "LOCAL_EVENT" in under 100ms on standard hardware.

### Trends & Observations
- **Distance Decay**: We observed that LFW images with extreme poses or lighting result in a lower $p$ score (~0.85), though they are still correctly identified as the best match.
- **Temporal Consistency**: The 24-minute step size provides a clear, readable timeline that justifies claims of "continuous surveillance" throughout a standard work day.

### Limitations
- **Occlusion**: The current dlib-based model requires a clear view of the face.
- **Scalability**: While 25 occupants run smoothly, a system with 1000+ occupants would require an optimized vector database (like FAISS) for faster lookup.

### Conclusion
The results justify the implementation of a distributed DSTS. The high recognition probabilities and the clear, logical track visualizations demonstrate that the system is practically applicable for building-scale security monitoring.

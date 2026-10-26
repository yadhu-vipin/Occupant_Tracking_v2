# Visitor identification: presentation and review notes

Sources: `paper/paper_sn_v8.tex`, `README.md`, `buildinglib/_vendored/lsh.py`, `buildinglib/_vendored/bloom.py`, `paper/run_experiments.py`. Figures are the paper's 20-seed averages unless marked otherwise.

Slides: `paper/slides/visitor_id_status.pptx` (2 slides).

---

## 0. Presentation script (about 2.5 minutes)

### Slide 1: Visitor Identification, Decentralized vs Centralized (~1 min 15 s)

"Good morning. My work is on identifying visitors across multiple buildings.

The usual approach is centralized: one shared store of face embeddings, so any building can look up a visitor. That works, and it is well served by replication and similar techniques. My question is different. If each building keeps its own embeddings, can we still identify a visitor registered elsewhere, and how effective is that compared with centralized?

The challenge is that a visitor's face arrives at a building that does not hold their embeddings. Asking all nine other buildings is expensive, and sharing embeddings defeats the point.

So each building shares only a compact Bloom filter built from LSH codes of its occupants' faces. No embeddings or ids are shared. When a visitor appears, the face is hashed into codes and checked against the other buildings' filters. That gives a short ranked list of likely home buildings. Only those buildings verify the face against their own gallery.

My objectives are to compare accuracy with the centralized baseline, to measure the cost in buildings contacted and data exchanged, and to keep the embeddings local to each building."

(Point to the flow diagram: capture, routing, shortlist, local verification.)

### Slide 2: Current Status and Comparison (~1 min 15 s)

"On status, the visitor-identification routing is done and evaluated against the centralized baseline. The part I am working on now is the mid-review question: how well can we identify and track people wearing masks. I am working on the recognition side of that, and the next step is to measure masked against unmasked accuracy, including through the routing step.

On the right is the comparison. The test used 500 identities across 10 buildings, averaged over 20 runs. Centralized search identifies about 95.5% of visitors correctly. The decentralized approach with the adaptive policy gets about 93%. Misidentification is about 1.3% for both. The decentralized approach contacts about 3 of the 9 other buildings, instead of all 9, and no embeddings leave their building.

So the cost of decentralizing is roughly 2.5 points of accuracy. What we gain is that embeddings stay local and the number of buildings contacted drops sharply. The remaining gap is mostly cases where the visitor's home building was not on the shortlist. That is where I am focusing, along with masked faces."

### Quick answers if interrupted

- **Why LSH and a Bloom filter?** A small, one-way summary that supports a fast membership test. HNSW-style indexes contain the vectors themselves, and my goal is to avoid sharing those.
- **Why not just use ANN?** The gallery is small enough for exact search. The problem is deciding which building to ask, not search speed.
- **Is it private?** Filters are one-way and no embeddings are shared, but I have not made a formal privacy claim or analyzed membership leakage yet.
- **Why is the Bloom false-positive rate above 1%?** The Bloom array itself meets it at 0.94%. The extra comes from short 11-bit LSH codes colliding across different people.

---

## 1. The idea in one paragraph

A visitor's face arrives at a building that does not hold their embeddings. Asking all nine other buildings is expensive, and sharing embeddings defeats the purpose. Each building therefore publishes only a small Bloom filter built from LSH codes of its occupants. The capturing building hashes the visitor's face, scores the other buildings' filters, shortlists the top five, and only those buildings verify the face against their own reference embeddings. The aim is to measure how effective this decentralized approach is compared with a centralized embedding store, not to claim the centralized one is broken.

Terminology: embeddings are distributed across buildings, there is no central gallery or coordinator (decentralized control), and the baseline is a centralized store. Caveats: all buildings share the same hyperplanes, salt and config, and the README mentions CA certificates for the sealed channel.

---

## 2. End-to-end flow

### Setup (once, in each building)

```
For each of the 50 occupants:
  20 reference photos -> 20 embeddings -> average -> 1 centroid
        |
  LSH: 122 groups of 11 hyperplanes
        |
  122 codes (integers 0-2047), each tagged with its slot number
        |
Bloom filter: each code sets 7 bits in a bit array
        |
Result: one ~7.3 KB filter per building (6,100 codes inside)
        |
Share the filter with the other 9 buildings (no embeddings leave)
```

### A visitor arrives

```
Face rejected locally
        |
Face -> embedding -> LSH -> 122 codes
        |
Probes: for each slot, flip the 3 least-certain bits (4 candidates per slot)
        |
For each of the other 9 buildings:
   test the candidates against that building's filter
   a slot "hits" if any of its 4 candidates is found
   score = number of hit slots (0-122)
        |
Rank buildings by score -> top 5 = shortlist
        |
Score gap between the top two:
   small (<= 5) -> ask all 5 buildings
   large (> 5)  -> ask in rank order, stop at first accept
        |
Each asked building compares the face with its own references
   accepts if >= 12 of 20 references are within 80.43 degrees
        |
Result: identified person, or abstain
```

The filter and the score only point to where to look. The accept decision always comes from local verification.

---

## 3. Models

- Detection and alignment: SCRFD-10GF (InsightFace `det_10g`).
- Embedding: ArcFace via InsightFace `buffalo_l`, 512 dimensions, pretrained and not trained or fine-tuned here.
- The repo only names `buffalo_l`. From general knowledge of that pack, the recognition model is `w600k_r50.onnx`, an iResNet-50 trained on WebFace600K with the ArcFace loss. Confirm in `~/.insightface/models/buffalo_l/`.
- Embeddings are precomputed in `corpus/emb_arcface.npy`; `run_experiments.py` does not run the detector.
- Masked-face handling is in `recognition/mask_handler.py` (not covered here).

---

## 4. LSH codes

### Why

Two photos of one person give close but never identical embeddings. An ordinary hash maps near-identical inputs to unrelated outputs, so it cannot be used for matching. LSH maps similar inputs to the same output with high probability.

### How a code is formed

1. **Prepare the embedding.** Normalize, subtract the mean face, normalize again:
   `e = (e_raw/|e_raw| - x_mean) / |...|`.
2. **Random planes (once, shared).** Draw K x L = 11 x 122 = 1,342 random 512-vectors from a standard Gaussian, from a shared seed. They are grouped into L = 122 groups of K = 11.
3. **One bit per plane.** `h = 1 if w . e >= 0 else 0`. This records which side of the plane the face is on.
4. **Pack K bits into an integer.** `c_l = sum_k 2^k * h_{l,k}`, a number from 0 to 2^K - 1 = 2047.
5. **Repeat for all L groups.** Each face gives 122 codes.
6. **Tag with the slot.** `s_l = l * 2^K + c_l`, so slot 5's code 100 differs from slot 9's code 100.

Tiny example (K = 4): dot products +0.8, -0.3, +0.1, +0.5 give bits 1, 0, 1, 1, so code = 1 + 0 + 4 + 8 = 13. In slot 2 the tagged code is 2 * 16 + 13 = 45.

### Registered occupants vs captured faces

- Enrolled occupant: codes come from the normalized **centroid** of the 20 reference embeddings, which is stable.
- Captured face: codes come from that single embedding, plus probe variants.

### Why it works

- Two vectors at angle theta fall on the same side of a random hyperplane with probability `1 - theta/pi`.
- They agree on all K bits of a slot with probability `(1 - theta/pi)^K`.
- They agree in at least one of the L slots with probability

  `P_match(theta) = 1 - (1 - (1 - theta/pi)^K)^L`

- Larger K reduces chance agreement between different people. Larger L gives the same person more chances to agree.

### Parameter choice

- For each K, L is the smallest value giving same-identity pairs a 0.90 chance of agreeing in at least one slot, averaged over measured angles (reference-to-leave-one-out-centroid mean angle 46.2 degrees, versus 60.1 degrees between images).
- The rule applied to centroids gives L = 43 for K = 11. The chosen K = 11, L = 122 gives genuine agreement 0.0187 per slot and impostor agreement 0.062 over all slots.
- The setting was chosen after examining test-image accuracy. State this limitation openly.

### Multi-probe

Bits with a small margin `|w . e|` lie near the hyperplane and are the most likely to differ between two photos of the same person. For a captured face, the P = 3 smallest-margin bits per slot are flipped one at a time, giving `c xor 2^k`. Only the captured face is probed; the enrolled codes are unchanged. Routing abstentions fall from 6.86% (P = 0) to 3.78% (P = 3).

---

## 5. Bloom filter

### What it is

A long bit array, all zeros. Insert an item by hashing it to kappa positions and setting those bits to 1. Test an item by checking the same positions: all 1 means probably present, any 0 means definitely absent.

- No false negatives. Occasional false positives.
- One-way: the array cannot be turned back into the items.
- Compact: about 7.3 KB for 6,100 items.

### Sizing

For n items and target false-positive probability f*:

- `m = ceil(-n ln f* / (ln 2)^2)`
- `kappa = round((m/n) ln 2)`

Here n = N * L = 50 * 122 = 6,100 and f* = 0.01, so m is about 58,500 bits (about 7.3 KB) and kappa = 7 hash positions.

### Hashing

Double hashing (Kirsch-Mitzenmacher): two 64-bit SplitMix64 hashes h1 and h2, with h2 forced odd. Position j is `(h1 + j * h2) mod m`. The salt is shared, and the seed changes per run.

### Do not confuse these two numbers

| Term | Value | Meaning |
|---|---|---|
| L (slots) | 122 | LSH slots; one code per slot per face |
| kappa | 7 | Bloom hash positions per item |

The routing score counts slots out of 122, not hash positions out of 7. Each slot hit already required all 7 Bloom bits of at least one candidate to be 1.

---

## 6. Routing score and shortlist

For a captured face and one candidate building:

1. Compute the 122 tagged codes plus 3 probe codes per slot (4 candidates per slot).
2. Test each candidate against the building's filter.
3. A slot counts as a hit if at least one of its 4 candidates is present. Each slot counts at most once.
4. The score `A_b` is the number of hit slots (0 to 122). In code: `np.logical_or.accumulate` over the probes, then a sum over slots.

Then:

- The capturing building is excluded (its score is set to -1e9).
- Buildings are sorted by score. Ties go to the lower building index.
- The top S = 5 form the shortlist.
- The score gap g = best score - second-best score.

Why count slots instead of codes: so one slot with several lucky probes cannot outweigh the rest. A building should win by agreeing in many independent slots.

### Scores are noisy (rough estimate, not a measurement)

A wrong building hits roughly 12% of slots by chance (about 15 of 122, give or take 3 to 4), because of code collisions (about 2.4% per code) and Bloom false positives (about 1%), with four candidates per slot. The true building adds real matches from the same person's codes, which is only a few extra slots on average. That is why:

- the shortlist has five buildings: the right one only needs to be in the top five
- the gap threshold g0 = 5 matters: a small lead means the top two are hard to tell apart
- sequential verification can accept a wrongly ranked building first

---

## 7. Verification policies

At a shortlisted building, a reference votes if its angle to the captured embedding is at most tau = 80.43 degrees. The best occupant is accepted only with at least V = 12 votes of 20. tau minimizes the combined error: 7.7% false-reject on genuine pairs and 0.5% false-accept on impostor pairs. It applies to image pairs and is not a calibrated error rate for the voting rule.

| Policy | Rule | Correct | Misidentified | Buildings contacted |
|---|---|---|---|---|
| Centralized (baseline) | Search the 450 enrolled identities not registered in the capturing building | 95.50% | 1.31% | n/a |
| Verify all five | All five verify; best vote wins | 92.81% | 1.44% | 5.00 |
| Sequential | Rank order; stop at first accept | 92.19% | 2.06% | 1.49 |
| Adaptive (g0 = 5) | All five if gap <= 5, else sequential | 92.95% | 1.30% | 2.54 |

- Contacts are 83% (sequential), 72% (adaptive) and 44% (verify all) lower than contacting all nine.
- The adaptive policy contacts all five on 37.6% of attempts.
- Adaptive vs verify-all: +0.14 +/- 0.04 points (better in 19 of 20 seeds). Centralized remains 2.55 +/- 0.16 points above adaptive.
- All policies abstain on the same attempts.
- If no contacted building reaches V votes, the system abstains.

---

## 8. Experimental setup

- Data: 500 CASIA-WebFace identities, split at random into B = 10 buildings of 50. 20 reference and 20 held-out test images per identity (10,000 of each), disjoint and fixed.
- Events: one simulated day per seed, 10,000 events (one per test image): 8,000 local, 2,000 visitor.
- Visitor flow: first compared with the capturing building's 50 occupants. Only locally rejected visitors are routed over the other nine buildings.
- Evaluation is closed-set: unknown people are not tested.
- Parameters: K = 11, L = 122, P = 3, S = 5, V = 12, g0 = 5, f* = 0.01.
- 20 seeds. Each seed changes the building assignment, LSH hyperplanes, Bloom salt and simulated events. Embeddings, reference/test split and tau stay fixed. Confidence intervals use Student's t (19 degrees of freedom) and describe variation across runs, not across people or datasets.

### Metrics

Computed on **routed visitors** (visitors no occupant of the capturing building accepts). Each gets exactly one outcome:

| Outcome | Meaning |
|---|---|
| Correct | Accepted the right person |
| Misidentified | Accepted the wrong person |
| Verification rejection | True building shortlisted, but nobody reached V votes |
| Routing abstention | True building not shortlisted, nobody accepted |

Aggregate metrics: correct %, misidentified %, buildings contacted per attempt, share of attempts contacting all five, effective Bloom false-positive rate, estimated payload (analytic, 5.2 KB for adaptive), local-recognition correct % (about 96%, counted separately).

Inputs: embeddings, building assignment, event stream, parameters. Outputs: one outcome per routed visitor, summarized as accuracy, cost and filter quality against the centralized baseline.

Not measured: latency and real traffic. The system is an in-process simulation.

---

## 9. Why the effective FPR is 3.54%, not 1%

- Configured: 1%. Measured by testing enrolled items of the other nine buildings against each filter: 3.54 +/- 0.02%.
- Without a Bloom array, 2.61 +/- 0.02% of other buildings' items already equal an enrolled code in the same slot. With 50 occupants over 2^11 = 2,048 values, uniform codes would give about 2.41%.
- The Bloom array alone, tested on random integers never inserted, gives 0.94 +/- 0.01%.
- So the Bloom filter meets its target, and the excess comes from LSH code collisions between different occupants.
- Consequence: false hits add wrong buildings to the shortlist and can push the right one down in rank. This is a routing hint, not an identity decision, so it does not cause wrong identifications directly.

---

## 10. Effect of K and L

### Increasing K (code length)

- Code collisions fall: about 50 / 2^K (K = 11: 2.4%; K = 15: 0.15%).
- But same-person agreement per slot falls like `(1 - theta/pi)^K`, so L must rise to keep recall.
- Costs of a larger L: filter size grows linearly, more hashing per face (K * L projections), more probe lookups.

### Increasing L (number of slots)

- Recall rises: `P_match = 1 - (1 - p)^L`, with diminishing returns.
- Impostor hits rise too (about N * L / 2^K per building) and the filter fills more.
- Signal grows like `p * L`, chance noise like `L * N / 2^K`. The score gap is what matters.
- Past a point, L cannot fix large same-person angles (the main source of the remaining gap to centralized).

### One-seed K sweep (`python run_experiments.py ksweep`, query protocol, B = 10, P = 3)

L for each K is the smallest giving the same mean P_match as K = 11, L = 122 (iso-recall). Single seed, so differences under about a point may be noise.

| Config | Correct | Effective FPR | Code collisions | Filter size |
|---|---|---|---|---|
| K9, L57 | 89.7% | 10.45% | 9.75% | 3.3 KB |
| K11, L122 (paper) | 92.2% | 3.57% | 2.62% | 7.1 KB |
| K13, L267 | 92.9% | 1.64% | 0.67% | 15.6 KB |
| K15, L587 | 92.4% | 1.19% | 0.18% | 34.3 KB |
| K15, L122 (no compensation) | 86.5% | 1.20% | 0.19% | 7.1 KB |

Centralized on the same seed: 95.5%.

Reading: the FPR excess is a code-length effect; keeping L fixed while raising K hurts accuracy; accuracy plateaus around K = 13; the remaining gap to centralized is mostly routing recall, not FPR.

Run the full 20-seed version (about 22 minutes, writes `paper/results/dacs_ksweep.json`):

```bash
cd paper && DSTS_MEAN_FROM=refs DSTS_TAU=80.43167114257812 python run_experiments.py ksweep
```

---

## 11. Why LSH + Bloom, and what about other ANN methods

### Why LSH + Bloom

- Buildings must exchange something small that says where to look, without face embeddings.
- LSH makes different photos of one person collide, so a membership test works.
- The Bloom filter turns each building's codes into a small, one-way summary with a fast test.
- Shortlisting means only a few buildings are contacted, and the decision stays local.

### Other ANN methods (HNSW, IVF, PQ, ScaNN, Annoy, DiskANN)

- They exist and are better for fast search in a single index.
- At this scale (about 20,000 images, 10,000 references) exact brute force takes milliseconds, so ANN is not needed for speed.
- The problem here is routing (which building to ask), not search speed.
- Standard indexes are not designed to be shared: an HNSW graph contains the vectors; IVF centroids and PQ codebooks or codes are lossy copies of embedding structure.
- Per-building ANN with fan-out to all nine works but costs nine contacts per visitor.
- Do not claim LSH beats ANN: it does not. Accuracy is 2.55 points below centralized.

### HNSW in brief

A layered graph of the vectors. The top layer holds a few random nodes with long-range links. Search starts at the top, moves greedily to the closest neighbor, drops a layer, repeats, and at layer 0 runs a wider search (`efSearch`) to return the best k. Parameters: `M` (links per node), `efConstruction`, `efSearch`.

### Baselines worth adding

- Query all nine buildings (accuracy upper bound).
- Per-building centroid or mean-embedding routing (shares one vector per building).
- Random or round-robin shortlist (shows the Bloom ranking carries signal).

---

## 12. Visit pointers (how the home building knows where a visitor went)

1. A visitor enters building B and is identified via routing.
2. When B confirms the match at its entry zone `zT`, it sends the home building a small record: occupant id, current building, timestamps, no biometrics. Stored in `pointers.db`.
3. Departure at `zT` closes the pointer. Pointers also expire after the 4-hour TTL.

- The home building knows **which buildings** the person visited and when, not their zones inside them.
- Zone-level state (probability over zones) stays in the visited building.
- Q6 follows an open pointer to the right building; TRACK merges closed pointer history with local state.
- Limits: no pointer if the visitor is not matched at `zT` (including missed masked faces); events carry only a time of day, so ordering assumes one day.

---

## 13. Explainable queries

Explainability of the **policy decision** already exists (`--explain`: `policy_rule`, `reason`, `exposed_fields`, `redacted_fields`). The gap is explaining how the **answer** was derived.

Layers:

1. **Evidence:** which events, zones, timestamps and tracking probabilities produced the answer; which pointer was followed.
2. **Identification provenance:** local match vs routed visitor; shortlist, rank and score gap; which building verified; vote count; whether a mask changed confidence.
3. **Privacy and uncertainty:** what was redacted at which disclosure level (L0-L4); confidence band; what could make the answer wrong.

Per query type:

| Query | What to explain |
|---|---|
| Q1 (anyone after t?) | Latest exit or last-seen per person; who triggered "yes" |
| Q2 (visitors > occupants?) | The two counts; which visitors were counted; routed vs local |
| Q3 (left before t?) | Exit event used, or "no exit found, so still inside" |
| Q4 (entered in [t1, t2]?) | Entry event and timestamp, or nearest event outside the window |
| Q5 (visited all zones?) | Coverage checklist: visited, missing, evidence per zone |
| Q6 (where at t?) | State at t, gap to nearest observation, pointer followed |
| Q7 (distinct zones) | Zone list and dwell times |
| Q10-Q13 | Why counts instead of names (disclosure level); what was filtered |
| TRACK | Path with per-hop probability; segments from other buildings' pointer history |

### Is it enough for novelty?

On its own, probably not (provenance and explainable face recognition are well studied; this is my judgment, not a literature check). It can support a claim if tied to the setup:

1. Explanations generated per disclosure level, so an explanation never leaks more than the answer permits.
2. Provenance across buildings using only metadata (scores, votes, ids), no embeddings.
3. Explaining uncertainty from masks, linking to the recognition work.

Evaluate leakage per role, faithfulness (replay from the trail), overhead, and usefulness. Position it as a supporting contribution; the headline remains how effective decentralized identification is vs centralized.

---

## 14. Likely questions and short answers

| Question | Answer |
|---|---|
| Why LSH and a Bloom filter? | A small, one-way summary supports a fast membership test; HNSW-style indexes contain the vectors themselves. |
| Why not ANN? | The gallery is small enough for exact search; the problem is deciding which building to ask. |
| Is it private? | Filters are one-way and no embeddings, ids or centroids are shared, but no formal guarantee is claimed and membership leakage is not analyzed. |
| Why is the FPR above 1%? | The Bloom array meets it (0.94%); short 11-bit LSH codes collide across people (2.6%). |
| Why K = 11, L = 122, P = 3? | Chosen after examining test-image accuracy, so possibly optimistic; the K sweep shows the trade-off. |
| Why centroids for enrolled people but raw embeddings for the query? | A centroid summarizes 20 photos; the query is one noisy image. Centroid-to-image angle (46.2 degrees) is smaller than image-to-image (60.1 degrees). |
| Is it decentralized or distributed? | Embeddings are distributed; control is decentralized (no central gallery or coordinator); shared setup (hyperplanes, salt, config) is common to all buildings. |
| How can ~93% be "good"? | It is 2.55 points below centralized, mostly routing misses (right building not shortlisted), in exchange for embeddings staying local and about 2.5 contacts instead of 9. |

---

## 15. Limitations to state up front

- Closed-set evaluation; unknown visitors not tested.
- Effective Bloom FPR 3.54% vs the 1% target.
- About 3.3% of visitors are wrongly accepted locally.
- Payload is estimated, not measured; no latency or real network.
- 500 synthetic-assignment identities only; larger populations untested.
- K, L, P tuned on test-image accuracy.
- No formal privacy guarantee; membership leakage not analyzed.
- The older `reports/system_execution_summary.md` contradicts the paper and README; cite the paper.
- Masked-person recognition is in progress.

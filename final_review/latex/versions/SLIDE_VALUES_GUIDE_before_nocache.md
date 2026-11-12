# Slide-by-slide guide to every value in the final-review deck

Deck: `final_review/S7_Final_Review_Group_C3_updated_v8.pptx` (30 slides).
Purpose: for each slide, what every row, column, number, formula and parameter means, where it comes from, and how it was calculated, so any question in the review can be answered from this file.

**Contents**

- [0. Conventions used everywhere](#0-conventions-used-everywhere)
- [1. The fixed parameters (hyperparameters)](#1-the-fixed-parameters-hyperparameters)
- [2. The experiment: how the 10,000 events are produced](#2-the-experiment-how-the-10000-events-are-produced)
- [3. Slides 1 to 8: front matter](#3-slides-1-to-8-front-matter)
- [4. Slides 9 to 17: methodology and approach](#4-slides-9-to-17-methodology-and-approach)
- [5. Slide 18: evaluation setup](#5-slide-18-evaluation-setup)
- [6. Slides 19 to 22: results](#6-slides-19-to-22-results)
- [7. Slides 23 to 30: discussion, conclusion, appendices](#7-slides-23-to-30-discussion-conclusion-appendices)
- [8. Where the numbers live in the repository](#8-where-the-numbers-live-in-the-repository)
- [9. Honest caveats to know before the review](#9-honest-caveats-to-know-before-the-review)

---

## 0. Conventions used everywhere

| Term | Meaning |
|---|---|
| **Seed** | One random population: it decides which 50 of the 500 identities go to which building, who is the Dean/Teacher/Student and the teacher rosters, and the events that are generated (who is seen where and when). Seeds 0 to 49 = 50 independent populations. |
| **Mean of 50 seeds** | Every metric is computed *inside each seed* first (for example precision of that seed's 10,000 events) and then the 50 per-seed values are averaged. It is not a pooled ratio over all 500,000 events. |
| **Event** | One camera sighting: a face photo, a building, a zone and a time. One event is made from one *test* photo, each used once. |
| **Local event** | The person is registered in the building where the photo was taken. |
| **Visitor event** | The person is registered in another building of the campus (a visitor is always an enrolled person elsewhere; there are no unknown guests). |
| **Central search (baseline)** | The same pipeline, same local check, same day cache, but a visitor's first sighting is looked up by a direct search of the people of **all 9 other buildings** (no LSH, no Bloom filter), and every building is asked. |
| **Ours (decentralised)** | Local check, then LSH + Bloom routing to a shortlist of 5 buildings, which verify by voting. |
| **Abstain** | The system names nobody (no building confirms). It is a miss (FN) but not a wrong answer (not an FP). |

### One-vs-rest metrics (used on slides 18, 19, 20)

For each event there is one true identity out of **500 labels** and at most one predicted identity.

| Count | Definition |
|---|---|
| **TP** | predicted identity = true identity |
| **FP** | an identity was predicted but it is not the true one (counted once per wrong answer) |
| **FN** | the true identity was not predicted: either a wrong person was named, or nobody was named |
| **TN** | every other (event, label) pair = events × 500 − TP − FP − FN (huge, so it is never used for the headline) |

A wrong answer is therefore **both** an FP and an FN; an abstention is **only** an FN.

```
Precision = TP / (TP + FP)          "of the people we named, how many were right"
Recall    = TP / (TP + FN)          "of all events, how many did we get right"  (= identification accuracy)
F1        = 2·TP / (2·TP + FP + FN) = 2PR / (P + R)
```

Because every event is either TP, a wrong answer or an abstention, **recall = TP / events** is exactly the "identified correctly" percentage. That is why recall 0.950 on slide 19 equals the 95.0% quoted elsewhere.

Worked example, seed 42, all events: TP = 9,488, FP = 170, FN = 512 (of which 170 wrong + 342 abstentions)
→ precision = 9,488 / 9,658 = 0.982; recall = 9,488 / 10,000 = 0.949; F1 = 18,976 / 19,658 = 0.965.
The 50-seed means are TP 9,501.4, FP 155.5, FN 498.6 → 0.984 / 0.950 / 0.967.

---

## 1. The fixed parameters (hyperparameters)

All of these are fixed constants; none is re-tuned per seed. They sit in every building's `nodes/building_X/config.json` and `config/experiment.yaml`.

| Parameter | Value | Meaning and effect |
|---|---|---|
| Embedding | 512-dimensional ArcFace (InsightFace `buffalo_l`, `w600k_r50`) | A face becomes a 512-number vector. Detector: SCRFD. Models are pretrained and unchanged. |
| References per person **R** | 20 | The 20 photos used to enrol a person in their building. |
| Test photos per person | 20 (different from the references) | Each becomes exactly one event. 500 × 20 = 10,000 events. |
| **Accept angle** | 80.43° | A reference photo "votes" for a candidate when the angle between its embedding and the capture is ≤ 80.43°. |
| **Minimum votes** | 12 of 20 | A person is accepted only if at least 12 of their 20 references vote. |
| LSH bits **K** | 11 | Each LSH slot is an 11-bit code (2,048 possible values). |
| LSH slots **L** | 122 | Independent random-hyperplane tables. Hyperplanes are drawn once from a fixed seed and are identical in all buildings. |
| Multi-probe | 3 | Besides the exact code, the 3 least confident bits of each slot are flipped to give extra probes. |
| Bloom target false-positive rate | 0.01 | Sizing target. The filter holds 50 people × 122 slots = 6,100 items, giving m = ⌈−n·ln(0.01) / (ln 2)²⌉ = **58,469 bits (7.3 KB)** and k = round(m/n · ln 2) = **7 hash positions**. Fill ratio = 1 − e^(−7·6100/58469) ≈ 51%. |
| Effective Bloom FPR | ≈ 3.5% (3.56% measured) | The rate routing actually sees: LSH codes of different people collide (≈2.4% by chance with 11-bit codes) on top of the array's own ≈0.94%. It is only a routing hint; verification is exact, so it is not a correctness error. |
| Shortlist size | 5 | The 5 highest Bloom-score buildings are asked to verify. |
| Verification policy | sequential (main results) | Ask in rank order, stop at the first confirmation. Alternatives on slide 16. |
| Adaptive gap g₀ | 5 | If best − second-best Bloom score ≤ 5, ask all 5; else sequential. |
| Visitor promotion | 3 entries in 7 days, or 8 in 30 | After that, the next entry stores the visitor permanently. |
| Flag thresholds | few votes < 16; narrow margin < 5; stale > 60 min | Slide 15. |
| Sign-in token life / lockout | 1 hour / repeated failed logins lock the account | Slide 12. |
| Replay window | 300 s | Slide 12. |

---

## 2. The experiment: how the 10,000 events are produced

- **Dataset:** CASIA-WebFace, **500 identities**, 40 photos each (20 reference + 20 test) = 20,000 embeddings.
- **Campus:** **10 buildings × 50 people**. Which 50 go where is decided by the seed.
- **Roles per building:** 1 Dean, 10 Teachers, 39 Students (random per seed); each Teacher has a roster of about 4 students (students are assigned to teachers round-robin).
- **One simulated day** (single-day model of the generator, `--days 1`): each of the 500 people produces **20 events** between 08:00 and 17:00 (the generator's default window; event spacing is set by its interval bounds of 30 to 180 seconds). A visitor trip happens with probability `visitor_ratio` = **0.20**, so the visitor events number round(500 × 20 × 0.20) = **2,000** and the local events **8,000**. The 2,000 is set by the generator, it is not an outcome.
- **Zones:** z1 Entrance, z2 Mail room, z3 Office, z4 Lounge, z5 Conference, z6 Classroom, z7 Cafeteria, z8 Exit, and zT (transition zone to the outdoors; movement between buildings passes through zT). That is 9 zones.
- **Compute:** laptop CPU, no GPU; one run of 10,000 events takes about 8 minutes.
- **Pipeline order per seed:** build population → enrol and publish Bloom filters → generate events → local recognition (phase 2) → routing and remote verification (phase 3) → state update (phase 4) → evaluation.

---

## 3. Slides 1 to 8: front matter

**Slide 1: Title.** Project title, the four team members with roll numbers, guide Dr. Lakshmi Mohan, "S7 Project Final Review", Group C3. No numbers.

**Slide 2: Outline.** The 18 section names in presentation order.

**Slide 3: Introduction.** Definition of a *smart space* (campuses, hospitals, military bases), why the camera is modelled (nobody has to stop and tap, unlike RFID or badges), the campus framing (a person registered in one building is a visitor in every other), and the motivation: biometric data in a central store cannot be revoked if it leaks; anyone who can query tracking data could follow anyone, so answers are filtered by role; answers must be explained. No numbers. The central design is treated as the reference, not as bad: the question is how close a distributed design can come to it.

**Slide 4: Problem definition.** Existing systems track inside one site; distributed trackers associate across cameras; neither tells how a person registered elsewhere is identified or who may query locations. The DSTS base paper resolves visitors through a centralised master list. The problem statement is the one-sentence goal of the project.

**Slide 5: Objectives.** Six objectives (circles 1 to 6): extend DSTS to many buildings; RBAC query interface; explainable responses; LSH + Bloom lookup; security; evaluation on precision/recall, **communication cost (buildings contacted per lookup)** and explanation quality. Objective 6 names the metrics that the results slides report.

**Slide 6: Literature review table.** Columns: paper and year; key contribution; demerit (what is missing relative to this project); dataset used; metrics. All figures in this table (for example MOTA 88.6%, 1.41 m error, F1 ~90%, 35 registered occupants in the base paper's campus) are **taken from the cited papers**, not produced by this project. Rows [1] to [7] are the reviewed works; [6] is the DSTS base paper. The "Acronyms" line expands MAE, MOTA/MOTP, IDF1, FP/FN, DBSCAN, RF.

**Slide 7: Research gap.** Three gaps (cross-building identity, routing effectiveness, role-aware explainable querying), what is missing, and which part of this project fills each. The "routing effectiveness" row names the comparison measured on slide 19: identification accuracy and buildings contacted.

**Slide 8: Datasets, platforms and tools.**

| Row | Value and derivation |
|---|---|
| Dataset | 500 identities × (20 reference + 20 test) = **20,000 embeddings**; 10 buildings × 50 people = 500. |
| Face models | SCRFD detector + ArcFace `w600k_r50`, 512 dimensions, run with ONNX Runtime. Pretrained, not fine-tuned. |
| Platform | Windows 11, Python 3.12, CPU only; full 10,000-event run ≈ 8 minutes. |
| Routing and tracking | Random-hyperplane LSH, Bloom filter, vote-based verifier and BSTS (Building-level State Transition System) tracking databases in SQLite, all written from scratch with NumPy/pandas. |
| Security and API | X25519 key exchange, AES-GCM encryption, Ed25519 signatures, signed login tokens, FastAPI, static HTML/JS dashboard, Prometheus metrics. |
| Quality | **574 pytest tests** (breakdown on slide 21); seeded populations make every result reproducible. |

---

## 4. Slides 9 to 17: methodology and approach

### Slide 9: Architecture and what is new
The figure shows buildings, each with its own gallery, Bloom filter and tracking databases (BSTS), and the sealed channel between them. The Novelty list:

1. **LSH + Bloom routing:** only a Bloom filter (no ids, no embeddings) leaves a building.
2. **Targeted peer verification:** only shortlisted buildings compare faces (12 of 20 votes).
3. **Dynamic visitor database:** presence, day cache, entry counter, permanent pool.
4. **Role-based access control** with graded levels of detail (L0 to L3).
5. **Explainable answers:** rule, evidence, uncertainty flags, redacted to the caller's level.
6. **Encrypted transfers:** every cross-building message is encrypted, signed and replay-protected.

### Slide 10: Identification and routing (equations 1 to 4)

**Preprocessing.** A raw embedding *e* is normalised, centred with the mean face *x̄* of the reference photos (references only), and normalised again:
`ê = (e/‖e‖ − x̄) / ‖e/‖e‖ − x̄‖`. A person's centroid `z_u` is the normalised average of their 20 preprocessed references; it is used only to build the Bloom filter. The 20 individual references are kept for voting, because a single centroid would discard the variation between photos of the same person.

**Equation (1), LSH code of table l.**
`h(l,k)(ê) = 1[ w(l,k)ᵀ ê ≥ 0 ]`, `c_l = Σ_k h(l,k) · 2^k`.
Each of the K = 11 hyperplanes `w(l,k)` says which side of a plane the embedding lies on (1 or 0). The 11 bits form an integer code `c_l` in 0 to 2,047. There are L = 122 such tables. An item for the Bloom filter is `l · 2^K + c_l`, so a code only matches in the same slot.

**Equation (2), chance two faces share a bit.** `P[bit(a) = bit(b)] = 1 − θ/π`, where θ is the angle between the two embeddings. Close faces (small θ) agree on most bits, which is why similar faces get the same codes. With 122 slots and 3 probes, a few bit disagreements caused by photo noise are tolerated.

**Equation (3), Bloom score of building b.** `score(b) = | { slots l : some probe code of slot l is in F_b } |`, where P is the set of the capture's probe codes (exact code plus 3 flipped-bit probes per slot) and F_b the Bloom filter published by building b. It counts the **distinct slots that match**, on a scale 0 to 122. The 5 highest scores form the shortlist. Buildings are compared locally; nothing is sent while scoring.

**Equation (4), acceptance by votes.** `votes(u) = Σ_{j=1..20} 1[ θ_j ≤ 80.43° ]`; person *u* is accepted if `votes(u) ≥ 12`. θ_j is the angle between the capture and the j-th of *u*'s 20 references. The winner is the person with most votes (ties broken by the smallest single angle). This vote rule is used at the local building **and** by every shortlisted building.

### Slide 11: Dynamic visitor database and visit pointers (equation 5)

1. **Presence:** who is inside a building and in which zone, until they leave or the day ends. Every event updates the zone probabilities of visitors inside. *Transition-zone rule:* a visitor waiting in zT is left out of other people's updates until seen outside zT.
2. **Day cache:** the home building confirms the match, then a copy of the visitor's **20 references** is held in the visited building's **memory for one simulated day**. A repeat sighting that day is matched there with the same vote rule, with no routing and no other building asked. Zeroed at day rollover.
3. **Permanent pool:** every arrival is logged on disk. If earlier entries satisfy a rule (3 in 7 days or 8 in 30), the *next* entry stores the embeddings permanently, and the visitor is matched on any later day without routing.
4. **Visit pointers:** on arrival the home building stores `(occupant id, current building, entry time)`, no biometrics; the second zT event closes it with an exit time. A location query follows the open pointer directly to the right building.

**Equation (5), probability of candidate i** (Definition 3.3 of the DSTS paper):

```
p_i = exp(−d_i² / 2σ²) / Σ_l exp(−d_l² / 2σ²)
```

`d_i` = distance from the event to candidate *i*, taken as that candidate's **smallest angle** (in degrees) to any of their 20 references; `σ` = population standard deviation of all candidates' distances; uniform if σ = 0. The Bloom score, votes and ground truth never enter p. **It is an identity probability, not a calibrated confidence.** (The dashboard calls it "confidence"; the report explains why that label is misleading.) Example from the demo: 0.970 for the winner when its distance is 74.6° against the next 75.2° and σ = 3.71°.

### Slide 12: Security and role-based access control

- **Between buildings.** *Encrypted payloads:* a fresh X25519 key per message, HKDF-SHA256 to derive a key, AES-GCM with the whole header as associated data. *Signed:* Ed25519 over header, nonce and ciphertext, replies bound to their request. *Certified keys:* a campus certificate authority signs a certificate binding both public keys; expiry, revocation, subject and pinning are checked. *Replay guard:* a message is accepted once per nonce and only within a **300 s** window, checked after the signature so the timestamp is authenticated. *Fail closed:* any failed step gives no reply and admits nothing.
- **Two layers of access control.** Layer 1 authenticates the signed-in principal and applies the building scope. Layer 2 (the persona policy) looks at the caller's role and relationship to the target and returns a disclosure level. Sign-ins expire after 1 hour, repeated failed logins lock the account, every decision is logged.

**"Who may see whom" table**

| Role | May follow | Hidden from them |
|---|---|---|
| Dean | own campus's teachers and students (L3); other Deans (L3) | people of other campuses |
| Teacher | only their roster students, in any building (L3) | unassigned students, peers, the Dean |
| Student | themselves (L3); assigned teachers (L2, present only) | peers, other teachers, the Dean |

**"What each level shows" table**

| Level | Name | What the caller sees |
|---|---|---|
| L0 | None | Nothing; refused with a rule code |
| L1 | Presence | Counts and presence, no names or positions (granted for building and zone aggregates) |
| L2 | Precise current | Exact zone, zone label, confidence and time, for the present only |
| L3 | Historical track | Full trajectory and dwell times |

### Slide 13: Query types (14 templates)
Columns: **Target** (building, person or zone), **Query** id, **What it asks**, **What it returns**. Reading the rows:

| Query | Asks | Returns |
|---|---|---|
| Q1 | Did anyone stay in building B after time t? | Yes/no and the number of people (people seen after t) |
| Q2 | Were there more visitors than registered people in B at t? | Yes/no with the two counts |
| Q3 | Did O leave B before t? | Yes/no |
| Q4 | Did O enter B between t1 and t2? | Yes/no |
| Q5 | Did O visit all zones of B? | Yes/no |
| Q6 | Where was O at t? | Building, zone and time of O's **last identified sighting at or before t** |
| Q7 | How many zones did O visit in [t1, t2]? | A number (distinct zones) |
| Q8 | When did O first visit zone Z? | A time |
| Q9 | How long did O stay in Z on the first visit? | A duration |
| Q10 | Who was in zone Z at t? | People whose last sighting at or before t is in Z |
| Q11 | Where was O during [t1, t2]? | The sightings in the window with zones and times |
| Q12 | Who was in Z during [t1, t2]? | A list of people |
| Q13 | Who ever visited Z? | A list of people |
| TRACK | O's full trajectory | Every sighting across buildings, with the photo |

(O = person, B = building, Z = zone, t, t1, t2 = times. TRACK is the dashboard's person-track view.)

### Slide 14: Who may use which query
Derived by running every query through the live API for each of four callers on seed 42 (building_5): Dean 0002253, Teacher 0000582, Student 0000205, Outsider 0000099 (a student registered in building_1). Targets: a rostered student (0000103), another student (0000210), the Dean, and the outsider.

| Row | Rule behind it |
|---|---|
| Building queries Q1, Q2 | Aggregates. Everyone may ask about their **own** building; only the Dean also gets the people behind the count (L3). Everyone else gets yes/no and counts only (L1). |
| Person queries Q3 to Q5, Q7 to Q9, Q11, TRACK | One rule for all of them: Dean = anyone of the own campus and other Deans (L3); Teacher = roster students wherever they are (L3); Student and outsider = themselves only (L3). |
| Q6 | Same as above, but a Student may also ask Q6 about an **assigned teacher**, only for the present moment at exact-zone level (L2), never the teacher's history (the other person queries are refused for that teacher). |
| Zone queries Q10, Q12, Q13 | Each person in the zone is run through the persona policy individually and listed only if the caller may see their exact zone (L2+); the rest are only counted. Dean: everyone of the own campus in that zone (L2). Teacher: roster students named, others counted (L1 if nobody can be named, L2 otherwise). Student, outsider: refused. |
| Another building | Refused for everyone, except a Dean following another Dean and a Teacher following a rostered student. |

### Slide 15: Explainable responses and the six flags
Every answer carries five parts: **Rule** (the decision rule in one sentence), **Evidence** (the events it rests on, each with a role such as decisive, last-before-t, carry-in), **Absence** (what was looked for and not found), **Uncertainty flags**, **Withheld** (what the caller's level removed). The explanation is built from the **same event list that produced the answer**, never recomputed, then redacted to the caller's level.

| Flag | Raised when | Threshold |
|---|---|---|
| `vote_winner_not_nearest` | the accepted person was not also the person with the single closest photo (the strongest warning) | exact comparison |
| `low_votes` | fewer than 16 of 20 votes although 12 suffice | votes < 16 |
| `verified_by_lower_ranked_building` | the confirming building was not first on the shortlist | rank > 1 |
| `narrow_routing_margin` | best minus second-best Bloom score is small (out of 122) | gap < 5 |
| `stale_observation` | a point-in-time answer rests on a sighting older than 60 minutes | age > 60 min |
| `pointer_based` | the value comes from a visit pointer with no identified sighting behind it | exact |

The first four concern identifying a person; the last two concern the answer to a query. Flags are computed from facts saved **at the moment of identification** (votes, rank, Bloom gap, nearest-or-not); they never use the true identity. A flag means "look again", not "this is wrong".

### Slide 16: Shortlist verification policies
Shortlist of S = 5 buildings from Bloom scores, best first.

| Policy | Rule | Contacts per routed lookup (50-seed mean) |
|---|---|---|
| Sequential (used for the headline) | ask in rank order, stop at the first confirmation | 2 (1.77) |
| Adaptive | if gap g ≤ 5 ask all 5 (most votes wins), else sequential | 3 |
| Verify all | all 5 verify, most votes wins | 5 |

All three abstain on the same lookups: an abstention means no shortlisted building confirms.

### Slide 17: Proposed approach
- **Algorithm (7 steps per event):** read the event; vote over each local person's 20 references; if not accepted, LSH codes and score peers' Bloom filters; verify in the shortlisted buildings over the sealed channel; update zone probabilities (BSTS); open/close the visit pointer and cache the visitor; answer queries by role with the explanation.
- **Training and testing:** the models are pretrained and unchanged (no training here). 20 unique references per person enrol the building; 20 *different* photos per person are the events, each used once; 50 independent random populations (seeds 0 to 49); test once on one simulated day of 10,000 events.
- **Hyperparameter table:** K / L = 11 / 122; Bloom target false-positive rate 0.01; 20 references; accept angle 80.43°; minimum votes 12 of 20; shortlist 5 / probes 3; visitor promotion 3 in 7 days or 8 in 30. See section 1 above for the meaning of each.

---

## 5. Slide 18: evaluation setup

- **Setup and baselines:** 500 people in 10 buildings, 50 seeded populations, all results are means; one simulated day of 10,000 events (8,000 local, 2,000 visitor); the baseline is the central search defined in section 0.
- **Metrics box:** the one-vs-rest definitions and formulas of section 0. "Identification accuracy = correct identities / events" equals recall. "Buildings contacted" = other buildings asked to verify a capture (the central search asks all 9 for a routed visitor).
- **Why these:** accuracy alone hides abstentions and wrong answers (a wrong answer is both an FP and an FN), so precision, recall and F1 are reported per cohort (all, local, visitor).

---

## 6. Slides 19 to 22: results

### Slide 19: Identification and routing (central search vs ours)

**Table: rows are cohorts, columns are scores for each design**

| Column | Meaning |
|---|---|
| Cohort | All events (10,000), Local = registered in the building where seen (8,000), Visitor = registered elsewhere (2,000) |
| Events | Count in that cohort per run |
| Precision / Recall / F1 (Central, Ours) | The one-vs-rest scores of section 0, computed per seed for each design on the same events, then averaged over 50 seeds |

| Row | Precision (C / O) | Recall (C / O) | F1 (C / O) |
|---|---|---|---|
| All | 0.982 / 0.984 | 0.952 / 0.950 | 0.967 / 0.967 |
| Local | 0.990 / 0.992 | 0.959 / 0.959 | 0.975 / 0.976 |
| Visitor | 0.950 / 0.950 | 0.921 / 0.913 | 0.935 / 0.931 |

Mean counts behind them (central / ours): all TP 9,517.2 / 9,501.4, FP 170.8 / 155.5, FN 482.8 / 498.6; local TP 7,675.0 / 7,675.0, FP 74.1 / 59.6; visitor TP 1,842.2 / 1,826.4, FP 96.7 / 95.8, FN 157.8 / 173.6.

**The four cards**

| Card | How it is computed |
|---|---|
| **0.16 pts** | mean over seeds of (central recall − our recall) = 0.0016 → 95.17% vs 95.01%. Central search is ahead in **50 of 50** seeds (per-seed gap 0.03 to 0.34 points, 95% CI about ±0.02). |
| **0.00 pts** (local) | Local recall is 7,675 / 8,000 = 95.94% in **both**: a local event is decided by the local check (≥ 12 of 20 votes) before any routing, so the two designs cannot differ in correct answers. They differ in *wrong* answers: central gives 74.1 − 59.6 ≈ **14 more wrong identifications** (precision 0.990 vs 0.992), because after a local rejection it keeps searching all other buildings and sometimes accepts a look-alike where ours abstains. |
| **0.8 pts** (visitor) | 92.11% − 91.32% = 0.79 points of visitor recall: the cost of routing to a shortlist instead of searching everyone. Visitor recall is capped by *routing recall* (the right building is in the shortlist of 5): 90.6% of routed lookups. |
| **9 vs 2** | Mean buildings asked per **routed** visitor lookup (a visitor whose first sighting is not answered by the day cache): central = 9 (all other buildings); ours = 1.77, shown as 2. |

**"How to read" and footnote:** definitions of TP/FP/FN and the three formulas; "Central" is the baseline; recall counts every abstention as a miss.

**Not on the slide but related (report):** buildings asked per event (central 0.52, ours 0.10; visitors 2.58 vs 0.52); "gallery touched" = (1 + buildings asked) / 10 = 15.2% central vs 11.0% ours over all events.

### Slide 20: Dynamic visitor database (with and without caching)

**What was run.** The same 50 populations twice with identical events. *Without the database:* the visitor database is still kept up to date but never answers a lookup, so every visitor sighting the capturing building does not recognise is routed (environment switch `DSTS_VISITOR_DB=off`). *With the database:* normal behaviour.

| Row | Meaning and derivation | Without | With |
|---|---|---|---|
| Answered inside the building, no routing | Visitor events whose answer came from the day cache (no other building asked). Without: 0 by construction. With: 1,344.9 of 2,000 = **67.2%** | 0 | 1,345 (67.2%) |
| Buildings asked per day | Sum over the day of buildings asked to verify (local events ask nobody, so all of it comes from visitor events) | 2,868 | 1,049 |
| Precision | Visitor events only: TP / (TP + FP) | 0.946 | 0.950 |
| Recall | TP / (TP + FN) | 0.894 | 0.913 |
| F1 | 2TP / (2TP + FP + FN) | 0.919 | 0.931 |

Mean counts: without TP 1,787, FP 103, FN 213; with TP 1,826, FP 96, FN 174.
Savings: 1 − 1,049 / 2,868 = **63%** fewer buildings asked. Recall rises by **1.96 ≈ 2 points** because a repeat sighting is no longer identified again from scratch, with another chance of a routing miss.

**Chart:** bars of precision, recall and F1 for the two cases (axis from 0.85 to 1.00).

**What this slide does not show (report):** the price of caching: a wrong first identification is reused all day, about **14 wrong identifications per day** come from the database and only **4.3%** carry a flag; visit pointers are correct for **98.3%** of about **414** closed visits per run. These are one-day runs, so **all database answers are day-cache answers; the permanent pool needs visits over several days and is not exercised.**

### Slide 21: Security and RBAC results

**Disclosure level chips:** L0 none, L1 presence only, L2 exact zone present only, L3 full history.

**Persona replay table (16 requests per role = 64).** Run on seed 42, building_5, by `python tools/rbac_matrix.py --seed 42`. Each role makes 16 requests: 3 person queries (Q3, Q6, TRACK) × 4 targets = 12, plus Q1 and Q10 × 2 buildings (own and another) = 4.

| Role | Answered | Refused | Levels granted |
|---|---|---|---|
| Dean | 11 | 5 | L3 ×10, L2 ×1 |
| Teacher | 5 | 11 | L3 ×3 (roster student), L1 ×2 (Q1 and Q10 own building) |
| Student | 1 | 15 | L1 ×1 (Q1, own building) |
| Outsider | 4 | 12 | L3 ×3 (themselves), L1 ×1 (Q1 of own building) |
| Total | **21** | **43** | each decision, rule code and level compared with the policy |

Every refusal still returns a rule code (for example `RULE_C_PEER_STALKING_DENIED`) and level L0.

**Automated tests (574 total, all passing).** Counted by pytest per area: access control 112; sealed transfers 61; API 98; queries and explanations 181; pipeline, population, events, evaluation 98; standalone, centralised, state library 19; shared utilities 5 (112 + 61 + 98 + 181 + 98 + 19 + 5 = 574).

**Attack cases covered:** tampering with every header field of a sealed message, replay, wrong sender or recipient, a revoked certificate, brute-force login (lockout), a token used as another person, a cache shared between roles. A failed step returns nothing (fail closed).

**Limits:** the network and certificate authority are simulated; there is no real TLS and no outside penetration test. This is a tested design, not a hardened deployment.

### Slide 22: Explainability results

**Table definitions.** An *identification* is an identified event. *Wrong* = identified person ≠ ground-truth person. *Flagged* = it carries at least one of the four identification flags. Per seed:
- **Wrong ones flagged** = wrong identifications with a flag / wrong identifications (higher is better).
- **Correct ones flagged** = correct identifications with a flag / correct identifications (false alarms, lower is better).
Then averaged over the 50 seeds.

| Row | Wrong flagged | Correct flagged | Reading |
|---|---|---|---|
| All identifications | 73.9% | 2.3% | about 3 in 4 wrong ones carry a flag; 2 in 100 correct ones do. 34% of flagged identifications are wrong, against ≈0.4% of unflagged ones. |
| In the person's own building | 74.2% | 0.9% | local recognition |
| Visitors found by routing | 85.3% | 30.0% | flagged reliably but noisily |
| Visitors from the visitor database | 4.3% | 0.0% | the weak spot: a cached visitor leaves almost no trail for the flags |

**Cards**
- **79% / 39%:** the same check on **query answers** (about 6,900 answers per run: location at a time, zones visited, full track). Of the wrong answers, 79% carry a flag (recall); of the flagged answers, 39% are wrong (precision); 21% of all answers carry a flag.
- **+13%:** cost of building the explanation. Time per query summed over the 14 query types: **5.9 ms → 6.6 ms** = +12.8% (per-seed range 12.1 to 13.4%), measured on seeds 0 to 4, one at a time, on one machine (so no network delay). Response size: **16 KB → 119 KB** with the full explanation (evidence listed).
- **Strongest flag:** `vote_winner_not_nearest`: 714 of the 730 identifications that carry it (summed over 50 seeds) were wrong.

**"Faithful by test":** for every query type, tests with a yes, a no and an empty answer (**40 cases**) check that each piece of evidence is one of the system's own events and that redoing the answer from the evidence gives the same answer; further tests check an explanation never shows more than its role may see.

**Two-role screenshots.** The same query (Q6 on occupant 0001272 at 16:00, 5 Jan 2026) asked by a Dean of building 8 (0001054) and by a Student (0000114):
- *Dean:* "At building_3 / z6 at 15:52:45 (confidence 0.970)". Rule `RULE_A_OWN_CAMPUS_FULL`, level L3. The sighting is event E008700, the last identified sighting at or before 16:00. Flags raised: few votes (12 < 16), lower-ranked building verified (rank 3), narrow routing margin (gap 4 < 5).
- *Student:* `RULE_C_PEER_STALKING_DENIED`, L0, explanation withheld entirely.
- The 0.970 is the identity probability of equation (5), not a measure that the answer is right. (This particular example is actually a wrong identification which the three flags correctly warn about.)

---

## 7. Slides 23 to 30: discussion, conclusion, appendices

**Slide 23: Discussion and limitations**

| Statement | Source |
|---|---|
| within 0.2 points (95.0% vs 95.2%) while asking 2 buildings instead of 9 (0.10 vs 0.52 per event) | slide 19: 95.01% vs 95.17%; asked per routed lookup 1.77 vs 9; per event 0.10 vs 0.52 |
| most visitor events need no routing | 67.2% answered from the day cache (slide 20) |
| flags mark 74% of wrong, 2.3% of correct, about 13% extra latency; visitor-database errors almost never flagged | slide 22 |
| Bloom filters report 3.5% false positives, not the configured 1% | measured over 50 seeds: effective rate 3.56% (≈0.94% from the array + code collisions) |
| asking all five, or only when top scores are close, cuts wrong identifications of routed visitors by about 0.6 points for 3 to 5 contacts instead of 2 | routed visitor lookups, 50 seeds: misidentified sequential 2.95%, adaptive 2.29%, verify-all 2.36% (so 0.66 and 0.59 points); correct 83.89% / 84.49% / 84.42%; contacts 2 / 3 / 5 |

Limitations: simulated network, sensors, detector and certificate authority; TLS relies on a proxy, data on disk is unencrypted, no penetration test; a wrong identity in the visitor cache lasts for the rest of the day; 500 people / 10 buildings only; week and month simulations replay one recognised day; masked-face recognition is not implemented.

**Slide 24: Conclusion.** One-sentence summary and the objective-wise table: objective, planned date, status, evidence ("2 buildings asked instead of 9, 0.2-point accuracy cost" for objective 4). The asterisk on objective 5 marks that the network is simulated.

**Slide 25: Future work.** (1) Masked-face recognition (a model trained with masks, a real mask detector, context such as who is already inside). (2) Unknown-visitor re-identification (keep one-way LSH+Bloom codes of unknown faces under a temporary id; needs retention and privacy rules and a check that two strangers are not merged). (3) Other sensors (badge, RFID, beacons) feeding the same pipeline, each needing its own error measurement and access-rule review.

**Slide 26: Team contribution.** Who did what, following repository history and hand-overs.

**Slide 27: Appendix A, code and demo.** Repository `github.com/yadhu-vipin/Occupant_Tracking_v2`, branch `test_7`; run all seeds with `python manage.py run --seeds 0-49`; demo with `DSTS_DEMO_MODE=1 python manage.py serve` then open 127.0.0.1:8000; tests with `python manage.py test` (574 pass). QR code and screenshots.

**Slide 28: Appendix B, dashboard demo.** One row per dashboard page (Overview, Occupancy, Building and zones, Research queries, Person track, Security, Metrics and settings), what it shows, and what the role changes. The "confidence" shown on the Overview and in answers is the identity probability of equation (5).

**Slide 29: Project timeline.** Component, status and completion month. No computed values.

**Slide 30: References.** Eleven references [1] to [11]. [8] Bloom 1970, [9] Charikar 2002 (similarity estimation, the angle-agreement property behind equation (2)), [10] Lv et al. 2007 (multi-probe LSH), [11] Deng et al. 2019 (ArcFace).

---

## 8. Where the numbers live in the repository

| Slide | Numbers | Produced by |
|---|---|---|
| 19 | central vs ours event P/R/F1 | `final_review/latex/versions/job_cen.py` (per seed), `run_cen.py` (50 seeds), `aggregate_cen.py` → `agg_cen.json`; baseline = `route_and_recognize.py` with `DSTS_VERIFY_POLICY=verify_all`, `DSTS_SHORTLIST_K=9` |
| 20 | with/without database | `job_nocache.py`, `run_abl.py`, `aggregate_cen.py`; switch `DSTS_VISITOR_DB=off` in `pipeline/route_and_recognize.py` |
| 22 | flags, answers, overhead | `evaluation/evaluate_explanations.py` per seed → `results/explanation_flags.json`; 50-seed means in `aggregate50.py` → `agg50.json`; overhead timed with `overhead.py` (`overhead53.json`) |
| 21 | persona replay | `tools/rbac_matrix.py --seed 42` |
| 21 | 574 tests | `python manage.py test` |
| 14 | role × query | in-process calls to `/api/query` for all 14 query types per role |
| 16, 23 | policy comparison | `run_policies.py` / `job_all.py` (50 seeds) |
| Bloom FPR | 3.56% | `tools/measure_bloom_fpr.py` → `results/bloom_fpr.json` |
| per-seed raw results | event metrics, state metrics, end-to-end rows | `runs/seed_XX/results/*.json` (`event_metrics.json`, `end_to_end_results.json`, `end_to_end_summary.json`) |

Per-seed run folders are about 400 MB each and are deleted after the small result files are saved; they can be regenerated with `python manage.py pipeline --seed N --days 1`.

---

## 9. Honest caveats to know before the review

1. **The baseline is not a pure "search everything for every event".** It keeps the local check and the day cache, so on average it asks 0.52 other buildings per event, not 9. The "9 vs 2" is per *routed visitor lookup*. Older wording "central search touches 100% of galleries" is not what was measured.
2. **"Confidence" in the dashboard is the identity probability** of equation (5), computed from candidate distances only. A high value does not mean the answer is right; the flags are the warning signal.
3. **Slide 22's example is a wrong identification.** It shows what the flags are for.
4. **The permanent visitor pool is not exercised** in one-day runs; only the day cache produces database answers.
5. **The visitor database helps and has a price.** It raises visitor recall (0.894 to 0.913) and cuts the buildings asked by 63%, but a wrong first identification is reused for the rest of the day and is almost never flagged (4.3%).
6. **Flags are a "look again" signal.** 79% of wrong answers and 74% of wrong identifications are flagged, but 39% of flagged answers and 34% of flagged identifications are wrong; visitor-database errors are the blind spot.
7. **Security results describe a tested design on a simulated network**, not a hardened deployment.
8. **Different experiments give different numbers.** The earlier routing paper (20 seeds, adaptive policy, 92.95% on routed visitors) measures routed visitors only; the deck's 91.3% / 95.0% are over all visitor events or all events. Never mix them.
9. **State-level tracking results were removed from the deck and report.** The state evaluation code and numbers still exist (`evaluation/evaluate_states.py`; averages: building F1 0.961, zone 0.941, building and zone 0.925) if a reviewer asks.
10. **Tracking state and masked faces.** The system assumes unmasked faces; masked-face recognition is future work.

# Final review script: Group C3

**Project:** Secure and Role-Aware Explainable Occupant Tracking in Multi-Building Indoor Smart Spaces
**Deck:** `final_review/13th presentation.pptx` (30 slides). **Guide:** Dr. Lakshmi Mohan, whose DSTS paper this work extends.
**Report:** `final_review/latex/Final_Report_v8.tex`. **Numbers guide:** `final_review/SLIDE_VALUES_GUIDE.md`.

This file has four jobs: (1) a word-for-word script for every slide, (2) how every number on the results slides is derived, (3) the confusing pairs of numbers and terms, and (4) the questions a hostile panel will ask, with answers that are honest and still hold up. Assume the panel wants to fail you. Every answer below is written so that it is **true**, because a true answer cannot be caught out.

**How to use it.** Read Parts A to D once. Then, for each slide, read SAY (what to speak), DERIVATION (where numbers come from), CONFUSABLES (what people mix up) and ATTACKS (the cruel questions). Part F is the master question bank, the numbers cheat sheet, the demo script, the open items to fix before the review, and the closing words.

**Timing.** Times in brackets are suggestions for a full delivery (about 40 minutes with demo). If your slot is shorter, see "If you are short on time" at the end of Part A.

---

# Part A. Ground rules, the story, and what we claim

## A1. Eight rules for answering under attack

1. **Know the claim and never exceed it.** Our claim is: *a decentralised design, where each building keeps its own face data and shares only a small summary, keeps almost all the accuracy of a central search (0.21 points lower over all events, 1.1 points lower on visitors) while contacting about 17 times fewer buildings and never pooling face data.* We do **not** claim it is better than a central design. Saying "better" is the fastest way to lose.
2. **Concede first, then reframe.** "You are right that the central search is ahead in all 50 seeds. The question we asked was how much accuracy is given up, and the answer is 0.21 points."
3. **Separate three kinds of statement:** *measured* (we ran it and have a number), *argued* (our reasoning, not an experiment), *not done* (future work or a limitation). Say which one it is.
4. **Always name the scope of a number.** "All events", "visitor events" and "routed lookups" give three different gaps (0.21, 1.1, about 2.5 points). If you say a number without its scope, a panel member will quote a different one back at you.
5. **If you do not know, say so and say how you would find out.** "We did not measure that. We would measure it by ..."
6. **Do not defend the simulation as reality.** Cameras, network, certificate authority and movement are simulated, and photos are replayed. Say it before they do.
7. **Say "about" for rounded numbers, but know the exact ones** (Part F3 has the cheat sheet).
8. **If the premise of the question is wrong, correct it politely and point at the slide.** "On slide 20 the central column has no cache, so ..."

## A2. The project in 60 seconds (memorise this)

> A campus has many buildings. A person is registered in one building and is a visitor in every other. Our guide's DSTS framework tracks people with camera events and keeps a probability of where each person is, building by building, but it identifies visitors through one central master list that holds everybody's face data. That is simple and accurate, but face data cannot be revoked if it leaks, and the list is a single point of failure. We extend DSTS so that each building keeps its own face data and publishes only a compact Bloom filter of hashed codes. A capture that is not recognised locally is routed to a short list of likely buildings, and only those verify it. We also control who may ask where a person is, with two layers of role-based access, and every answer comes with an explanation of the rule, the evidence and the uncertainty. We tested it on 500 simulated occupants in 10 buildings over 50 random populations. It identifies 95.0% of events against 95.2% for a central search, and it asks about 17 times fewer buildings. We never say it beats the central design. We say it gives up very little accuracy for a large gain in locality and privacy.

## A3. The five sentences you must never get wrong

1. "The central search is ahead in all 50 seeds; the overall gap is 0.21 points, on visitors 1.1 points."
2. "The central baseline has **no visitor cache**; ours is shown **with and without** its cache."
3. "Face data is not pooled. A visitor's 20 reference embeddings are copied into the visited building's memory for one day, but only after the home building confirms the match, sealed, and they are zeroed at the day rollover."
4. "The network, cameras, certificate authority and movement are simulated and the photos are replayed. This is a tested design, not a hardened deployment."
5. "The Bloom filter's real false-positive rate is about 3.5%, not the configured 1%. It is only a routing hint, and the exact vote decides."

## A4. If you are short on time

- **Cut to one sentence each:** slides 6 (literature), 8 (tools), 12 (security), 13 and 14 (query tables), 15 and 16 (flags and policies), 26 (team), 29 (timeline).
- **Never cut:** slides 3 and 4 (what space, why camera, the problem), 10 (the routing flow), 19 and 20 (the comparison), 23 (limitations), 24 (conclusion).
- For a 15-minute slot: slides 1, 3, 4, 7, 9, 10, 11, 19, 20, 22, 23, 24, and the demo in 2 minutes.

---

# Part B. The smart space we model, and why a camera

## B1. What the space is, concretely

Say this when someone asks "what exactly is your smart space?"

- **A university-style campus with 10 buildings.** Each building has **50 registered people**: 1 Dean, 10 Teachers and 39 Students. Each Teacher has a class roster of about four students. 500 people in all.
- **Each building has 9 zones:** z1 Entrance, z2 Mail room, z3 Office, z4 Lounge, z5 Conference, z6 Classroom, z7 Cafeteria (reached only from the classroom), z8 Exit, and zT, the transition zone to the outdoors. To go from one building to another a person must pass through zT.
- **Sensing is by camera.** Each camera sighting is an **event**: an identity reading (a face embedding), a zone and a time. The system's job is to say *who* it was and then update *where each person probably is*.
- **A person is "registered" in exactly one building.** In that building they are **local**; in any other building they are a **visitor**. Every visitor is registered somewhere (closed set). People registered nowhere are future work (slide 25).
- **One simulated day, 08:00 to 17:00.** Each person produces 20 events, so 500 x 20 = **10,000 events**. A visitor trip happens with probability 0.20, so about **2,000 events are visitor events and 8,000 local**. These two numbers are set by the event generator, they are not an outcome.
- **What is real and what is simulated.** Real: the face photos (CASIA-WebFace) and the ArcFace model that turns them into embeddings. Simulated: the cameras, the movement between zones and buildings, the network between buildings, the certificate authority. The "camera events" are test photos replayed on a timeline.

## B2. Why a camera, and why not something else

**The honest order of reasons (use this order):**

1. **Our guide's DSTS framework models camera networks.** We extend that work, so we keep its sensing assumption: an event is a recognition at a zone. Changing the sensor would change the thing we are extending and make results incomparable with it.
2. **A camera is unobtrusive.** Nobody has to stop, tap or present anything (slide 3). That is the defining property of the smart space we model.
3. **A camera gives identity and location together.** The sensing reading says *who* and the camera's zone says *where*. Many alternatives give only one of the two.
4. **This is not a face-recognition project.** The face is only the sensor reading. The research question is how identity is found and shared across buildings, how access is controlled and how answers are explained. The pipeline needs only "an identity estimate with a zone and a time", so other sensors can feed it (slide 25), each with its own error measurement.

**The alternatives (this is our reasoning, not an experiment; we ran no non-camera sensor):**

| Alternative | What it gives | Why it does not fit this space |
|---|---|---|
| RFID, badge, ID card | The identity of the card | The person must tap or pass a reader, which is the pause we want to avoid; a card can be lent, lost or forgotten, so it identifies the card, not the person |
| Wi-Fi or Bluetooth beacons | The location of a **device** | A person may carry several devices or none, so it tracks devices, not people; identity is only as good as the link from device to person |
| RF sensing (our reference [7], Wu et al. 2025) | Where **bodies** are, learned from radio signals | It tracks bodies, not identities, so it cannot answer "who"; it has no access control or explanations either (this is in our literature table) |
| GPS | Outdoor position | Does not work indoors |
| Door counters, motion sensors | Counts, presence | No identity at all |
| Distributed hash table, as a way to share identities | Exact key lookup | A sensor reading is noisy and only ever *similar* to the stored one, so an exact key does not exist (this is in the notes of slide 3) |

**The honest costs of a camera (concede them before they are asked):**

- Cameras are privacy-sensitive. That is exactly why the design keeps face data in the building, filters answers by role and audits every decision.
- Accuracy depends on light, angle and occlusion. **Masked faces are not handled** (slide 25), and we say so.
- Our accuracy numbers come from clean web photos, not live surveillance video.

## B3. Attacks on the camera choice

**"Why a camera? A badge is cheaper and almost perfect."**
A badge identifies the card, not the person, and the person has to tap it. We model a space where nobody pauses. The first reason, though, is simpler: the base paper we extend is a camera-network framework. We are not claiming cameras are the best sensor for every space. We are modelling the unobtrusive case, and the pipeline accepts any sensor that gives an identity estimate, a zone and a time (slide 25).

**"Face tracking in buildings is creepy. Why build it?"**
That concern is the motivation for two of our three contributions. A central store of faces concentrates exactly the data that cannot be revoked. So the face data stays in the building, the capture only goes, sealed, to a few likely buildings, the answers are filtered by role (a Student cannot follow another Student), and every decision is audited and explained. We do not claim it is risk-free: we claim no formal privacy guarantee.

**"Did you compare cameras with the other sensors?"**
No. The comparison in this section is our reasoning, plus our literature review. We did not run a non-camera sensor, and we say so.

**"You say it is not a face-recognition project, but your accuracy is entirely the face model."**
Yes, the absolute accuracy is set by the face model. Our claim is relative: both the central search and our design use the **same** embeddings, the same vote rule and the same local check, so the gap between them (0.21 points) measures the effect of routing, not of the face model. A better face model would raise both numbers.

**"Real smart spaces use several sensors."**
Agreed, and that is why slide 25 says the pipeline needs only an identity estimate, a zone and a time. But each new sensor needs its own error measurement, and the access rules and explanations must be re-checked for it. We did not do that.

---

# Part C. Our guide's paper, and exactly what we add

## C1. The base paper

**Mohan and Menon, "Modelling large scale camera networks for identification and tracking: an abstract framework", IET Computer Vision, vol. 14, no. 7, pp. 426 to 433, 2020.** Dr. Lakshmi Mohan, our guide, is its first author. It builds on earlier work on the "three Rs of cyber-physical spaces" (our reference [7] in the report).

What our report and slides say it does (use only these claims; **re-skim the paper itself before the review** so you can answer detail questions):

- It models identification and tracking in a wide-area camera network as a **distributed state transition system**: a *state* holds the probability that each occupant is in each zone, an *event* is a recognition at a zone, and a *transition function* updates the state when an event happens.
- Each building has a **building-level state transition system (BSTS)**.
- Registered occupants are recognised against their own building's database; **visitors are resolved through a centralised master list.**
- It was evaluated on a **two-building campus with 35 registered occupants**, using state-based precision and recall.

## C2. What we keep, what we change, what we add

| | The guide's DSTS paper | Our project |
|---|---|---|
| Sensing | Camera events | **Same.** An event is a camera recognition at a zone and a time |
| Model | States, events, transitions; probability of each occupant in each zone | **Same.** We use the DSTS probability (their Definition 3.3, our formula 5), and building-level BSTS |
| Scale | 2 buildings, 35 registered occupants | **10 buildings, 500 occupants, 10,000 events per day, 50 random populations** |
| Visitors | Central master list holds everyone's references | **Each building keeps its own gallery. LSH codes and Bloom filters rank likely buildings, and only the shortlisted buildings verify** |
| Visitor memory | Not part of the model | **Dynamic visitor database:** presence, day cache, permanent pool, visit pointers |
| Who may ask where someone is | (Not our claim to make for the paper; re-check) | **Two-layer RBAC** with graded disclosure L0 to L3 and 14 query templates |
| Why an answer was given | (Re-check) | **Explanations:** rule, evidence, uncertainty flags, what was withheld, redacted to the caller's level |
| Security between buildings | (Re-check) | **Sealed transfers:** X25519, AES-GCM, Ed25519, replay guard, fail closed |
| Evaluation | State-based precision and recall on a small campus | **Event-level and routing evaluation against a central search, per cohort, 50 seeds; flags; Bloom false-positive rate** |

**The one sentence that ties it together:**

> "We take our guide's DSTS framework as given, camera events, states and zone probabilities, and ask what it takes to run it across many buildings without a central master list, and with access that is controlled and answers that are explained."

## C3. Attacks on the relationship to the guide's paper

**"What is new compared with the base paper?"**
Three things, all about running the same model at campus scale with care for people. (1) Visitor identification without a central master list: LSH and Bloom routing plus targeted verification, evaluated against a central search. (2) Controlled access: two-layer role-based access with graded disclosure, so a query returns only what the caller's relationship allows. (3) Explanations that justify both the access decision and the tracking evidence. We also added the visitor database and sealed transfers, and an evaluation on 10 buildings.

**"Did you extend the framework or just implement it?"**
We extended its handling of visitors and its scale, and we use its model as it is. We did not extend the theory: there is no new theorem or proof. Formula 5 is the DSTS probability, unchanged.

**"Bloom filters and LSH are textbook. Where is the research contribution?"**
The pieces are known (references [12] to [16]); the contribution is the combination for cross-building biometric routing, the measurement of what it costs against a central search, and a finding about the design: the Bloom filter's effective false-positive rate is about 3.5%, not 1%, because LSH codes of different people collide, so the privacy argument must not rest on a small false-positive rate.

**"So you did not improve on your guide's central list."**
Correct, and we do not claim to. The central list is simple and accurate. We measured how much accuracy a distributed design gives up (0.21 points overall, 1.1 on visitors) for keeping face data local and contacting far fewer buildings.

---

# Part D. A short glossary (so words do not trip you)

| Word | Meaning here |
|---|---|
| **Embedding** | A list of 512 numbers describing a face, produced by the ArcFace model |
| **Registered / local** | A person in the building where they are registered; an event there is a *local* event |
| **Visitor** | A person seen in a building other than the one they are registered in |
| **Reference photos** | The 20 photos per person that enrol them in their building (never used as events) |
| **Test photos / events** | The other 20 photos per person; each becomes exactly one event |
| **Vote** | One reference photo "votes" for a person if its angle to the capture is at most 80.43 degrees; a person is accepted with at least 12 of 20 votes |
| **Abstain** | The system names nobody. A miss, but not a wrong answer |
| **Routed lookup** | A visitor sighting the building does not recognise and the cache does not answer, so it must be looked up in other buildings |
| **Shortlist** | The 5 buildings with the highest Bloom score |
| **Day cache** | A copy of a visitor's 20 references held in the visited building's memory for one simulated day |
| **Visit pointer** | A small record at the home building (id, current building, entry time), with no biometrics |
| **Flag** | A warning that the evidence behind an identification or answer was weaker. It does not say the answer is wrong |
| **L0 to L3** | Disclosure levels: nothing, presence only (counts), exact present zone, full history |

---

# Part E. Slide by slide

## Slide 1. Title [0:20]

**SAY**
> Good morning, ma'am and members of the panel. We are Group C3. This is the final review of our project, *Secure and Role-Aware Explainable Occupant Tracking in Multi-Building Indoor Smart Spaces*, guided by Dr. Lakshmi Mohan. The work was done by Ajith Ashok, Arjun Rajesh, D Aditya Kiran and me, Yadhu Vipin.

**ATTACKS:** "Who did what?" Slide 26. Know your own part and each teammate's one-line part.

---

## Slide 2. Outline [0:10]

**SAY**
> We begin with the introduction and the problem, the objectives, the literature and the gap, then our tools, the methodology and the approach. Then the evaluation setup, the results, and the discussion with limitations. We close with the conclusion, future work, the code and demo.

---

## Slide 3. Introduction [2:00]  (the most important slide for "what space, why camera")

**ON THE SLIDE:** background (smart space, camera, campus, central store), motivation (biometric data, controlled access, explanations), a picture.

**SAY**
> A smart space is an indoor environment with sensors and intelligent infrastructure that collect indoor location data, so the space can respond to its visitors and staff. Campuses, hospitals and military bases are examples.
>
> The space we model is a **campus of ten buildings**. A person is registered in one building and is a **visitor in every other**, so tracking has to follow people as they move. We model an **unobtrusive** space, so the sensor is a **camera**: nobody has to stop and tap, unlike with RFID or a badge reader. That is also the sensing assumption of the base paper we extend, our guide's DSTS framework.
>
> Let me be clear about one thing: this is **not a face-recognition project**. The face is only the sensor reading. The question is what happens to *identity* when the space has many buildings, because the building that sees a visitor does not hold that person's data.
>
> The usual answer is one central store of everybody's faces. That is simple and accurate, and we do not say it is a bad design. But it concentrates biometric data that, unlike a password, cannot be revoked if it leaks, and it is a single point of failure. So our first motivation is: **how close can a distributed design come to the central one?**
>
> The second is **controlled access**: anyone who can query tracking data could follow anyone, so answers must be filtered by the requester's role. The third is **explanations**: the system must show why each answer was given.

**CONFUSABLES**
- "Smart space" means the *environment*, not a product. Our concrete instance is the 10-building campus in Part B.
- "Unobtrusive" means no one pauses. It does **not** mean invisible or privacy-free.

**ATTACKS (see Part B3 for the camera answers)**

**"Why a camera and not an RFID card?"** Part B2: first, the base paper is a camera framework; second, a card needs a tap and identifies the card, not the person.

**"You said the central store can be secured well today. So why avoid it?"**
Because the issue is not whether it *can* be secured but what a breach costs: face data cannot be revoked and reissued like a password, and one server is one target. We treat the central design as the **reference** and measure how close a distributed design comes. We are not claiming the central design is wrong.

**"Why not a distributed hash table or a blockchain to share identities?"**
A distributed hash table needs an *exact* key, and a camera reading is noisy: it is only ever similar to the stored one, never equal, so exact lookup does not apply. That is why we use similarity-preserving hashing (LSH). We did not evaluate a hash table or a blockchain; that is an argument, not a measurement.

**"Why a campus? Why 10 buildings?"**
A campus has the property we need: a person is registered in one building and a visitor in every other, so cross-building identity is a real question. Ten buildings of fifty people is a size a laptop can run 50 times; scale beyond that is untested (slide 23).

---

## Slide 4. Problem definition [1:30]

**ON THE SLIDE:** existing systems and limitations; the problem statement.

**SAY**
> Existing systems localise and track people **inside one site**, and distributed trackers associate targets across cameras. But **how a person registered in another building is identified**, and **who may query the locations**, remain open. Our base paper, DSTS, resolves visitors through a centralised master list.
>
> So our problem is: identify and track occupants across a wide-area, multi-building indoor environment, while giving **secure, role-based and explainable** access to where they are and have been, and justify **both the access decisions and the tracking evidence** behind each answer.
>
> It has three parts: find a visitor's home building without pooling face data; control who may ask; and explain every answer.

**CONFUSABLES**
- "Tracking evidence" means the events and votes behind an *identification*; "access decision" means the *policy rule* that allowed or refused a query. Both are explained.

**ATTACKS**

**"Is this a real problem or one you invented?"**
Any multi-building space with cameras has it: the capturing building does not know where a visitor is registered. The central list is the standard answer, and it concentrates face data. Whether a distributed alternative is *worth* its accuracy cost is what we measured.

**"Why is 'who may ask' part of a tracking problem?"**
Because tracking data is exactly what a stalker wants. A system that can say where anyone is, to anyone who asks, is a surveillance tool. Access control is therefore part of the problem, not an add-on.

**"Is the problem statement too broad for one project?"**
It has three linked parts and each has its own evidence: routing (accuracy and cost), access (the 64-request replay and the tests), explanations (flags and faithfulness tests). We do not claim each is deep; we claim they work together end to end.

---

## Slide 5. Objectives [1:00]

**SAY**
> We set six objectives. One, extend DSTS to multiple buildings. Two, a role-based query interface in which each role sees only the people, and the level of detail, its relationship allows. Three, explainable responses. Four, design the LSH and Bloom-filter mechanism for visitor lookup. Five, security: encrypted transfers, role-based filtering and audit logging. Six, evaluate on precision and recall, on communication cost, measured as buildings contacted per lookup, and on explanation quality. All six are achieved, with one caveat: the network between buildings is simulated.

**ATTACKS**

**"Define 'achieved'."** Each objective has its evidence on slide 24: for example objective 4 is the measured result that routing asks 2 buildings instead of 9 at a cost of about 0.2 points, and objective 2 is every policy cell tested plus the 64-request replay.

**"Your communication cost is 'buildings contacted'. You did not measure network time."**
Correct. We measured how many buildings a lookup contacts, not milliseconds, because the network is simulated in one process. Buildings contacted is a proxy for messages and for how much data is consulted. We did measure the extra time to build an explanation (about 13%), on a single machine.

**"Objective 1 says 'extend DSTS'. What did you extend?"**
Its visitor handling and its scale (Part C). We did not change its model or add theory.

**"Objective 5, 'security', is just a library."**
The primitives come from the `cryptography` library (X25519, AES-GCM, Ed25519). The protocol is ours: a fresh key per message, a signature over header, nonce and ciphertext, replies bound to their request, certificate checks, a replay guard, and fail-closed handling. We tested tampering, replay, wrong sender and revoked certificates. It has not been audited, and the network and certificate authority are simulated.

---

## Slide 6. Literature review [1:00]

**ON THE SLIDE:** seven papers: five camera tracking papers [1] to [5], the base paper [6], and an RF tracking paper [7], each with its contribution, its demerit, dataset and metrics.

**SAY**
> We reviewed fourteen works in the report; this table shows the seven closest. Papers one to five are edge-camera group identification, localisation and tracking and distributed multi-camera association. Paper seven, from 2025, tracks people indoors from radio signals. They all work **inside one site**, and none gives cross-building identity, building-level probabilistic state or role-aware access. Paper six is the base paper: the DSTS framework, tested on a two-building campus, which resolves visitors through a central master list.

**DERIVATION:** every number in this table (for example F1 of about 90%, MOTA 88.6%, 1.41 m error, 7 people for 35 minutes, 35 registered occupants) is **taken from the cited paper**. None is ours.

**ATTACKS**

**"Can you compare your accuracy with these papers?"**
No. They have different data, tasks and metrics (groups, poses, tracking accuracy). The fair comparison for our project is the **same pipeline with a central search**, which is what slides 19 and 20 do.

**"Why is an RF paper in a camera project?"**
It is the nearest alternative sensing modality. It tracks **bodies, not identities**, which is why it cannot answer "who". It supports our choice of sensor.

**"Your 'demerit' column is a judgement."**
It is our reading of what each paper does not address relative to our problem. It is not a criticism of the papers on their own goals.

---

## Slide 7. Research gap [1:00]

**SAY**
> From the literature we found three gaps. **Cross-building identity:** tracking work stays inside one site; finding a visitor's home building is not addressed. **Routing effectiveness:** Bloom filters with LSH are efficient but unproven for cross-building biometric routing. **Role-aware, explainable querying:** nothing controls who may query a person's location, and answers carry no reasoning. For each gap, the last column says what this project does.

**ATTACKS**

**"'Unproven for biometric routing': you only proved it on your own simulation."**
Fair. We measured it on CASIA-WebFace embeddings in a simulated 10-building campus, against a central search on the same embeddings. We do not claim it holds on live video or at larger scale; both are limitations on slide 23.

**"Is 'nothing controls who may query' really true of the literature?"**
Of the fourteen works we reviewed, none treats access control over tracking queries together with identification and explanations. We did not claim to survey the whole field; we claim this about our reviewed set.

---

## Slide 8. Datasets, platforms and tools [1:00]

**SAY**
> Our data is **CASIA-WebFace**: 500 identities with 20 reference photos and 20 test photos each, 20,000 embeddings, split over ten simulated buildings of 50 people. Faces are processed with InsightFace: the SCRFD detector and the ArcFace model, giving 512-number embeddings. Everything runs on a laptop CPU, and a full run of 10,000 events takes about eight minutes. Routing, the verifier and the tracking databases are written from scratch with NumPy and pandas. Security uses the cryptography library, with FastAPI and a static dashboard. There are 574 automated tests, and populations are seeded so every result is reproducible.

**DERIVATION**
- 500 identities x (20 reference + 20 test) = 20,000 embeddings.
- 10 buildings x 50 people = 500.
- 574 tests: 573 pass and 1 is skipped (it needs the optional masked building). Slide 21 gives the split by area.

**ATTACKS**

**"CASIA-WebFace is web photos, not surveillance video."**
Correct. Our absolute accuracy describes clean photos. Our claim is the relative gap to a central search on the same embeddings. That gap measures routing, not photo quality.

**"ArcFace was trained on large web face data that may overlap CASIA's identities, so accuracy is inflated."**
Possible, and it is a limitation in our report. It would raise both the central and our numbers alike, so it does not bias the comparison, but it does mean absolute accuracy could be lower for people the model has never seen.

**"Could near-identical reference and test photos make this too easy?"**
We split each person's 40 photos into 20 references and 20 test photos, and no photo is used for both. We did not measure how similar the two halves are, so we cannot rule out that web photos of one person come from similar sessions. We say the absolute numbers are for clean photos.

**"Eight minutes for 10,000 events, is that real time?"**
It is simulation throughput for the whole run on a laptop CPU, with embeddings already extracted. We make no real-time claim.

**"574 tests, all passing?"**
573 pass and 1 is skipped, because it needs the optional masked building. We corrected that claim in the deck.

---

## Slide 9. Architecture and what is new [1:30]

**ON THE SLIDE:** the architecture figure and six novelty points.

**SAY**
> This is the architecture. **Every building holds its own gallery and its own state.** Only **Bloom filters and encrypted replies cross buildings.** The novelty is in six parts.
>
> One, **LSH plus Bloom routing**: a building publishes only a compact Bloom filter, with no ids and no embeddings, so a capture can rank buildings without exposing any gallery. Two, **targeted peer verification**: only the shortlisted buildings compare faces. Three, a **dynamic visitor database** with presence, a day cache, an entry counter and a permanent pool. Four, **role-based access control** with graded levels of detail. Five, **explainable answers**. Six, **encrypted transfers**, signed and replay-protected.

**ATTACKS**

**"What exactly leaves a building?"** (Know this table.)

| Item | Leaves the building? |
|---|---|
| Bloom filter (58,469 bits, 7.3 KB, no ids or embeddings) | Yes, published to all peers; one-way |
| The capture's embedding, to the **shortlisted** buildings only (about 1.8 on average, at most 5), sealed | Yes, but only when the local check fails |
| The matched person's 20 reference embeddings, sealed, **after a confirmed match**, into the visited building's day cache | Yes, one day, then zeroed |
| Zone probabilities and events | No |
| Visit pointer (id, current building, entry time, no biometrics) | Read by location queries |

**"So face data does leave a building."**
Yes, in two controlled cases, and we say so: the capture goes sealed to a few likely buildings, and a confirmed visitor's references are copied for one day. What never happens is **pooling**: there is no central store of everyone's faces. Our claim is "without pooling face data", not "face data never moves".

**"You call it 'decentralised', but the hyperplanes, the filter size and the certificate authority are shared."**
Fair. Better wording is distributed architecture with decentralised control. The shared items are configuration (hyperplane seed, Bloom parameters) and a trust anchor (the campus certificate authority, which is simulated). No building holds another's data.

**"Is the Bloom filter itself a leak?"**
It is one-way and holds hashed LSH codes, not ids or embeddings. But membership can be tested, and at a 3.5% effective false-positive rate a positive is ambiguous. We claim **no formal privacy guarantee** and say the privacy argument rests on the one-way encoding and on exact verification.

---

## Slide 10. Identification and routing: what happens to a captured face [2:00]

**ON THE SLIDE:** an eight-step flow in words, in two rows: steps 1 to 4 in the capturing building, steps 5 to 8 against the other buildings, ending in "identified" or "unidentified (abstain)".

**SAY** (walk along the arrows)
> This is what happens to one captured face. **One:** the camera sees a face and the building first compares it with its **own people**. If it recognises the person, we stop here. Most events end here: all the local ones.
>
> Otherwise the face may belong to a visitor. **Two:** the face is turned into a list of numbers that describes its features, so faces can be compared. **Three:** those numbers are compared with several sets of **random dividing planes**, the same in every building. Each comparison gives a yes or a no. **Four:** the answers of each set are packed into a short **code**, so similar faces get the same codes. We also flip the least certain answers, to catch near misses.
>
> **Five:** each code is looked up in the compact **Bloom filter** that every other building has published in advance. A filter can only say "probably there" or "definitely not there". This happens on our side; **nothing is sent yet**. **Six:** a building earns a point for every set of codes that finds a match. **Seven:** the buildings with the most points form a **shortlist of five**, best first.
>
> **Eight:** only the shortlisted buildings receive the face, sealed, and each compares it with its own people by **voting** over reference photos. If one confirms, the person is identified. If none does, the face is left **unidentified**. The Bloom filter is only used to *rank*; it never identifies anyone.

**IF THEY ASK FOR THE MATHS** (the equations are no longer on a slide; they are in the report and here)

| Step | What it computes | Numbers |
|---|---|---|
| Embedding | ArcFace gives a 512-number vector, normalised, centred on the mean face of the reference photos, normalised again | 512 dimensions |
| Hyperplane tests | Each test is the sign of a dot product with a random vector: bit = 1 if w . e >= 0 | K = 11 bits per slot, L = 122 slots, so 11 x 122 = **1,342 hyperplanes** |
| Code | The 11 bits of a slot are read as a binary number, 0 to 2,047 | 2^11 = 2,048 possible codes per slot |
| Bloom item | item = slot x 2,048 + code, so a code only counts in the same slot | |
| Multi-probe | Also test the codes with each of the **3 weakest bits** (smallest absolute dot product) flipped, one at a time | 4 items per slot, **488 lookups** per building |
| Bloom filter | Each item is hashed to 7 positions (double hashing) in a 58,469-bit array; present only if all 7 bits are 1 | sized for 50 people x 122 slots = 6,100 items at a 1% target; about 51% of bits set |
| Score | Number of **distinct slots** with at least one matching probe | scale 0 to 122 |
| Shortlist | Top 5 scores | |
| Verify | One vote for each of the person's 20 reference photos within **80.43 degrees** of the capture; accepted with **at least 12 votes**; ties broken by the smallest single angle | |

**Why it works (one sentence):** two faces share an LSH bit with probability 1 - (angle / pi) (Charikar 2002), so close faces get the same bits, hence the same codes. With 122 slots and 3 probes, a few flipped bits caused by photo noise are tolerated.

**A real worked example** (one visitor event from the seed 42 run; one event, not an average): a person registered in building 9, captured in building 7. The exact codes alone matched the home building in only **9 of 122 slots**; with the 3 extra probes the home building reached a score of **33**, while the other buildings scored **12 to 24** (by chance). The shortlist was buildings 9, 2, 1, 10, 3. Building 9, asked first, confirmed the face, so **1 building** was contacted.

**DERIVATION of the numbers on the later slides that come from this flow**
- *Routing recall 90.6%:* share of routed visitors whose home building is in the top 5 of the shortlist. The other 9.4% cannot be identified, so they abstain. This is the main source of the decentralised design's extra misses.
- *Buildings contacted per routed lookup, 1.77:* mean number of shortlisted buildings actually asked. In the sequential policy it stops at the first confirmation.

**CONFUSABLES**
- **Bloom score vs votes vs probability.** The Bloom score (0 to 122) only ranks buildings. Votes (0 to 20) decide acceptance. The zone probability (formula 5, slide 11) is a different thing used for tracking.
- **"Probably there"** does not mean "this face is enrolled". A Bloom hit can be a collision.

**ATTACKS**

**"Why random hyperplanes and not a learned hash?"**
They need no training data, so every building can use the *same* planes without sharing any faces, and the angle-preserving property is proven (Charikar 2002). A learned hash would need data from all buildings to train, which defeats the purpose.

**"How did you choose K = 11 and L = 122?"**
From an earlier sweep on one seed, which is **not** a final result: K = 9 gave a 10.5% effective false-positive rate; K = 11 with L = 122 gave about 3.6% at a 7.1 KB filter; K = 13 with L = 267 improved the rate to 1.6% but doubled the filter to 15.6 KB; and accuracy plateaued around K = 13. We took the smaller filter. We did not re-tune per seed.

**"The Bloom filter reports 12 to 24 hits for the *wrong* buildings. Doesn't that make routing useless?"**
No. It only needs the home building to score **higher** than most others: 33 against 12 to 24 in the example. The shortlist of 5 gives room for ranking errors, and the vote is exact. The home building is in the shortlist for 90.6% of routed lookups.

**"What happens to the 9.4% where the home building is not in the top 5?"**
None of the shortlisted buildings confirms, so the face is left unidentified. That is an abstention, not a wrong answer. It is the main reason the decentralised design misses more than the central search, and it is why abstentions, not wrong people, make up the gap on slide 20.

**"Does the capture leak to the shortlisted buildings?"**
Yes. The capture's embedding goes, sealed, to the shortlisted buildings only: about 1.8 on average, at most 5, and only when the local check fails. That is the privacy-for-accuracy trade, and the embedding is biometric. It is not sent to all nine.

**"Why does the building vote over 20 references instead of taking the nearest photo?"**
One odd reference photo cannot decide. A person needs at least 12 of 20 references to agree within 80.43 degrees. The nearest single photo is used only as a tie-break and as a warning flag (slide 15).

**"How was 80.43 degrees chosen?"** See Part F6, item 1 (it is a fixed parameter in every building's config; prepare your honest answer for how it was set).

---

## Slide 11. Dynamic visitor database: caching and visit pointers [1:45]

**ON THE SLIDE:** presence, day cache, permanent pool, visit pointers, and formula 5.

**SAY**
> A visitor passes through three stages. **Presence:** who is inside a building and in which zone, until they leave or the day ends; on every event, the zone probabilities of the visitors inside are updated with formula 5. **Day cache:** once the home building confirms a visitor, a copy of their twenty reference photos is held in the visiting building's memory for one simulated day. Later sightings that day are matched there, with **no routing and no other building asked**, and the cache is cleared at the day rollover. **Permanent pool:** every arrival is recorded, and after three entries in seven days, or eight in thirty, the next entry stores the person permanently.
>
> **Visit pointers** link a visitor to their home building. On arrival the home building creates a small record saying where its visitor is now: id, current building, entry time. **It holds no biometrics.** The second event in the transition zone closes it. A location query then follows the open pointer to the one building where the person is, instead of asking every building.
>
> A visitor waiting in the transition zone is left out of other people's updates until seen outside it.
>
> Formula 5 is the DSTS probability of the base paper: each candidate's probability falls with its distance, scaled by the spread of all candidate distances.

**DERIVATION: formula 5** (the only formula still on a slide)
p_i = exp(-d_i^2 / 2 sigma^2) / sum over all candidates l of exp(-d_l^2 / 2 sigma^2)
- d_i = the **smallest angle** (degrees) between the event and any of candidate i's 20 references (confirmed in `pipeline/phase4_state.py`: `distances = {o: float(angles[i].min())}`).
- sigma = the standard deviation of all candidates' distances; if sigma = 0 the result is uniform.
- It is DSTS Definition 3.3, **unchanged**. The Bloom score, the votes and the ground truth never enter it.
- *Example from the dashboard screenshot:* confidence 0.970 for the winner. That is formula 5, **not** a probability that the answer is right.

**CONFUSABLES**
- **Day cache vs permanent pool vs visit pointer.** The cache holds 20 reference embeddings in *memory* at the *visited* building for one day. The pool holds embeddings on *disk* for *frequent* visitors. The pointer holds **no biometrics** and lives at the *home* building.
- **"Formula 5" numbering:** the slide says "formula 5" because formulas 1 to 4 used to be on a slide of their own. They are now explained in words on slide 10 and in the report. If asked "where are formulas 1 to 4", point to slide 10's maths table above.
- **Identity probability vs confidence.** The dashboard calls formula 5 "confidence". It is not calibrated, and the flags are the real warning.

**ATTACKS**

**"You say face data is not pooled, but you copy 20 reference embeddings into another building."**
That is the day cache, and it is the one place a visitor's references move. It happens only **after the home building confirms the match**, sealed, to the building the person is actually in, held in memory, and zeroed at the day rollover. There is still no central store. Our claim is "no pooling", not "no movement". The report's table of what each building keeps and shares lists it.

**"The cache keeps a *wrong* identity for the whole day. That is dangerous."**
Yes, and it is our biggest weakness. About **14 wrong identifications per day** come from the cache, and only **4.3% of them carry a warning flag**. It is on slide 23. The cache also gains 2.0 points of visitor recall, so it is a trade. We do not hide it.

**"Did you test the permanent pool?"**
No. Our runs are one simulated day, so every database answer comes from the day cache. The pool needs visits across several days and is not exercised here.

**"What if a visitor leaves without a clean exit?"**
Then the pointer and presence stay open until the day ends. That is in our limitations.

**"Why 3 in 7 days or 8 in 30?"**
They are configuration defaults for what counts as a frequent visitor. We did not tune them, and they are untested in one-day runs.

---

## Slide 12. Security and role-based access control [1:30]

**SAY**
> Security has two parts. **Between buildings**, every message is encrypted with a fresh key and signed. Keys are certified by a campus authority, a replay guard rejects old or repeated messages, and any failed step means no reply and nothing admitted. **Within a building**, access has two layers. The first checks **who is signed in and which building they belong to**. The second checks the **role's relationship to the person** and sets the level of detail, from L0 to L3. A **Dean** sees their own campus's teachers and students, and other Deans, at full history. A **Teacher** sees their assigned students at full history, in any building. A **Student** sees themselves in full and their assigned teachers at the exact current position only. Sign-ins expire after an hour, repeated failed logins lock the account, and every decision is logged.

**DERIVATION**
- *Levels:* L0 nothing (refused with a reason code); L1 counts and presence, no names or positions (granted for building and zone aggregates); L2 exact zone, time and confidence, present only; L3 full trajectory and dwell times.
- *Sealed message:* fresh X25519 key per message, HKDF-SHA256 to derive the symmetric key, AES-GCM with the whole header as associated data, Ed25519 signature over header, nonce and ciphertext, replies bound to their request. Replay guard: accepted once per nonce and only within 300 seconds, checked **after** the signature so the timestamp is authenticated.

**CONFUSABLES**
- **Layer 1 vs Layer 2.** Layer 1 asks "are you who you say you are and in which building"; Layer 2 asks "what is your relationship to this person".
- **Authentication vs authorisation vs encryption.** Tokens and lockout are authentication; the persona policy is authorisation; sealed transfers protect data between buildings.
- **L1 is not unused.** It is granted for aggregate answers (counts). Do not say "no rule grants it".

**ATTACKS**

**"RBAC is textbook (Sandhu 1996). What is yours?"**
It is relationship-based on top of roles: the decision depends on the role **and** the caller-target relationship (own campus, roster, assigned teacher, another Dean), and the answer is graded in detail, not just allowed or denied. And every answer's explanation is cut to the same level.

**"Why can a Dean see everyone on campus, even a student in another building? Isn't that excessive?"**
It is a policy choice, modelled on a campus administrator, and it is configurable in principle. We show how it is enforced and tested, not that this policy is the right one for every institution. A Dean is **not** allowed to see people of other campuses, and every request across buildings is refused except a Dean following another Dean and a Teacher following a rostered student.

**"Why can a Student see their teacher's position?"**
For office hours. It is a policy choice, limited to the **present** at exact-zone level (L2) and never a history, so asking many times cannot rebuild a track. A student never gets another student or a Dean.

**"The demo lets anyone act as any role."**
Yes. The dashboard's demo mode signs in as a chosen role for demonstration, by design. In normal mode a user signs in with a per-person credential, tokens expire after an hour, and repeated failures lock the account. We say in the limitations that token secrets and lockout counters are per process.

**"You wrote the policy and the tests. Isn't the verification circular?"**
Partly. The 64-request replay and the tests check that the **implementation matches the policy we wrote**. They are not an independent audit and not a penetration test. Slide 21 says so.

**"Is the cryptography yours?"**
The primitives come from a standard library. The protocol around them is ours and is tested for tampering, replay, wrong sender or recipient and revoked certificates. The certificate authority is a simulated substitute whose signature is a message authentication code, not a real PKI. There is no TLS in the application.

---

## Slide 13. Query types [0:45]

**SAY**
> The system answers 14 query templates. A query asks about a **building**, a **person** or a **zone**. Building queries Q1 and Q2 return yes or no with counts. Person queries ask where someone was (Q6), what they did over a window (Q3 to Q5, Q7 to Q9, Q11), or return the full track. Zone queries Q10, Q12 and Q13 return **lists of people**. Every answer comes with an explanation card.

**CONFUSABLES**
- *Q6 "last sighting at or before t"*: the answer is the last **identified sighting** at or before time t, not a live position.
- *Q10* lists people whose **last sighting at or before t** was in that zone.

**ATTACKS**

**"Why 14 templates? Where do they come from?"**
Q1 to Q13 are point-in-time and boolean questions about state; the dashboard labels them "research-paper query templates", and TRACK (the full trajectory) is the person-track view we added. They are our implemented set, not a standard. **Before the review, ask Arjun (who built the query engine) exactly which paper the Q1 to Q13 templates come from, so you can name it if asked.**

---

## Slide 14. Who may use which query [1:30]

**SAY**
> This table shows what each role gets, and we obtained it by **sending every query to the running system** for a Dean, a Teacher, a Student and an outsider, a registered person of another building. **Person queries follow one rule:** a Dean sees anyone on their own campus, and other Deans; a Teacher sees their roster students wherever they are; a Student and an outsider see only themselves. **There is one exception:** a Student may ask where an *assigned teacher* is, but only for the present moment at exact-zone level, never the teacher's history. **Building queries** are aggregates: everyone gets counts for their own building, and only the Dean also gets the people behind the count. **Zone queries** reveal an exact zone per person, so each person is checked individually: a Dean sees everyone of the campus in that zone, a Teacher sees only roster students and the rest are counted, and Students and outsiders are refused. Anything about **another building** is refused, except a Dean following another Dean and a Teacher following a rostered student.

**DERIVATION:** each cell is the decision the system actually returned for that role and query (answered or refused, rule code, disclosure level). Run by `tools/rbac_matrix.py` and a wider script over all 14 queries on seed 42 (building 5: Dean 0002253, Teacher 0000582, Student 0000205, outsider 0000099).

**CONFUSABLES**
- **"Outsider"** is a registered person of **another building** (here, building 1), not an unregistered stranger.
- **L1 vs L2 for the Teacher on zone queries:** if nobody in the zone can be named, the answer is counts only (L1); if roster students are named, it is L2.
- **"Refused" still returns an answer object:** a rule code and level L0, and no explanation content.

**ATTACKS**

**"Why does the Teacher see roster students 'wherever they are'? Isn't that surveillance?"**
It is the class-teacher model: a teacher is responsible for their roster. It is a policy choice. The point of the project is that the policy is **enforced, graded and explained**, so an institution can choose a stricter one.

**"What stops someone rebuilding a student's history by asking many times?"**
A Student's location query is always answered for the **present**, whatever time is asked, so repeated queries cannot rebuild history. History (L3) requires a role with that right over that person.

---

## Slide 15. Explainable responses [1:30]

**SAY**
> Every answer carries five things: the **rule** that decided it, the **evidence** it rests on, what was **looked for and not found**, **uncertainty flags**, and what the caller's level **withheld**. The explanation is built from the **same events that produced the answer**, never recomputed, and then cut down to the caller's level, so it can never show more than the answer does. The table defines the six flags, warnings that the evidence was weaker. The strongest is "winner not nearest": the accepted person was not the one with the closest photo.

**DERIVATION: the six flags**

| Flag | Raised when | Threshold |
|---|---|---|
| `vote_winner_not_nearest` | the accepted person was not also the person with the single closest photo (strongest warning) | exact comparison |
| `low_votes` | fewer than 16 of 20 votes, although 12 are enough | votes < 16 |
| `verified_by_lower_ranked_building` | the confirming building was not first on the shortlist | rank > 1 |
| `narrow_routing_margin` | the Bloom score gap between the top two buildings is small | gap < 5 (out of 122) |
| `stale_observation` | a point-in-time answer rests on a sighting older than 60 minutes | age > 60 min |
| `pointer_based` | a value comes from a visit pointer with no sighting behind it | exact |

Flags are computed from facts **saved at identification** (votes, rank, Bloom gap, whether the winner was nearest). They **never use the true identity**. The first four concern identifying a person; the last two concern the answer to a query.

**CONFUSABLES**
- **A flag is not "wrong".** It means "weaker evidence, look again". Slide 22 measures how often a flag goes with a wrong answer.
- **Explanation vs model explainability.** We explain the **system's evidence trail** (rule, events, votes), not why ArcFace thinks two faces look alike.
- **Rule vs evidence.** The rule is the access or answer logic; evidence is the list of events used.

**ATTACKS**

**"Why 16 votes, gap 5 and 60 minutes?"**
They are warning thresholds we chose. We did not tune them or run a sensitivity study. Their usefulness is what slide 22 measures, and we say so.

**"Is this really 'explainability'? It does not explain the face model."**
Correct, and we do not claim it does. We explain the **access decision and the tracking evidence**: why the system answered, from which events, and how weak they were. That is what a panel auditing a surveillance system needs. Explaining a neural network's internals is a different problem.

**"A faithful explanation just restates the answer."**
It restates the *evidence*, and the tests (40 cases with a yes, a no and an empty answer for each query type) check that every piece of evidence is a real event and that **redoing the answer from the evidence gives the same answer**. That excludes a decorative explanation.

---

## Slide 16. Shortlist verification policies [1:00]

**SAY**
> Once the filters have ranked the buildings, a shortlist of five is verified, and there are three ways to do it. **Verify all:** every shortlisted building verifies and the most votes wins; it does not depend on the ranking, but every lookup contacts five buildings. **Sequential:** buildings are asked in rank order and the first confirmation ends the search; it contacts the fewest buildings, but a wrongly ranked building can confirm first. **Adaptive:** if the top two Bloom scores are close, all five verify, otherwise in order; extra contacts only where the ranking is uncertain. The main results use the **sequential** policy.

**DERIVATION** (from the report's policy table; routed visitor lookups, 50 seeds)

| Policy | Correct | Wrong person | Abstained | Buildings contacted | Accuracy, all events |
|---|---|---|---|---|---|
| Sequential (used everywhere) | 83.89% | 2.95% | 13.16% | 2 | 95.01% |
| Adaptive (gap 5) | 84.49% | 2.29% | 13.22% | 3 | 95.06% |
| Verify all | 84.42% | 2.36% | 13.22% | 5 | 95.04% |

- All three leave about 13% of routed lookups unidentified, because an abstention means **no shortlisted building confirms**, which does not depend on the order of asking.
- They differ in the **wrong-person** rate: sequential is about 0.6 points worse.
- Over all 10,000 events the policies differ by at most 0.05 points, because routed lookups are only about 6% of events.

**ATTACKS**

**"Why report the weakest policy as your headline?"**
Because it contacts the fewest buildings (2) and its errors are exactly what the flags are built to expose. The better policies buy about 0.6 points on routed lookups (0.05 points overall) for 3 to 5 contacts instead of 2. We show all three, so the trade is visible.

---

## Slide 17. Proposed approach: algorithm, training and testing, hyperparameters [1:00]

**SAY**
> For each sensor event: read the identity, zone and time; vote over the building's own twenty references; if not accepted, compute the codes and score the peers' filters; verify in the shortlisted buildings over sealed channels; update the zone probabilities; open or close the visit pointer and cache the visitor; and answer queries by role with the explanation.
>
> **No model is trained.** The pretrained detector and ArcFace give the embeddings, unchanged. Twenty photos per person enrol the building; the **other twenty**, different photos, are the events, each used once. We test **once per population, over 50 random populations**, on one simulated day of 10,000 events. The table lists the settings.

**DERIVATION: the hyperparameters**

| Parameter | Value | Meaning |
|---|---|---|
| LSH bits K / slots L | 11 / 122 | bits per code / number of independent code tables |
| Bloom target false-positive rate | 0.01 | sizing target; the **measured** rate is about 3.5% |
| Reference photos per occupant | 20 | enrolment photos |
| Accept angle | 80.43 degrees | a reference "votes" if its angle to the capture is at most this |
| Minimum votes | 12 of 20 | to accept a person |
| Shortlist size / probes | 5 / 3 | buildings verified / extra bits flipped per slot |
| Visitor promotion | 3 in 7 days or 8 in 30 | when a frequent visitor is stored permanently |

**CONFUSABLES**
- **"Training and testing"** here is the *protocol*: enrolment photos vs event photos. There is no training of a model and no validation set.
- **Reference vs test photos:** 20 each per person, disjoint, so a test photo is never in a gallery.

**ATTACKS**

**"No training? Then what is the 'approach'?"**
The approach is the pipeline: local vote, LSH routing, targeted verification, tracking, access, explanation. We deliberately use pretrained face models unchanged so the comparison measures the **routing**, not a better matcher.

**"Did you tune hyperparameters on the test photos?"**
Our protocol says no model is trained or tuned on the test photos, the mean face and the Bloom filters use reference photos only, and the thresholds are the same constants in every building and every seed. We did **not** run a sensitivity study of the thresholds. **Before the review, confirm for yourself how 80.43 degrees and 12 votes were originally set** (Part F6, item 1), and answer exactly that.

**"50 populations, one test each. Is that enough?"**
The per-seed spread is small (the gap has a standard deviation of 0.08 points and a 95% interval of 0.02), so the means are stable. See the next slide for what a "population" is, and its limits.

---

## Slide 18. Evaluation setup and criteria [1:30]

**SAY**
> We use **500 people in 10 buildings**, with **50 seeded populations**; every result is an average over them. One simulated day has **10,000 events**, one per test photo: **8,000 local and 2,000 visitor**.
>
> The **baseline is a central search**: the same pipeline and the same local check, but **with no visitor cache**, and every visitor sighting is looked up by a direct search of **all nine other buildings**, with no hashing or Bloom filters. Ours is the full decentralised system, with its cache.
>
> We use one-versus-rest metrics over 500 identities. **TP** is when the named person is the true one. **FP** is when someone else was named. **FN** is when the true person was not named, either because a wrong person was named or because the system said nothing. Precision, recall and F1 follow. We report them per cohort, because **accuracy alone hides abstentions and wrong answers**: a wrong identity is both a false positive and a false negative, while an abstention is only a false negative.

**DERIVATION: a full worked example** (the 50-seed means, all events)
- Every event is one of: correct (TP), wrong person (counts as FP **and** FN), or abstain (FN only).
- TP = 9,501.4, FP = 155.5, FN = 498.6. Check: TP + FN = 10,000 events.
- Wrong identifications = FP = 155.5. Abstentions = FN - FP = 343.1.
- Precision = 9,501.4 / (9,501.4 + 155.5) = **0.984**. Recall = 9,501.4 / 10,000 = **0.950**. F1 = 2 x 9,501.4 / (2 x 9,501.4 + 155.5 + 498.6) = **0.967**.
- Recall equals the share of events identified correctly, so "recall" and "identification accuracy" are the same number here.
- *Why TN is not used:* TN = events x 500 labels - TP - FP - FN, which is millions, so accuracy computed with TN is about 1 and means nothing.

**DERIVATION: how a "seed" works**
- A seed fixes: which of the 500 identities go into which building (a seeded permutation, 50 per building), who is Dean, Teacher or Student, the teacher rosters, and the event timeline (who is seen where and when).
- The 500 people, their photos and the embeddings are the **same in every seed**.
- Each metric is computed **inside** a seed first; the slide shows the **mean of the 50 per-seed values**.

**CONFUSABLES**
- **"Independent random populations":** the people and photos are the same 500 in every seed; what is random is the *assignment to buildings and the event day*. Say "50 random assignments of the same 500 people", not "50 different groups of people". The confidence interval covers assignment variability only, not variability from the face model or from sampling new people.
- **Local vs visitor** is decided by where the person is **registered**, not by who they are.
- **Baseline vs ours:** the baseline has **no cache**; ours has one (and slide 20 also shows ours without).
- **Accuracy vs recall vs precision:** see the example above.

**ATTACKS**

**"Is your central baseline a real central database?"**
It is the *same pipeline* with a direct search over all nine other buildings' galleries, as if they were pooled, so the matcher is identical and any difference is caused by routing. A real pooled database would be one server doing one search, with zero buildings contacted and all the face data in one place. We use "buildings contacted" to measure how much of the data a lookup has to consult, not network latency.

**"You removed the cache from your baseline. Isn't that rigging it?"**
It was the other way round. Our first baseline had the cache too. We removed it so the cache, which is a decentralised feature, is not credited to the baseline, and we show ours with and without the cache so nobody has to take our word. With the cache on the baseline, the numbers are: all-event accuracy 95.17% (gap 0.16 points), visitor recall 0.921, and 5,162 buildings asked per day. The choice of baseline moves the accuracy gap by 0.05 points and the buildings-asked ratio from about 5 times to about 17 times. Know both.

**"Are 50 seeds statistically independent?"**
They are independent *assignments*, not independent people (see above). We report the paired difference per seed (central ahead in 50 of 50, range 0.09 to 0.43, 95% interval 0.02), which tells you the gap is real and small, not that it generalises to other people.

**"Why not a confusion matrix over 500 identities, or ROC curves?"**
A 500 x 500 matrix is unreadable and the answer is almost entirely on the diagonal. The system makes a hard decision (identify or abstain) with a fixed threshold, so we report the confusion counts (TP, FP, FN) per cohort. ROC would vary the threshold, which we did not do.

---

## Slide 19. Results: identification and routing, central search against ours [3:00]

**ON THE SLIDE:** a table of precision, recall and F1 for the central search and ours, over all events, local events and visitor events; four cards (0.21 pts, 0.00 pts, 1.1 pts, 9 vs 2); a "how to read" box.

| Cohort | Events | Precision (C / O) | Recall (C / O) | F1 (C / O) |
|---|---|---|---|---|
| All events | 10,000 | 0.983 / 0.984 | 0.952 / 0.950 | 0.967 / 0.967 |
| Local (registered here) | 8,000 | 0.990 / 0.992 | 0.959 / 0.959 | 0.975 / 0.976 |
| Visitor (registered elsewhere) | 2,000 | 0.953 / 0.950 | 0.924 / 0.913 | 0.938 / 0.931 |

(C = central search with no cache, O = ours, the full decentralised system.)

**SAY**
> This is the main comparison, averaged over 50 random populations of 10,000 events each. The baseline is the central search; ours is the full decentralised system.
>
> Over **all events**, ours identifies **95.01%** and the central search **95.23%**. That is a gap of **0.21 percentage points**, and the central search is ahead in **all 50 populations**. We do not claim to beat it; we claim to lose very little.
>
> The gap is small partly because **80% of the events are local, and both designs treat them identically**: a registered person is recognised by their own building's vote, with no lookup. Both identify exactly the same 7,675 of 8,000 local people. The only difference on local events is that the central search gives about **14 more wrong answers**, so its local precision is 0.990 against 0.992. After a local rejection, the person cannot be in another building, so every extra building searched is just another chance to accept a look-alike.
>
> The real cost is on **visitors**: recall is **0.913 against 0.924**, a gap of **1.1 points**. That is the price of routing a capture to a shortlist of five buildings instead of asking all nine.
>
> And the last card: for a visitor that has to be routed, the central search asks **all 9 other buildings; ours asks 2 on average**.

**DERIVATION: every value**

*Counts behind the table (50-seed means, central / ours):*

| | TP | FP | FN |
|---|---|---|---|
| All events | about 9,523 / 9,501 | about 165 / 155 | about 477 / 499 |
| Local | 7,675 / 7,675 | 74.1 / 59.6 | 325 / 325 |
| Visitor | about 1,848 / 1,826 | 91.0 / 95.8 | 152 / 174 |

- **Precision, recall, F1** are the one-vs-rest formulas of slide 18, computed inside each seed and averaged over the 50 seeds.
- **0.21 pts:** the mean over seeds of (central recall - our recall). Per-seed gap: range 0.09 to 0.43, standard deviation 0.08, 95% interval +/-0.02, central ahead in 50 of 50.
- **Why it is only 0.21:** all-events gap = 20% (visitors' share of events) x visitor gap + 80% x 0 (local) = 0.2 x 1.07 = **0.21**. The local events contribute nothing.
- **1.1 pts:** mean over seeds of (central visitor recall - ours) = 92.39% - 91.32% = 1.07. Per seed: range 0.45 to 2.15, central ahead in 50 of 50.
- **0.00 pts (local):** both get 7,675 / 8,000 = 95.94%, because the local vote (12 of 20 references within 80.43 degrees) decides these events **before any routing**.
- **"14 more wrong answers":** local FP 74.1 (central) minus 59.6 (ours) = 14.5.
- **9 vs 2:** mean buildings asked per **routed** visitor lookup. Central: 9.0 (all other buildings). Ours: 1.77, shown as 2 (the sequential policy stops at the first confirmation).
- *Not on the slide, but if asked:* buildings asked per **event** (over all 10,000): central **1.74**, ours **0.10**, a ratio of about **17 times** (this is the "17 times fewer buildings" on slides 23 and 24; per day 17,420 against 1,049). The "gallery touched" figure in the report is (1 + buildings asked) / 10 buildings: 27.4% central against 11.0% ours.

**CONFUSABLES (the most dangerous slide for mixing numbers up)**

| Number | What it is | Scope |
|---|---|---|
| **0.21** | Overall recall gap, central minus ours | all 10,000 events, no-cache central baseline |
| **1.1** (1.07) | Visitor recall gap | the 2,000 visitor events |
| **3.0** | Visitor recall gap when **ours has no cache** | slide 20, ours without the cache |
| **about 2.5** | Gap on **routed lookups only** | only the visitor sightings the cache did not answer, ours with the cache |
| **0.16 and 0.8** | The **old** gaps, when the baseline also had a cache | superseded; baseline changed |
| **2.55** | A gap from the **earlier routing paper**: routed visitors only, adaptive policy, 20 seeds, 92.95% against 95.50% | a different experiment; do not mix |
| **9 vs 2** | Buildings asked per **routed visitor lookup** | routed lookups |
| **1.74 vs 0.10** | Buildings asked per **event** | all events |
| **17,420 vs 1,049** | Buildings asked per **day** | the whole day (sum over events) |
| **27.4% vs 11.0%** | "Gallery touched", i.e. (1 + asked) / 10 | all events |

- **95.23 - 95.01 looks like 0.22, but the card says 0.21.** The percentages are rounded; 0.21 is the mean of the per-seed gaps (0.2148). Say "about 0.2 points".
- **Precision is slightly *higher* for ours overall (0.984 against 0.983) even though recall is lower.** Ours makes fewer wrong local identifications (59.6 against 74.1), and that outweighs its slightly more visitor errors (95.8 against 91.0).
- **Recall = identification accuracy** here. The central search has 0.952 overall recall in the table but is "95.23%" in the card; same number, different rounding. Likewise the visitor card says 92.4% and 91.3% (rounded from 92.39% and 91.32%) while the table says 0.924 and 0.913.
- **"All 450 other people" (footnote)** = the 9 other buildings x 50 people each: the people the central search compares a visitor against. Ours compares only with the people of the shortlisted buildings (about 1.8 buildings, so about 90 people).

**ATTACKS**

**"So your approach is worse. Why should anyone use it?"**
It is slightly less accurate and we say so. In return it keeps every building's face data in that building, shares only a compact Bloom filter, sends a capture to about two buildings instead of nine for a routed visitor (about 17 times fewer buildings asked per day), and has no single store to breach. Whether that trade is worth it is the reader's decision; we measured the price of it.

**"A 0.21-point gap on 10,000 events is about 21 events. That is noise."**
It is not noise: the gap is positive in all 50 populations, and the per-seed spread is small (standard deviation 0.08, 95% interval 0.02). It *is* small, and that is our finding. What the interval does **not** cover is variation in the people or the photos, because the 500 people are the same in every seed.

**"Your local result says the two designs are identical, so why does the central search have more false positives there?"**
Because after a local rejection a local person cannot be in another building, so any building that "confirms" them is a look-alike. The central search asks all nine buildings and takes the most votes, so it has more chances to accept one (about 14 more per run). Ours asks a shortlist of five in rank order and stops at the first confirmation, so it has fewer chances. Ours does not *find* more local people; it makes fewer mistakes on people nobody could find.

**"Is the central search ahead on visitors because the baseline is unfairly strong?"**
It uses the same local check, the same embeddings and the same vote rule. It differs only in that it looks the visitor up in all nine buildings instead of a shortlist of five. Most of its extra recall comes from the 9.4% of routed visitors whose home building is not in the shortlist: the central search can still find them, ours cannot (slide 20 shows the extra misses are mostly "not identified").

**"Why does the 'All events' row not simply average the other two?"**
It does: 80% x local + 20% x visitor. For recall: 0.8 x 0.959 + 0.2 x 0.924 = 0.952 (central) and 0.8 x 0.959 + 0.2 x 0.913 = 0.950 (ours).

**"Are these on the same events?"**
Yes. For each seed, both designs process the same 10,000 events, so the comparison is paired.

**"Is 'F1' meaningful in a one-vs-rest setting with 500 classes?"**
We compute TP, FP and FN over all identification decisions (each event has one true label and at most one predicted label), then precision, recall and F1 from those totals. It is a micro-style summary of decisions. It is not an average over 500 per-person F1 scores.

---

## Slide 20. Results: visitor lookup, central against ours, with and without the cache [3:00]

**ON THE SLIDE:** a three-column table (Centralised no cache; Decentralised no cache; Decentralised with cache), in **average events out of 2,000 visitor events per run**; a bar chart of visitor recall; a reading box.

| Visitor events (2,000 per run) | Centralised, no cache | Decentralised, no cache | Decentralised, cache |
|---|---|---|---|
| Answered by the cache: correct | 0 | 0 | 1,331 |
| Answered by the cache: wrong person | 0 | 0 | 14 |
| Routed lookups: correct | 1,848 | 1,787 | 495 |
| Routed lookups: wrong person | 27 | 38 | 17 |
| Routed lookups: not identified | 61 | 110 | 78 |
| Accepted as a local person (not routed) | 64 | 64 | 64 |
| Buildings asked per lookup | 9.00 | 1.48 | 1.77 |
| Buildings asked per day | 17,420 | 2,868 | 1,049 |
| Visitor precision | 0.953 | 0.946 | 0.950 |
| Visitor recall | 0.924 | 0.894 | 0.913 |
| Visitor F1 score | 0.938 | 0.919 | 0.931 |

**SAY**
> This slide takes the visitor events apart, because that is where the two designs differ. It is the same 2,000 visitor events per run for all three columns.
>
> Every visitor event ends up in one of three places. Either it is **answered by the visitor cache**: the building already confirmed this visitor earlier that day. Or it is a **routed lookup**: the building does not recognise the face and must look it up in other buildings. Or it is **wrongly accepted as a local person** by a look-alike, in which case it is never routed. About 64 events per run are in that last group in every column.
>
> The first six rows add up to 2,000 in every column.
>
> Look at the recall row. **Without a cache, ours loses 3.0 points** to the central search: 0.894 against 0.924. The central search can ask all nine buildings; ours asks a shortlist of five, and when the right building is not on it, the visitor is left unidentified. Look at the "not identified" row: 110 for ours, 61 for the central search. The wrong-person row is small for both. So **the cost of decentralising is mostly "not identified", not wrong people**.
>
> Then the cache. A repeat sighting the same day is answered **inside the building**, with no routing, so it cannot miss again. That raises our recall by **2.0 points** to 0.913, which leaves a gap of **1.1 points**. And it cuts the buildings asked per day from 2,868 to 1,049, against **17,420 for the central search**.
>
> One warning about reading the routed rows: with the cache on, the routed lookups are only the harder sightings the cache could not answer, so only compare those rows **within** a cache setting.

**DERIVATION: every row**

- **Answered by the cache:** visitor events answered from the day cache (no other building asked). Correct 1,331 and wrong 14 (1,345 in all, 67.2% of visitor events). Without a cache, 0 by construction.
- **Routed lookups (correct / wrong / not identified):** the visitor events the building did not recognise and the cache did not answer, split by outcome. They total 1,936 without a cache and 591 with it (1,848 + 27 + 61 = 1,936; 495 + 17 + 78 = 590).
- **Accepted as a local person:** visitor events a look-alike among the capturing building's own 50 people won with at least 12 of 20 votes. They are decided by the local vote before routing, so they are **the same in every column** (about 64) and cap the recall of all three.
- **The rows add up:** 0 + 0 + 1,848 + 27 + 61 + 64 = 2,000; 0 + 0 + 1,787 + 38 + 110 + 64 = 1,999; 1,331 + 14 + 495 + 17 + 78 + 64 = 1,999 (rounding).
- **Buildings asked per lookup:** mean over routed lookups of how many other buildings received the capture. Central 9.00; ours 1.48 (no cache) and 1.77 (cache).
- **Buildings asked per day:** the sum over the day. Check: 1,936 routed x 9 = 17,420 (central); 591 x 1.77 = 1,049 (ours with cache).
- **Recall** = correct / 2,000. Central: 1,848 / 2,000 = **0.924**. Ours without cache: 1,787 / 2,000 = **0.894** (0.8935). Ours with cache: (1,331 + 495) / 2,000 = 1,826 / 2,000 = **0.913**.
- **Precision** = correct / (correct + wrong), where "wrong" includes the cache's wrong answers, the routed wrong answers **and** the 64 look-alike false accepts. Central: wrong = 27 + 64 = 91; 1,848 / (1,848 + 91) = **0.953**. Ours without cache: 38 + 64 = 103; 1,787 / (1,787 + 103) = **0.946**. Ours with cache: 14 + 17 + 64 = 96 (95.8); 1,826 / (1,826 + 96) = **0.950**.
- **F1** = 2 x correct / (2 x correct + wrong + missed).
- **The three gaps, in events out of 2,000:** no cache: 1,848 - 1,787 = 61 events = **3.05 points**; the cache adds 1,826 - 1,787 = 39 events = **1.95 points**; remaining gap 1,848 - 1,826 = 22 events = **1.1 points**. (61 - 39 = 22.)
- **Why the numbers move together:** the savings is 1 - 1,049/2,868 = **63%** fewer buildings asked with the cache; central against ours with cache is 17,420 / 1,049 = **16.6 times**; per lookup 9.00 / 1.77 = **5.1 times**.

**CONFUSABLES (read this twice)**

- **"Routed correct is lower with the cache (495 of 590 = 83.9%) than without (1,787 of 1,936 = 92.3%). So the cache hurts?"** No. These are different sets of events. With the cache on, the 1,345 easy repeat sightings are taken away, and 99% of them are answered correctly (1,331 of 1,345). What is left to route is the harder first sightings and the ones the cache could not match. The cache turns about 1,345 sightings from roughly 92% correct into 99% correct, a net gain of about **39 correct events**, which is exactly why recall rises. **Compare routed rows only within the same cache setting.**
- **Visitor recall vs routed-lookup correct.** Same numerator, different denominator. Without the cache, ours has 1,787 correct: recall = 1,787 / **2,000** = 0.894; routed-correct = 1,787 / **1,936** = 92.3%. The routed figure is higher because it leaves out the 64 events that never reached routing.
- **"Wrong person" in the table vs "wrong" in precision.** The precision count adds the 64 look-alike false accepts and the cache's 14; the table lists them in separate rows.
- **Cache vs permanent pool.** All cache answers here are from the **day cache**, because the runs last one simulated day.
- **The 14 and the 11.** Slide 20 shows 14 wrong answers from the cache (an outcome bucket, 13.9 per run); the flag analysis behind slide 22 counts about 11 wrong identifications in the same "present visitor" mode (11.3 per run among about 1,336 identified events). Two scripts count slightly different sets; we did not reconcile the remaining 2 to 3 events per run. If asked, say "about 11 to 14 per day, only 4% flagged".

**ATTACKS**

**"Your baseline has no cache. A real central system would cache, so you have handicapped it."**
We tried both. With a central search that also has the cache: all-events accuracy 95.17%, visitor recall 0.921 and 5,162 buildings asked per day. That makes our gap 0.16 points overall and 0.8 on visitors, and the buildings-asked ratio about 5 times instead of 17. We chose the no-cache baseline for the slide so the cache, a decentralised feature, is not credited to the baseline. The accuracy gap moves by 0.05 points either way. We show ours with and without its cache so you can see both effects.

**"So the 17x is only against a naive baseline."**
It is against the central search that asks every other building for every visitor, which is what a distributed system without routing would do. A truly pooled database would contact no other building at all, but would hold everyone's face data in one place. The 17x measures how much less of the data a lookup has to consult; it does not claim to beat a pooled database on messages.

**"Your decentralised recall is worse in every column. What is the point?"**
In the no-cache row, yes: 3.0 points. The cache recovers two of them. We present the cost honestly: the gap is 1.1 points with the cache, 3.0 without. The benefit is in the buildings-asked rows and in not pooling face data.

**"The cache gives you recall by reusing identities. What if the first identification was wrong?"**
Then the wrong identity is reused for the rest of the day: about 14 of the 1,345 cache answers are wrong, and only 4.3% of those carry a warning flag. That is our main weakness (slide 23). The net effect of the cache is still positive: about 39 more correct events per day and slightly fewer wrong identifications overall (about 96 against 103 per run, counting the look-alike false accepts), but the 14 it does get wrong are reused all day and are almost never flagged.

**"Why does 'not identified' dominate? Isn't that just threshold choice?"**
It is mostly the 9.4% of routed visitors whose home building is not in the top five, which no threshold of the vote can fix. A longer shortlist would raise routing recall but ask more buildings.

**"64 events are accepted as a local person in every column. Isn't that a bug in the comparison?"**
No. It is the **local check**, which both designs share. A look-alike among the capturing building's own 50 people passes the vote, so the event is never routed. It is identical in all three columns because the cache and the routing do not touch it. It caps visitor recall at about 96.8% for everyone.

---

## Slide 21. Results: security and role-based access control [1:30]

**ON THE SLIDE:** level chips; the persona-replay table (16 requests per role); the test table (574); attacks that are refused; limits.

**SAY**
> We verified access control and security two ways. First, a **replay of 64 requests against the running system**: 16 for each of four roles. The Dean got 11 answers and 5 refusals, a Teacher 5 and 11, a Student 1 and 15, and a person from another building 4 and 12. Every refusal carries a rule code and every grant a disclosure level, and each decision is compared with the policy.
>
> Second, **574 automated tests**: 573 pass and one is skipped because it needs an optional building. They include attacks: a tampered header field, a replayed message, a wrong sender or recipient, a revoked certificate, brute-force login, a token used as another person, and a cache shared between roles. A failed step returns nothing.
>
> And the honest limit: the network and the certificate authority are **simulated**, and there is no real TLS or penetration test. This is a **tested design, not a hardened deployment**.

**DERIVATION**
- **64 = 4 roles x 16 requests.** Each role makes 12 person queries (3 queries: Q3, Q6, TRACK, against 4 targets: a rostered student, another student, the Dean, a person of another building) plus 4 building and zone queries (Q1 and Q10, each for its own building and for another building).
- **21 answered, 43 refused** are the totals of the table. "Levels granted" counts the disclosure level of each answered request, for example the Dean's L3 x10 and L2 x1.
- **Test counts by area:** access control 112, sealed transfers 61, API 98, queries and explanations 181, pipeline, population, events and evaluation 98, standalone, centralised and state library 19, shared utilities 5. Sum 574.
- The replay is run with `python tools/rbac_matrix.py --seed 42`.

**CONFUSABLES**
- **"574 tests pass" is wrong; it is 573 passed, 1 skipped.** The skipped one is a dashboard check that needs the optional masked building.
- **The replay checks the policy, not the policy's wisdom.** It proves the system does what the policy says.
- **"Outsider"** is a registered person of another building.

**ATTACKS**

**"You wrote the tests and the policy. How is that verification?"**
It verifies that the implementation matches the specification, and that attacks written by us fail. It is not an independent audit and not a penetration test, and the slide says so.

**"64 requests is a tiny sample."**
It is a complete grid, not a sample: 4 roles against each query type and target type, 16 combinations per role. Larger grids would repeat the same rules.

**"Which attacks did you actually test?"**
Tampering with every header field of a sealed message, replay, wrong sender or recipient, a revoked certificate, brute-force login with lockout (5 failures in 60 seconds), a token used as another person, and a cache shared across roles. We did not test denial of service, side channels or a compromised node.

**"No TLS? Then it is not secure."**
Correct, the API has no TLS unless a reverse proxy adds it, stored embeddings are not encrypted, and secrets and lockout counters are per process. We state this as a limitation. What we secured is the **application-level** exchange between buildings.

---

## Slide 22. Results: explainability [2:00]

**ON THE SLIDE:** the flags table (wrong ones flagged and correct ones flagged), two cards (79% / 39%; +13%), two screenshots of the same query from a Dean and a Student, and "Faithful by test".

| Identified | Wrong ones flagged | Correct ones flagged |
|---|---|---|
| All identifications | 73.9% | 2.3% |
| In the person's own building | 74.2% | 0.9% |
| Visitors found by routing | 85.3% | 30.0% |
| Visitors from the visitor database | 4.3% | 0.0% |

**SAY**
> Do the explanations help, and what do they cost? A flag is raised from facts **saved at the moment of identification**, such as a low vote count or a close call between two buildings. It never uses the true identity.
>
> Overall, **73.9% of wrong identifications carry a flag, and only 2.3% of correct ones do.** That is the useful direction: more among the wrong, fewer among the correct. The weak spot is the **visitor database**: only **4.3%** of its wrong identifications are flagged, because a cached visitor leaves almost no trail for a flag to use. Routed visitors are flagged reliably but noisily: 30% of their correct identifications carry a flag.
>
> For **query answers**, **79% of the wrong ones are flagged, but only 39% of the flagged answers are wrong.** So a flag means **look again**, not this is wrong.
>
> Building the explanation makes a query about **13% slower**, 5.9 to 6.6 milliseconds, and the response grows from 16 to 119 kilobytes. That cost was timed on five seeds on one machine. The two screenshots are the same query from a Dean, who gets the answer with its rule, evidence and flags, and from a Student, who is refused with a rule code and gets no explanation. And the explanations are **faithful by test**: 40 cases check that each piece of evidence is a real event and that redoing the answer from it gives the same answer.

**DERIVATION**
- **"Wrong ones flagged"** = (wrong identifications that carry at least one of the four identification flags) / (wrong identifications). This is the flag's **recall** on errors; higher is better.
- **"Correct ones flagged"** = (correct identifications with a flag) / (correct identifications). This is the **false-alarm rate**; lower is better.
- Computed per seed, averaged over 50 seeds. An "identification" is an event the system named a person for; abstentions are not included.
- **79% / 39% (answers):** about **6,900 answers per run** (where a person was at a time, how many zones they visited, full track). An answer is *wrong* if it differs from the same query run on the identified events **with their true occupants**. 79% = wrong answers that carry a flag; 39% = flagged answers that are wrong. 21% of all answers carry a flag.
- **+13%:** time per query, summed over the 14 query types, **5.9 ms without the explanation to 6.6 ms with it** (12.8%, per-seed range 12.1% to 13.4%), measured on seeds 0 to 4, one at a time, on one machine, so **no network delay**. Response size 16 KB to 119 KB, about 7 times larger, because the evidence is listed.
- **Strongest flag:** 714 of the 730 identifications that carry "winner not nearest", over the 50 seeds, were wrong.
- **Faithfulness:** 40 test cases (a yes, a no and an empty answer for each query type) check that each piece of evidence is one of the system's own events and that recomputing the answer from the evidence gives the same answer. Other tests check that an explanation never shows more than its role may see and is never shared between roles.

**CONFUSABLES**

- **73.9% vs 34%.** 73.9% of **wrong** identifications are flagged. But among **flagged** identifications, about **34% are wrong** (against about 0.4% of unflagged ones). Same data, opposite question. Do not swap them. The same pair for answers is 79% and 39%.
- **A flag vs a wrong answer.** A flag means weaker evidence, not an error.
- **Confidence vs flag.** The "confidence 0.970" in the screenshot is the identity probability of formula 5, not a measure that the answer is right.
- **"Mean of 50 seeds" vs "timed on 5 seeds":** the flags are over 50 seeds; the **timing** is over 5 (seeds 0 to 4). The slide says so.
- **The screenshot is from an earlier run.** Do not claim that its answer is wrong or right unless you check it.

**ATTACKS**

**"Your flags miss visitor-cache errors, exactly where it matters (4.3%)."**
Yes, and we say so on slide 23. A cached visitor is matched against the 20 stored references and leaves no routing trail, no Bloom margin and no rank, so the flags have nothing to look at. It is a known weakness and the first thing we would fix.

**"Only 39% of flagged answers are wrong. Isn't that useless?"**
The flags cover 79% of the wrong answers while marking only 21% of all answers. From those two numbers, a flagged answer is roughly **14 times more likely to be wrong** than an unflagged one (79/21 against 21/79). For identifications it is the same picture: about 34% of flagged identifications are wrong, against about 0.4% of unflagged ones. So a flag is a strong signal to look again, not a verdict, and we are careful to say so. (The 14 times is derived from the rounded 79% and 21%, so say "roughly".)

**"30% of correct routed visitors are flagged. That is a lot of false alarms."**
Yes: routed visitors are inherently less certain, so "lower-ranked building verified" and "few votes" are common even when the answer is right. We report it rather than hide it.

**"Your explanations just restate the answer."**
They restate the **evidence** and the **rule** and they state what was withheld, redacted to the caller's level. The faithfulness tests exclude explanations that merely sound plausible.

**"It is not explainable AI. It does not explain the model."**
Correct. We explain the system's decision trail (rule, events, votes, flags), not why ArcFace scores two faces as similar. That is the part a panel auditing a surveillance system needs.

**"Is +13% real? On a laptop?"**
It is the extra time on a single machine, with no network. We do not claim it for a deployed system. It is a measure of the computation of building the explanation, not of end-to-end latency.

---

## Slide 23. Discussion and limitations [1:30]

**SAY**
> What do the results mean? Decentralised routing comes within about **0.2 points** of a central search, 95.0% against 95.2%, while asking **2 buildings instead of 9** for a routed visitor; per event that is 0.10 against 1.74 buildings. Most visitor events need **no routing at all**, because repeat sightings come from the day cache. Uncertainty flags mark about **three quarters of the wrong identifications**, and 2.3% of correct ones, for about 13% extra latency, though errors from the visitor database are almost never flagged. The Bloom filters show **3.5% false positives, not the configured 1%**, so privacy rests on the one-way encoding and exact verification. And asking all five shortlisted buildings, or only when the top scores are close, cuts wrong identifications of routed visitors by about 0.6 points, for more contacts.
>
> The limitations are honest ones. The **network, sensors and face detector are simulated**; the certificate authority is a stand-in. There is no real TLS, data on disk is unencrypted, and there has been no outside penetration test. **A wrong identity in the visitor cache persists for the day.** We tested 500 people in 10 buildings; larger scale is untested, and week and month simulations just replay one recognised day. Masked-face recognition is not implemented.

**DERIVATION: the Bloom filter's 3.5%**
- The filter is sized for a 1% false-positive rate **if its items behave like independent random keys**. They do not: LSH codes of different people collide.
- Measured by testing the enrolled items of the nine other buildings against each building's filter: **3.56%** over 10 buildings and 50 seeds.
- Two sources: the Bloom array alone, on random integers never inserted, reports **0.95%**, so the array is fine. The excess comes from the codes: **2.64%** of other buildings' items already equal an enrolled item in the same slot, close to the **2.44%** expected if the codes of 50 people were spread evenly over 2,048 values. They combine to about 3.5%.
- It is a **routing hint**, not a correctness break, because every candidate is verified exactly by the vote.

**CONFUSABLES**
- **1% vs 3.5%:** configured sizing target vs effective measured rate.
- **"0.6 points" for the policies** is on **routed lookups** (2.95% against 2.29% and 2.36% wrong), not overall (0.05 points overall).
- **"17 times fewer buildings"** (per event or per day, against the no-cache central search) vs **"2 instead of 9"** (per routed lookup).

**ATTACKS**

**"What is the biggest weakness of your system?"** (Be ready; say it first.)
The visitor cache. A wrong first identification is reused for the day and is almost never flagged. Right behind it, the whole evaluation is simulated: simulated cameras, replayed photos, no network. Then the closed-set assumption: every visitor is registered somewhere.

**"Privacy 'rests on exact verification'. So there is no privacy guarantee."**
Correct. We claim none. The filter is one-way and holds hashed codes, not ids or embeddings, but membership can be tested. We say the privacy argument must not rely on a small false-positive rate.

**"Why did you not fix the 1% versus 3.5%?"**
Making the filter bigger or K larger lowers the effective rate (an earlier one-seed sweep showed 1.6% at K = 13) but needs more slots and a bigger filter; and since the filter is only a routing hint, the 3.5% does not hurt accuracy. We report the finding rather than tuning it away.

**"What does 'larger scale is untested' mean for your claim?"**
That the 17-times figure and the accuracy gap are for 10 buildings of 50. With more buildings the central search would ask more, so the ratio would probably grow, but routing recall in the top five could fall. We did not measure it.

---

## Slide 24. Conclusion [1:00]

**SAY**
> We built and evaluated an end-to-end system that identifies and tracks occupants across buildings **without pooling face data**, answers queries **by role**, and **explains every answer**. It matches a central search to within about **0.2 points**, while asking about **17 times fewer buildings**. The table gives each objective, when it was planned, its status and the evidence. All objectives are achieved, and the security objective carries an asterisk because the network is simulated.

**NOTE (check before the review):** the conclusion table in the final deck lists **objectives 1 to 5 only; objective 6, "Evaluation", is missing** (it was in the earlier deck v7 and is gone from the final deck). Slide 5 lists six objectives and the notes say "all six". Either restore the row ("6 Evaluation, Final review, Achieved, Event TP/FP/FN/TN, 50 seeds, central baseline") or be ready to say "objective 6 is the evaluation itself, which is slides 18 to 22".

**ATTACKS**

**"You said 'achieved'. Achieved by whose standard?"**
Each objective was set with a method of evaluation (report, objectives table). For example objective 4 was "design the mechanism and measure its cost against a central search": designed, measured, 0.21 points for about 17 times fewer buildings. Objective 5 is "achieved" with the simulated-network caveat.

**"The conclusion says 'matches a central search'. It does not match it; it is lower."**
"Matches to within 0.2 points" is the claim. It is lower by 0.21 points overall and 1.1 on visitors. We never claim equality.

**"What would you do differently?"**
Evaluate on real video and a real network; fix the visitor-cache blind spot in the flags; run longer than a day so the permanent pool is exercised; add unknown-visitor handling; and get an independent security review.

---

## Slide 25. Future work [1:00]

**SAY**
> Three directions. **First, masked-face recognition.** The system assumes unmasked faces. It needs a face model trained with masks, a real mask detector, a few real masked photos per person, and tracking context such as who is already inside. **Second, unknown-visitor re-identification.** Today a visitor must be registered in some building, and a face nobody knows is left unidentified. We would keep one-way codes of unknown faces under a temporary id and link their sightings; that needs retention and privacy rules and a check that two strangers are not merged. **Third, other kinds of sensors.** The pipeline needs only an identity estimate with a zone and a time, so badge readers, RFID or beacons can feed it instead of, or together with, cameras. Each new sensor needs its own error measurement, and the access rules and explanations must be checked for it.

**ATTACKS**

**"You had a masked building earlier. Why was it removed? Isn't that cherry-picking?"**
We experimented with an extra building of masked people. Recall there was weak, about 59% in that earlier experiment. Synthetic masked templates, upper-face matching and relaxed thresholds did not fix it, because the limit is the face model, which was trained on unmasked faces. So the masked building is switched **off** in the reported runs and masked recognition is stated as a limitation and as future work. It is not in any result we report. (This is from our project notes; say "an earlier experiment".)

**"Your whole evaluation is closed-set: every visitor is registered somewhere. Real visitors are strangers."**
Correct, and it is the biggest scope limit. A face registered in no building is left unidentified, and we did not evaluate open-set behaviour, so accuracy on strangers is unknown. That is why unknown-visitor re-identification is future work. Our numbers describe the closed-set case.

**"Why would other sensors work? Your access rules and explanations assume face evidence."**
The pipeline needs an identity estimate, a zone and a time. The explanation flags (votes, routing rank) are specific to the face path, so they would need redefining for a badge. That is exactly why we say each sensor needs its own error measurement and re-checking of the rules and explanations. We did not do it.

---

## Slide 26. Team contribution [0:45]

**SAY**
> Ajith built the dashboard and its wiring. Arjun built the query engine, the access control and secure-transfer layer, the server, monitoring and the mobility simulation. Aditya built event recognition and retrieval, the centralised baseline, and the explanation model with redaction and faithfulness tests. I built the LSH and Bloom routing, the pipeline, the visitor database, the seeded populations, ground truth and metrics, role-based access and the integration. The contributions follow the repository history and hand-overs.

**ATTACKS**

**"Who wrote the part I am asking about?"** Each of you must be able to explain your **own** part in detail and every other part at the level of slides 9 to 11. Know the one-line answer for each teammate's part. If the question is about someone else's part and they are in the room, hand it over: "Arjun built that, I will let him answer."

**"Is it a team project or one person's?"**
Four people, each owning a layer, integrated into one pipeline. The repository history and hand-overs record who did what.

---

## Slide 27. Appendix A: code and demo; Slide 28. Appendix B: dashboard demo [3:00 with the live demo]

**SAY**
> Everything is in the repository, on branch `test_7`; the QR code opens it. One command runs the 50-seed experiment, another starts the demo server, and the tests are run with `python manage.py test`: 573 pass and one is skipped. I will now show two things: the 64-request replay in a terminal, and the dashboard.

Use the demo script in Part F5. Slide 28 lists every dashboard page, so anything you open in the demo is described in the report.

**ATTACKS**

**"If I clone the repo and run your commands, do I get your numbers?"**
`python manage.py run --seeds 0-49` regenerates the decentralised results per seed. The central-baseline and cache-off numbers use three switches in `pipeline/route_and_recognize.py` (`DSTS_SHORTLIST_K=9`, `DSTS_VERIFY_POLICY=verify_all`, `DSTS_VISITOR_DB=off`) and helper scripts under `final_review/latex/versions/`. **Check before the review that these are committed and that the scripts run from a fresh clone** (see Part F6). Do not promise one-command reproduction of the baseline unless you have tested it.

**"Demo mode lets anyone be anyone."**
Yes, by design: a demo mode signs in as a chosen role so the access model can be shown. Normal mode uses per-person credentials, one-hour tokens and lockout.

---

## Slide 29. Project timeline [0:20]

**SAY**
> The core pipeline and routing were done by the mid review. Role-based access, security and the baseline comparison followed in September and October. The explanations, the visitor database and the ground truth were finished on 25 October, and the comparison was re-run on 10 buildings in November. All components are implemented.

**ATTACKS:** "Why were results re-run in November?" Because we changed the evaluation to 10 buildings and rebuilt the central baseline so it is a fair comparison (same local check, no cache). The earlier numbers used an 11-building setup with a masked building and an unfair baseline, and they are superseded.

---

## Slide 30. References [0:00]

Do not read it. If asked about a reference, the ones you must know: **[6] Mohan and Menon 2020 (DSTS, the base paper)**, [8] Bloom 1970, [9] Charikar 2002 (random-hyperplane hashing preserves angular similarity: the property behind our codes), [10] Lv et al. 2007 (multi-probe LSH), [11] ArcFace (Deng et al. 2019).

---

# Part F. The question bank, the cheat sheet, the demo, the open items

## F1. The ten cruelest questions, with the answers that hold

1. **"You did not beat the central search. So what did you achieve?"**
We measured what a decentralised design costs: 0.21 points overall, 1.1 on visitors, for about 17 times fewer buildings asked and no pooled face data. Whether that trade is worth it is the institution's choice; we made it measurable.

2. **"Your baseline is a strawman."**
It is the same pipeline, same embeddings, same local check and same vote rule; only the visitor lookup differs. With the cache added to the baseline the accuracy gap moves by 0.05 points and the buildings-asked ratio goes from 17 times to about 5. We show ours with and without its cache.

3. **"Face data does leave the building."**
In two controlled cases: the capture goes, sealed, to about two shortlisted buildings, and a confirmed visitor's 20 references are copied for one day. What never happens is pooling in a central store. Our claim is "no pooling".

4. **"Your Bloom filter is not 1%, it is 3.5%. So it is broken."**
It is a routing hint. Every candidate is verified by the exact vote. The 3.5% comes from LSH collisions between different people, not from the Bloom array (0.95%), and we report it instead of hiding it. We claim no formal privacy guarantee.

5. **"The cache reuses wrong identities all day, and your flags miss them."**
Yes. About 14 wrong a day, 4.3% flagged. It is our largest weakness, on slide 23. The cache still gives 39 more correct events per run and fewer wrong ones overall.

6. **"Everything is simulated."**
Yes: cameras, movement, network, certificate authority, and replayed photos. Our claims are about the design and its relative accuracy against a central search, not a hardened deployment.

7. **"Your 50 populations are the same 500 people. It is not 50 independent samples."**
Correct. They are 50 random assignments of the same 500 people to buildings, each with a different event day. The interval covers assignment variability, not variability from the face model or from new people.

8. **"You wrote the policy and the tests, so 64 passes mean nothing."**
They show the implementation matches the specification, and that our attacks fail. They are not an independent audit, and we say so.

9. **"How did you choose 80.43 degrees and 12 votes?"**
See F6, item 1. Do not improvise: know the real answer.

10. **"Why a camera?"**
The base paper we extend is a camera framework, a camera is unobtrusive, and it gives identity and location together. We did not run any other sensor, and the pipeline accepts any sensor that gives an identity estimate, a zone and a time.

## F2. More questions by theme (short answers)

### Motivation and scope
- **"Why decentralise at all?"** Face data cannot be revoked; a central store is one breach and one point of failure. We measured the accuracy price.
- **"Is this novel?"** The pieces are known (Bloom, LSH, RBAC, sealed channels). The contribution is their combination for cross-building tracking, the measured cost against a central search, and the finding that the Bloom false-positive rate is 3.5%.
- **"Why not federated learning?"** We train nothing. The problem is identity lookup across buildings, not model training.
- **"Why not homomorphic encryption or secure multi-party computation?"** They would give stronger privacy at much higher cost. We did not evaluate them; they are possible future work. (Argument, not an experiment.)

### Method
- **"What if two buildings both confirm?"** In the sequential policy the first confirmation wins; in verify-all and adaptive the most votes wins. A lower-ranked confirming building raises the `verified_by_lower_ranked_building` flag.
- **"How is a local person distinguished from a visitor?"** By the local vote: at least 12 of 20 of the building's own people's references within 80.43 degrees. If it passes, the person is treated as local. A look-alike can pass it wrongly (about 64 visitor events per run).
- **"What does the Bloom filter contain?"** 6,100 hashed items per building (50 people x 122 slots), in a 58,469-bit array, 7.3 KB. No ids, no embeddings.
- **"Can the filter be reversed to get a face?"** Not directly: it holds hashed codes, and a codes-to-face reconstruction would be an attack we did not evaluate. We claim no formal guarantee.
- **"Why is a visitor's reference copied into the visited building?"** So repeat sightings the same day need no routing. It is zeroed at the day rollover.

### Evaluation
- **"What is the unit of evaluation?"** The event: one camera sighting from one test photo. 10,000 per run.
- **"Is accuracy computed per person?"** No; it is a pooled count of decisions (TP, FP, FN) over events, per cohort.
- **"Why is 'All events' precision higher for you than for central?"** Fewer wrong local identifications (59.6 against 74.1), which outweighs slightly more visitor errors.
- **"Could the test photos have leaked into enrolment?"** No: a person's 20 references and 20 test photos are disjoint, the mean face and Bloom filters use references only, and ground truth is read only by the evaluation scripts.
- **"Did you tune anything on the test photos?"** Our protocol says no, and the thresholds are the same constants in every building and seed. See F6, item 1 for the one parameter whose origin you must confirm.
- **"Is the gap statistically significant?"** It is positive in all 50 paired seeds, with a small spread (standard deviation 0.08, interval 0.02 on the mean). We did not run a formal test, and the seeds share the same 500 people.
- **"Where is the latency comparison?"** Not measured. The network is simulated in one process. We measure buildings contacted, and the extra time to build an explanation on one machine.

### Privacy and security
- **"What if an attacker owns a building?"** Not evaluated. A compromised node could receive captures sent to it and read its own filter. Our threat model is outsiders, tampering, replay and impersonation, not a malicious building.
- **"Can a Student infer a classmate's location from counts?"** Student zone queries are refused outright; building counts (L1) are aggregates over everyone in the building, with no names. We did not analyse inference from many aggregate queries.
- **"What about the audit log?"** Every decision is logged with layer, code, principal and target. A Dean sees all decisions, other roles only their own.
- **"Is the dashboard secure?"** Demo mode lets any viewer act as any role by design. Real sign-in uses per-person credentials, tokens that expire after an hour, and lockout. No TLS unless a reverse proxy adds it.

### Explanations
- **"Who is the explanation for?"** An auditor or user who must see why the system answered, from which events, and how weak they were.
- **"Does the explanation leak?"** It is redacted to the same level as the answer, so a Student refused an answer gets no explanation content, and tests check no leakage across roles.

### Practical
- **"How long does a run take?"** About 8 minutes per seed on a laptop CPU for 10,000 events; 50 seeds take hours.
- **"What does the dashboard do when no one is signed in?"** Sign-in is required; the Overview shows only what the role may see.

## F3. Confusing numbers: the cheat sheet

| Number | What it is | Where |
|---|---|---|
| 95.01% / 95.23% | all-event accuracy (recall), ours / central (no cache) | slide 19 |
| **0.21** | gap, central minus ours, all events; 50/50 seeds, range 0.09 to 0.43, SD 0.08, interval +/-0.02 | slide 19 |
| 7,675 | local events both designs identify, out of 8,000 (95.94%) | slide 19 |
| 74.1 vs 59.6 | local false positives, central / ours (about 14 more) | slide 19 |
| **1.07** (1.1) | visitor recall gap, 92.39% against 91.32%; per seed 0.45 to 2.15 | slide 19 |
| 9 vs 2 (1.77) | buildings asked per routed visitor lookup | slide 19, 20 |
| 1.74 vs 0.10 | buildings asked per event; ratio 16.6, i.e. about 17 times | slides 23, 24 |
| 17,420 / 2,868 / 1,049 | buildings asked per day: central / ours no cache / ours cache | slide 20 |
| 0.924 / 0.894 / 0.913 | visitor recall: central / ours no cache / ours cache | slide 20 |
| 3.0 / 2.0 / 1.1 | gap without cache / what the cache wins back / remaining gap (points) | slide 20 |
| 1,331 + 14 | cache answers: correct + wrong (1,345, 67.2% of visitor events) | slide 20 |
| 64 | visitor events accepted as local by a look-alike (the same in every column); caps recall at 96.8% | slide 20 |
| 63% | fewer buildings asked per day with the cache: 1 - 1,049/2,868 | slide 20 |
| 90.6% | routing recall: home building in the top 5 | slide 10, report |
| 11.0% / 27.4% | gallery touched: ours / central = (1 + asked) / 10 | report |
| 0.16 / 0.8 | **old** gaps (baseline had a cache); superseded | do not use |
| 95.17%, 0.921, 5,162 | central **with** cache: accuracy, visitor recall, buildings asked per day | backup only |
| 73.9% / 2.3% | wrong / correct identifications flagged | slide 22 |
| 34% / 0.4% | flagged / unflagged identifications that are wrong | report |
| 79% / 39% / 21% | wrong answers flagged / flagged answers wrong / answers flagged | slide 22 |
| 4.3% | wrong visitor-cache identifications flagged; about 11 to 14 per run | slides 20, 22 |
| +13%; 5.9 to 6.6 ms; 16 to 119 KB | explanation overhead, **5 seeds**, one machine | slide 22 |
| 3.56% (about 3.5%) vs 1% | effective vs configured Bloom false-positive rate; 0.95% from the array, 2.64% from code collisions | slide 23 |
| 83.89 / 84.49 / 84.42% | routed lookups correct: sequential / adaptive / verify all; contacts 2 / 3 / 5 | slide 16, report |
| 21 / 43 of 64 | answered / refused in the persona replay | slide 21 |
| 573 + 1 = 574 | tests passed + skipped = total | slide 21 |
| 80.43 degrees, 12 of 20 | vote parameters | slide 17 |
| K = 11, L = 122, 3 probes, shortlist 5 | LSH and routing parameters | slide 17 |
| 58,469 bits, 7 hashes, 7.3 KB | Bloom filter size per building | report |
| 500 people, 10 buildings, 10,000 events (8,000 + 2,000), 50 seeds | experiment size | slide 18 |

## F4. Words to use and words to avoid

| Use | Avoid |
|---|---|
| "keeps most of the accuracy" | "as good as" or "better than" a central search |
| "without pooling face data" | "face data never leaves a building" |
| "a tested design on a simulated network" | "secure", "hardened", "production-ready" |
| "50 random assignments of the same 500 people" | "50 independent populations of people" |
| "a routing hint" (Bloom filter) | "the Bloom filter identifies" |
| "a flag means look again" | "the flags catch errors" |
| "we did not measure that" | guessing a number |
| "routed lookups" with its scope | a gap without its scope |
| "the closed-set case" | "works for any visitor" |

## F5. The live demo (about 3 minutes), with a fallback

**Before the review:** run both steps once, on the machine you will present from.

**Step 1. The 64-request replay (terminal, 40 seconds).** From the repository root:

```bash
python tools/rbac_matrix.py --seed 42
```

It prints the personas (building 5: Dean 0002253, Teacher 0000582, Student 0000205, outsider 0000099), then one row per request (caller, query, target, answered, rule, level), and ends with `64 requests`. Say: "Each role makes 16 requests; this is the same call the web page makes, with the same sign-in and policy checks." Point out one Dean row answered at L3, one Student row refused with a rule code. The totals are 21 answered and 43 refused (Dean 11, Teacher 5, Student 1, outsider 4).

**Step 2. The dashboard (about 2 minutes).**

```powershell
$env:DSTS_DEMO_MODE = "1"; python manage.py serve --seed 42
```

then open `127.0.0.1:8000`.
1. Sign in as the **Dean** of building 5 (0002253). Open **Research queries**, pick **Q6** (where was O at time t), enter the rostered student `0000103` and a time such as `12:00`, run it. Show the **answer** and the **"Why this answer" card**: rule, evidence, flags, what was withheld.
2. Switch the sign-in to the **Student** `0000205` and run the **same query**. It is **refused with a rule code**, and the explanation is withheld.
3. As the same Student, ask Q6 about their **assigned teacher** `0000607`: it is answered at **L2, present only**. Say: "A student may see the position now, never the history."
4. If the live feed or tracks are empty, start the simulation from **Overview** (only a Dean can).

These persona ids are for seed 42 on this machine (checked with the replay). If you use another seed, the ids change; read them from the replay's first line.

**Fallback if the demo fails:** do not debug live. Say "Let me show the same thing from the slides" and use the Dean and Student screenshots on slide 22 and the dashboard pages on slides 27 and 28.

## F6. Open items to resolve BEFORE the review (found while preparing this script)

Each of these is a place where a hostile panel could catch you. They are listed in order of risk.

1. **The origin of 80.43 degrees and 12 votes.** The code does not record how they were chosen. Find out (it is a fixed constant in every building's `config.json`), and prepare an honest answer. If it was set on the reference photos or inherited from earlier work, say that. If it was set while looking at accuracy on these test events, say so plainly and add that it is the same in every seed. A reviewer will follow with "so you tuned on test data", and the only safe answer is the true one.
2. **The conclusion table (slide 24) has no row for objective 6, "Evaluation".** It was in the earlier deck and is missing from the final one, while slide 5 lists six objectives and the notes say "all six". Restore the row, or be ready to say it is covered by slides 18 to 22.
3. **Speaker notes of slide 12 are stale.** They still say L1 is "defined but no rule grants today". L1 is granted for building and zone aggregates (the slide table says so). Slide 27's note says the tests "pass in full"; it should say 573 pass and 1 is skipped.
4. **Slide 11 says "formula 5".** Formulas 1 to 4 are no longer on a slide. Change it to "the formula below", or be ready to point to slide 10's maths table in this script.
5. **"Independent random populations" on slides 17 and 18.** The people are the same 500 in every seed. Say "random assignments of the same 500 people", and expect the question.
6. **Reproducibility of the central baseline.** `manage.py run` produces the decentralised numbers. The central-baseline and cache-off numbers need `DSTS_SHORTLIST_K=9`, `DSTS_VERIFY_POLICY=verify_all` and `DSTS_VISITOR_DB=off` and the helper scripts in `final_review/latex/versions/`, which currently have **hard-coded paths into a temporary folder** and are **uncommitted**. The repository also has uncommitted changes (the cache switch in `pipeline/route_and_recognize.py`, the test fix, `config/experiment.yaml`). Commit what you want the panel to see, and either make the scripts path-independent or do not promise one-command reproduction of the baseline.
7. **Which paper Q1 to Q13 come from.** Ask Arjun. The dashboard calls them "research-paper query templates".
8. **Re-read the base paper** (Mohan and Menon 2020) once, especially its evaluation and what it says about visitors and access. This script only claims what our slides and report claim about it.
9. **The two numbers for visitor-cache errors** (about 14 on slide 20, about 11 behind slide 22). They come from two scripts that count slightly different sets; say "about 11 to 14 per run, 4% flagged".
10. **The slide 22 screenshots come from an earlier run.** Do not state whether that answer is right or wrong.
11. **Slide 14's full 14-query table was produced by a script that is not in the repository.** `tools/rbac_matrix.py` covers Q1, Q3, Q6, Q10 and TRACK only (the 64 requests). If a reviewer asks you to show Q2, Q4, Q5, Q7 to Q9, Q11, Q12 or Q13, say they follow the same rules (person queries follow one rule; zone queries are filtered per person), or add the wider script to `tools/` before the review.
12. **Report not compiled.** `Final_Report_v8.tex` has been checked for balanced environments and references but has not been compiled here. Compile it in Overleaf with the `figures` folder and look at every new table.

## F7. Closing words (after the conclusion slide)

> To summarise: starting from our guide's DSTS framework, we asked how far a multi-building camera-tracking system can go **without a central master list of faces**. The answer we measured is that each building can keep its own face data, publish only a compact summary, and still identify visitors almost as accurately as a central search: **0.21 points lower overall, 1.1 on visitors**, while asking about **17 times fewer buildings**. On top of that we control who may ask where a person is, and every answer explains itself. We have been open about the limits: it is a simulation, the Bloom filter's false-positive rate is higher than configured, and the visitor cache can repeat a wrong identity for a day. Thank you. We would be glad to take your questions.

## F8. How to handle the room

- **Pause before answering.** Restate the question in one line. It buys time and shows you understood.
- **If two people ask at once,** answer the first, then say "and on your point, ...".
- **If you realise you made a mistake,** correct it at once: "I misspoke; the correct figure is ..." Panels forgive corrections and punish defensiveness.
- **If the question is about a number,** say its scope first (all events, visitors, routed lookups), then the number.
- **If you do not know,** say "we did not measure that" or "I will check and tell you", and never invent one.
- **Hand over cleanly.** For another member's layer: "Arjun built that part; Arjun?"

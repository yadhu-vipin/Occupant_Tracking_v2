# building_11: masked identities from RMFRD

Built by `tools/build_masked_building.py` from the RMFRD real-world masked face dataset (Wuhan University, "Masked Face
Recognition Dataset and Application", Wang et al. 2020; `self-built-masked-face-recognition-dataset`): web-crawled photos
of 525 people with real masks and hundreds of unmasked photos each.

* 50 identities (the ones with the most real masked photos, each with >= 20 unmasked photos),
  ids 9000001 - 9000050. They have 10 - 52 real masked photos each.
* 2000 embeddings: 20 `reference` (unmasked, all distinct real photos) + 20 `test` (all masked) per person, 512-d,
  L2-normalised, from insightface `buffalo_l` (`det_10g` detector + landmark alignment, `w600k_r50` ArcFace, RGB,
  (x-127.5)/127.5).
* Probes: 800 of the 1000 masked probes are original masked photos; the rest are augmented copies of them
  (flip, rotation, scale, shift, brightness, blur, noise, JPEG). No synthetic masks. `source` column: real / real_augmented.
* Face detection found a face in 2132 photos; 41 fell back to a centre crop.

Limits: the median person has 16 real masked photos (15 people have the full 20), so the rest of their 20 probes are augmented copies; the photos are
celebrity-style web images. Treat building_11 as a test of masked recognition and routing, not as an accuracy estimate
for new people. RMFRD is released for research use; see its GitHub page for terms.

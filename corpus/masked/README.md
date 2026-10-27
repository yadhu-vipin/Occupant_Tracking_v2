# building_11: masked identities from MFR2

Built by `tools/build_masked_building.py` from the MFR2 masked-face recognition dataset (Anwar & Raychowdhury,
"Masked Face Recognition for Secure Authentication", MaskTheFace project): 53 identities, 269 aligned photos.

* 50 identities (the 50 with the most photos), ids 9000001 - 9000050.
* 2000 embeddings: 20 `reference` (unmasked) + 20 `test` (all masked) per person, 512-d, L2-normalised, from
  insightface `buffalo_l` / `w600k_r50.onnx` (112x112 RGB, (x-127.5)/127.5).
* Only 263 of the 2000 images are original MFR2 photos; the rest are augmented copies or synthetic masks
  drawn on unmasked photos (`source` column: real / augmented / real_augmented / synthetic; `mask_type` says which mask).

Limits: synthetic masks are a fixed-geometry overlay (no landmark detector); every person's 40 embeddings come from
at most 10 real photos. Treat building_11 as a test of masked recognition and routing, not as an accuracy estimate for
new people. MFR2 is for research use; see its page for terms.

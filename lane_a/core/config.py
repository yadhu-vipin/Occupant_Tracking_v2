"""Single source of truth for every path and tunable in the pipeline.

Nothing else in the codebase hard-codes a path or a magic number -- import it
from here. Constants that are *derived from the data* (the accept angle, k, L)
are marked below; the pipeline step that owns each one re-derives it and prints
the value to paste back here.
"""
from pathlib import Path

# --------------------------------------------------------------------------
# Filesystem layout (everything is relative to the repo root = parent of core/)
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent

CASIA_DIR   = ROOT / "casia_webface_sorted"      # raw source dataset (input to step 01)
SPLITS_DIR  = ROOT / "dataset_splits"            # step 01 output
EMB_DIR     = ROOT / "embeddings_arcface"        # step 02 output
ARTIFACTS   = ROOT / "artifacts"                 # step 03 output
RESULTS     = ROOT / "results"                   # steps 05 / 07 output

EMB_FILE       = EMB_DIR / "emb_arcface.npy"     # (N, 512) float32, one row per photo
EMB_META       = EMB_DIR / "meta.csv"            # row-aligned; occupant_id zero-padded
MEAN_FACE_FILE = ARTIFACTS / "mean_face.npy"     # (1, 512) population mean
META_FILE      = ARTIFACTS / "meta.csv"          # working copy; occupant_id as int (steps 04-07)

SUPERSET_SPLIT = SPLITS_DIR / "10_buildings"     # the 2- and 5-building configs are slices of this

# --------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------
SEED = 42          # drives the occupant shuffle, the per-occupant photo shuffle,
                   # the LSH hyperplanes, and the Bloom-filter hash salts.

# --------------------------------------------------------------------------
# Dataset design (step 01)
# --------------------------------------------------------------------------
OCCUPANTS_PER_BUILDING = 50
REFS_PER_OCCUPANT      = 20     # enrollment photos
TESTS_PER_OCCUPANT     = 20     # held-out "future sighting" photos
PHOTOS_PER_OCCUPANT    = REFS_PER_OCCUPANT + TESTS_PER_OCCUPANT

# Building counts to evaluate. Nested: the 2-building population is the first
# 100 of the shuffled pool, the 5-building the first 250, the 10-building all
# 500 -- so building_3 is the same 50 people in every config.
BUILDING_CONFIGS = (2, 5, 10)
N_BUILDINGS_SUPERSET = max(BUILDING_CONFIGS)

# --------------------------------------------------------------------------
# Face model (steps 01 and 02)
# --------------------------------------------------------------------------
ARCFACE_MODEL = "buffalo_l"    # InsightFace bundle: RetinaFace detector + ArcFace embedder
DET_SIZE = (192, 192)          # CASIA crops are ~250x250; the 640 default fails on them
EMBED_DIM = 512

# ONNX Runtime execution providers, in priority order. CPU is kept as the
# fallback, so this list is safe on a box with no GPU -- ORT just skips CUDA.
#
# GPU works via onnxruntime-gpu 1.22 + the nvidia-*-cu12 wheels, provided
# core.gpu.enable_cuda() runs before the first inference session (steps 01 and
# 02 both call it). Measured on an RTX 4050: 16 ms/photo vs 260 ms on CPU.
# See decisions.md "Step 02 / GPU".
ONNX_PROVIDERS = ["CUDAExecutionProvider", "CPUExecutionProvider"]

# --------------------------------------------------------------------------
# Random-hyperplane LSH   --   DERIVED by pipeline/06_derive_lsh_parameters.py
# --------------------------------------------------------------------------
K = 11     # bits per code
L = 122    # codes per face
LSH_TARGET_TPR = 0.90   # same-person recall target that step 06 solves L for
# Step 06 (per-pair theory) picks k=10 L=88; step 07 -- where each building's
# filter pools ~50 occupants -- measured k=11 L=122 as +1.0pp correct / -0.65pp
# routing failure at 10 buildings, so k=11 is adopted. See decisions.md "Step 07".

# --------------------------------------------------------------------------
# Per-building Bloom filter (step 07)
# --------------------------------------------------------------------------
TARGET_FPR = 0.01   # auto-sizes the bit array and hash count

# --------------------------------------------------------------------------
# Routing (step 07)
# --------------------------------------------------------------------------
SHORTLIST_K = 5     # top-scoring buildings kept for verification

# --------------------------------------------------------------------------
# Verification   --   ACCEPT_ANGLE_DEG is DERIVED by pipeline/04_check_separability.py
# --------------------------------------------------------------------------
ACCEPT_ANGLE_DEG = 80.43   # same-/different-person angle crossover (step 04, this data)
MIN_VOTES = 12             # of REFS_PER_OCCUPANT reference photos

"""building_11: fifty masked identities (RMFRD) appended to the corpus and wired into the population and evaluation."""
import sys

import numpy as np
import pandas as pd
import pytest

from evaluation.evaluate_events import event_confusion, masked_cohort
from population.build import build_population
from population.corpus import BASE_EMB, BASE_META, load_corpus
from shared.experiment_config import ExperimentConfig, MaskedBuildingConfig
from shared.paths import CORPUS_DIR, REPO

MASKED = CORPUS_DIR / "masked"
have_corpus = (BASE_EMB.exists() and BASE_META.exists() and (MASKED / "emb_arcface.npy").exists())
needs_masked = pytest.mark.skipif(not have_corpus, reason="corpus/ or corpus/masked/ is not present")


@needs_masked
def test_masked_meta_has_20_unmasked_references_and_20_masked_probes_per_person():
    meta = pd.read_csv(MASKED / "meta.csv", dtype={"occupant_id": str})
    assert meta.occupant_id.nunique() == 50 and len(meta) == 2000
    assert (meta.groupby("occupant_id").size() == 40).all()
    for _, g in meta.groupby("occupant_id"):
        assert (g[g.split == "reference"].masked == 0).all() and (g[g.split == "reference"].shape[0] == 20)
        assert (g[g.split == "test"].masked == 1).all() and (g[g.split == "test"].shape[0] == 20)
    assert meta.occupant_id.min() >= "9000000"                           # cannot collide with the 500 corpus ids


@needs_masked
def test_masked_embeddings_are_unit_vectors_in_the_corpus_format():
    emb = np.load(MASKED / "emb_arcface.npy")
    assert emb.shape == (2000, 512) and emb.dtype == np.float32
    assert np.allclose(np.linalg.norm(emb, axis=1), 1.0, atol=1e-4)
    base = np.load(BASE_EMB, mmap_mode="r")
    assert np.allclose(np.linalg.norm(np.asarray(base[:200]), axis=1), 1.0, atol=1e-4)    # same convention


@needs_masked
def test_extended_corpus_keeps_every_base_row_and_appends_the_masked_ones():
    corpus = load_corpus(ExperimentConfig(masked_building=MaskedBuildingConfig(enabled=True)))
    assert corpus.masked and len(corpus.meta) == 22000 and len(corpus.fixed_building) == 50
    ext = np.load(corpus.emb_path, mmap_mode="r")
    assert ext.shape == (22000, 512)
    assert np.array_equal(np.asarray(ext[:20000:997]), np.asarray(np.load(BASE_EMB, mmap_mode="r")[:20000:997]))
    assert (corpus.meta.iloc[20000:].building == "building_11").all()


@needs_masked
def test_disabled_gives_the_original_ten_building_corpus():
    corpus = load_corpus(ExperimentConfig(masked_building=MaskedBuildingConfig(enabled=False)))
    assert not corpus.masked and len(corpus.meta) == 20000 and corpus.emb_path == BASE_EMB


@pytest.fixture
def masked_enabled(tmp_path, monkeypatch):
    """The project default now runs the original ten buildings; this test needs the optional masked eleventh one switched on."""
    import shared.experiment_config as experiment_config
    config = tmp_path / "experiment.yaml"
    config.write_text("masked_building:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setattr(experiment_config, "CONFIG_FILE", config)


@needs_masked
def test_population_adds_building_11_without_reshuffling_the_other_ten(masked_enabled):
    pop = build_population(5)
    assert pop.n_buildings == 11 and pop.masked_building == "building_11" and len(pop.person_building) == 550
    by_b = pop.by_building
    assert len(by_b["building_11"]) == 50 and all(o >= "9000000" for o in by_b["building_11"])
    assert all(len(v) == 50 for v in by_b.values())
    plain = build_population(5, pd.read_csv(BASE_META))                   # original recipe, 500 people, 10 buildings
    assert {o: b for o, b in pop.person_building.items() if o < "9000000"} == plain.person_building
    assert build_population(5).person_building == pop.person_building    # deterministic


def test_event_confusion_splits_the_masked_cohort_from_everyone_else():
    rows = []
    for i, (home, now, truth, pred) in enumerate([
            ("building_1", "building_1", "A", "A"), ("building_1", "building_2", "A", None),
            ("building_11", "building_11", "M", "M"), ("building_11", "building_11", "M", None),
            ("building_11", "building_3", "M", "N"), ("building_11", "building_3", "M", "M")]):
        rows.append({"event_id": f"E{i}", "occupant_id": truth, "home_building": home,
                     "event_type": "local" if home == now else "visitor", "predicted_occupant_id": pred})
    out = event_confusion(rows, "building_11")
    assert out["masked"]["tp"] + out["unmasked"]["tp"] == out["all"]["tp"]
    assert out["masked"]["fn"] + out["unmasked"]["fn"] == out["all"]["fn"]
    assert out["masked_local"]["tp"] + out["masked_visitor"]["tp"] == out["masked"]["tp"]
    cohort = masked_cohort(rows, "building_11")
    assert cohort["masked"]["events"] == 4 and cohort["masked"]["accuracy"] == 0.5
    assert cohort["masked"]["abstained"] == 1 and cohort["unmasked"]["events"] == 2
    assert "masked" not in event_confusion(rows)                          # nothing extra without a masked building


# ----------------------------------------------------------------------------- the builder tool
sys.path.insert(0, str(REPO / "tools"))


def test_augmentation_is_seeded_and_changes_the_image():
    pytest.importorskip("cv2")
    import build_masked_building as tool
    img = np.random.default_rng(1).integers(0, 255, (160, 160, 3), dtype=np.uint8)
    one, two = tool.augment(img, tool.seeded("y")), tool.augment(img, tool.seeded("y"))
    assert np.array_equal(one, two) and not np.array_equal(one, img)
    assert not np.array_equal(tool.augment(img, tool.seeded("z")), one)


@needs_masked
def test_masked_probes_are_real_photos_topped_up_with_augmented_copies_only():
    meta = pd.read_csv(MASKED / "meta.csv", dtype={"occupant_id": str})
    test = meta[meta.split == "test"]
    assert set(test.source) <= {"real", "real_augmented"}                 # no synthetic masks
    assert set(meta[meta.split == "reference"].source) == {"real"}         # unmasked galleries are all real photos
    assert (test.groupby("occupant_id").source.apply(lambda s: (s == "real").sum()) >= 10).all()

import numpy as np
import pytest

from app.features import extract_windows
from app.owner_model import (
    MIN_WINDOWS_PER_CLASS, ModelError, ModelRegistry, OwnerModel, dataset_for, evaluate, load_recording,
    load_recordings, train_owner_model, walking_windows,
)
from simulator import synth

PEOPLE = {name: synth.Person.random(i) for i, name in enumerate(["asha", "ravi", "meena"])}


def windows(person, seed, seconds=90):
    a, g = synth.walk(person, seconds, seed=seed)
    X, motion = extract_windows(a, g, 50.0)
    return X[motion >= 0.8]


@pytest.fixture(scope="module")
def asha_model():
    # Three sessions per person, as the calibration protocol gives (own phone + swaps).
    # A single training session leaves a thin margin: the model cannot tell
    # session-to-session variation from the person.
    own = np.vstack([windows(PEOPLE["asha"], s, 30) for s in range(3)])
    others = np.vstack([windows(PEOPLE[n], 10 + s, 30) for n in ("ravi", "meena") for s in range(3)])
    return train_owner_model(own, others)


def test_model_separates_owner_from_others_in_a_new_session(asha_model):
    # Synthetic gaits: proves the pipeline works, not real-world accuracy.
    owner = asha_model.score(windows(PEOPLE["asha"], 101))
    other = asha_model.score(windows(PEOPLE["ravi"], 102))
    assert owner.mean() > 0.8 and other.mean() < 0.2
    assert np.mean(owner >= 0.5) > 0.95 and np.mean(other < 0.5) > 0.95


def test_scores_are_probabilities(asha_model):
    s = asha_model.score(windows(PEOPLE["meena"], 5))
    assert ((0.0 <= s) & (s <= 1.0)).all()


def test_needs_enough_data_in_both_classes():
    good = windows(PEOPLE["asha"], 1)
    with pytest.raises(ValueError, match="other"):
        train_owner_model(good, good[: MIN_WINDOWS_PER_CLASS - 1])
    with pytest.raises(ValueError, match="owner"):
        train_owner_model(good[:3], good)


def test_save_load_roundtrip(tmp_path, asha_model):
    X = windows(PEOPLE["asha"], 7, seconds=20)
    asha_model.save(tmp_path / "m.joblib")
    loaded = OwnerModel.load(tmp_path / "m.joblib")
    np.testing.assert_allclose(loaded.score(X), asha_model.score(X))


def test_corrupt_model_file_raises_model_error(tmp_path):
    (tmp_path / "bad.joblib").write_bytes(b"not a model")
    with pytest.raises(ModelError):
        OwnerModel.load(tmp_path / "bad.joblib")


def test_registry_missing_is_none_and_reloads_on_change(tmp_path, asha_model):
    reg = ModelRegistry(tmp_path)
    assert reg.get("asha") is None
    reg.put("asha", asha_model)
    first = reg.get("asha")
    assert first is not None and reg.get("asha") is first        # cached
    reg.put("asha", asha_model)
    p = reg.path("asha")
    import os
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 5))
    assert reg.get("asha") is not first                          # mtime changed -> reloaded


@pytest.mark.parametrize("bad", ["../x", "a/b", "", ".hidden", "a b", "x" * 65])
def test_registry_rejects_unsafe_student_ids(tmp_path, bad):
    with pytest.raises(ValueError):
        ModelRegistry(tmp_path).path(bad)


# ---- recordings + evaluation ----------------------------------------------------

def write_recordings(directory, pairs, seconds=60):
    for i, (carrier, device) in enumerate(pairs):
        a, g = synth.walk(PEOPLE[carrier], seconds, seed=10 + i)
        synth.write_csv(directory / f"{carrier}__{device}.csv", a, g)


SWAP = [(c, d) for c in PEOPLE for d in PEOPLE]


def test_csv_roundtrip_and_naming(tmp_path):
    write_recordings(tmp_path, [("asha", "ravi")], seconds=12)
    rec = load_recording(tmp_path / "asha__ravi.csv")
    assert (rec.carrier, rec.device) == ("asha", "ravi")
    assert rec.fs == pytest.approx(50.0, rel=0.01) and rec.accel.shape == (600, 3)


def test_csv_with_wrong_header_is_rejected(tmp_path):
    (tmp_path / "a__a.csv").write_text("time,x,y\n0,1,2\n1,1,2\n")
    with pytest.raises(ValueError, match="header"):
        load_recording(tmp_path / "a__a.csv")


def test_dataset_labels_by_carrier_not_device(tmp_path):
    write_recordings(tmp_path, SWAP, seconds=30)
    recs = load_recordings(tmp_path)
    own, other = dataset_for("asha", recs)
    per = len(walking_windows(recs[0]))
    assert len(own) == 3 * per and len(other) == 6 * per       # asha carried 3 of the 9 recordings


def test_evaluate_uses_leave_one_recording_out_when_possible(tmp_path):
    write_recordings(tmp_path, SWAP, seconds=45)
    report = evaluate("asha", load_recordings(tmp_path))
    assert report["method"] == "leave-one-recording-out"
    assert report["balanced_accuracy"] > 0.9


def test_evaluate_flags_optimistic_fallback_with_single_recording(tmp_path):
    write_recordings(tmp_path, [("asha", "asha"), ("ravi", "ravi")], seconds=90)
    report = evaluate("asha", load_recordings(tmp_path))
    assert "optimistic" in report["method"]


def test_evaluate_needs_other_people(tmp_path):
    write_recordings(tmp_path, [("asha", "asha")], seconds=90)
    with pytest.raises(ValueError, match="at least one other"):
        evaluate("asha", load_recordings(tmp_path))

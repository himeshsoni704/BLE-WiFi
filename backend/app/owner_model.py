"""Per-student owner-vs-other gait model (scikit-learn).

Training data comes from short calibration walks: each recording is a CSV named
`<carrier>__<device>.csv` with header `t,ax,ay,az,gx,gy,gz` (seconds, m/s^2,
rad/s). The label is who *carried* the phone, regardless of whose phone it was,
so swapped-phone recordings teach the model to recognise the walker rather than
the sensor hardware.

Models are pickled with joblib and only ever loaded from the configured model
directory. Never point that directory at untrusted files.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .features import FEATURE_NAMES, MIN_FS, MAX_FS, WALKING_THRESHOLD, extract_windows

MODEL_VERSION = 1
MIN_WINDOWS_PER_CLASS = 20
STUDENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_CSV_HEADER = "t,ax,ay,az,gx,gy,gz"


class ModelError(Exception):
    pass


class OwnerModel:
    def __init__(self, pipeline: Pipeline):
        self._pipeline = pipeline

    def score(self, features: np.ndarray) -> np.ndarray:
        """P(owner) for each row of `features`."""
        classes = list(self._pipeline.classes_)
        return self._pipeline.predict_proba(features)[:, classes.index(1)]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        joblib.dump(
            {"version": MODEL_VERSION, "features": FEATURE_NAMES, "pipeline": self._pipeline}, tmp
        )
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "OwnerModel":
        try:
            blob = joblib.load(path)
            if blob["version"] != MODEL_VERSION or tuple(blob["features"]) != FEATURE_NAMES:
                raise ModelError(f"{path.name}: model was built for a different feature set")
            return cls(blob["pipeline"])
        except ModelError:
            raise
        except Exception as exc:
            raise ModelError(f"{path.name}: cannot load model ({exc})") from exc


def _pipeline(seed: int) -> Pipeline:
    return Pipeline([
        ("scale", StandardScaler()),
        ("forest", RandomForestClassifier(
            n_estimators=100, min_samples_leaf=2, class_weight="balanced",
            random_state=seed, n_jobs=1,
        )),
    ])


def train_owner_model(owner_windows: np.ndarray, other_windows: np.ndarray, seed: int = 0) -> OwnerModel:
    for name, arr in (("owner", owner_windows), ("other", other_windows)):
        if len(arr) < MIN_WINDOWS_PER_CLASS:
            raise ValueError(
                f"need at least {MIN_WINDOWS_PER_CLASS} walking windows of {name} data, got {len(arr)}"
            )
    X = np.vstack([owner_windows, other_windows])
    y = np.concatenate([np.ones(len(owner_windows), int), np.zeros(len(other_windows), int)])
    pipe = _pipeline(seed)
    pipe.fit(X, y)
    return OwnerModel(pipe)


class ModelRegistry:
    """Loads per-student models from a directory, reloading when a file changes."""

    def __init__(self, model_dir: str | Path):
        self.dir = Path(model_dir)
        self._cache: dict[str, tuple[float, OwnerModel]] = {}
        self._lock = threading.Lock()

    def path(self, student_id: str) -> Path:
        if not STUDENT_ID_RE.match(student_id):
            raise ValueError(f"invalid student id: {student_id!r}")
        return self.dir / f"{student_id}.joblib"

    def get(self, student_id: str) -> OwnerModel | None:
        path = self.path(student_id)
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            with self._lock:
                self._cache.pop(student_id, None)
            return None
        with self._lock:
            cached = self._cache.get(student_id)
            if cached and cached[0] == mtime:
                return cached[1]
        model = OwnerModel.load(path)
        with self._lock:
            self._cache[student_id] = (mtime, model)
        return model

    def put(self, student_id: str, model: OwnerModel) -> None:
        model.save(self.path(student_id))


@dataclass
class Recording:
    carrier: str
    device: str
    accel: np.ndarray
    gyro: np.ndarray
    fs: float


def load_recording(path: str | Path) -> Recording:
    path = Path(path)
    carrier, sep, device = path.stem.partition("__")
    if not sep:
        device = carrier
    with open(path) as fh:
        header = fh.readline().strip().replace(" ", "")
    if header != _CSV_HEADER:
        raise ValueError(f"{path.name}: header must be {_CSV_HEADER!r}, got {header!r}")
    data = np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2)
    if data.shape[0] < 2 or data.shape[1] != 7:
        raise ValueError(f"{path.name}: expected 7 columns and at least 2 rows")
    t = data[:, 0]
    span = float(t[-1] - t[0])
    if span <= 0:
        raise ValueError(f"{path.name}: timestamps must increase")
    fs = (len(t) - 1) / span
    if not MIN_FS <= fs <= MAX_FS:
        raise ValueError(f"{path.name}: sample rate {fs:.1f} Hz outside {MIN_FS}-{MAX_FS}")
    return Recording(carrier, device, data[:, 1:4], data[:, 4:7], fs)


def load_recordings(directory: str | Path) -> list[Recording]:
    files = sorted(Path(directory).glob("*.csv"))
    if not files:
        raise ValueError(f"no .csv recordings in {directory}")
    return [load_recording(f) for f in files]


def walking_windows(rec: Recording, walking_threshold: float = WALKING_THRESHOLD) -> np.ndarray:
    feats, motion = extract_windows(rec.accel, rec.gyro, rec.fs)
    return feats[motion >= walking_threshold]


def dataset_for(owner: str, recordings: list[Recording]) -> tuple[np.ndarray, np.ndarray]:
    """(owner windows, other windows), labelled by who carried the phone."""
    own = [walking_windows(r) for r in recordings if r.carrier == owner]
    other = [walking_windows(r) for r in recordings if r.carrier != owner]
    empty = np.empty((0, len(FEATURE_NAMES)))
    return (np.vstack(own) if own else empty, np.vstack(other) if other else empty)


def _score_split(model: OwnerModel, owner_test: list[np.ndarray], other_test: list[np.ndarray]) -> dict:
    xp, xn = np.vstack(owner_test), np.vstack(other_test)
    tpr = float(np.mean(model.score(xp) >= 0.5))
    tnr = float(np.mean(model.score(xn) < 0.5))
    return {"owner_recall": tpr, "other_rejection": tnr, "balanced_accuracy": (tpr + tnr) / 2,
            "test_owner_windows": len(xp), "test_other_windows": len(xn)}


def evaluate(owner: str, recordings: list[Recording], seed: int = 0) -> dict:
    """Honest hold-out estimate for one student's model.

    Leave-one-recording-out: each recording in turn is held out whole, the model
    is trained on the rest, and the held-out windows are scored. That tests
    generalisation to a new session (new day, new phone placement), which is what
    matters in use. It needs the owner to have at least two recordings; folds that
    would leave either class without training data are skipped.

    If the owner has only one recording, falls back to a time-blocked split inside
    each recording (first 70% train, last 30% test). That is optimistic, because
    train and test share the same session, and the result says so.
    """
    windows = [walking_windows(r) for r in recordings]
    hits = {"owner": [], "other": []}
    for held, rec in enumerate(recordings):
        train = [(r, w) for i, (r, w) in enumerate(zip(recordings, windows)) if i != held]
        pos = [w for r, w in train if r.carrier == owner]
        neg = [w for r, w in train if r.carrier != owner]
        if not pos or not neg or sum(map(len, pos)) < MIN_WINDOWS_PER_CLASS \
                or sum(map(len, neg)) < MIN_WINDOWS_PER_CLASS or not len(windows[held]):
            continue
        model = train_owner_model(np.vstack(pos), np.vstack(neg), seed)
        scores = model.score(windows[held])
        hits["owner" if rec.carrier == owner else "other"].extend(
            (scores >= 0.5) if rec.carrier == owner else (scores < 0.5)
        )
    if hits["owner"] and hits["other"]:
        tpr, tnr = float(np.mean(hits["owner"])), float(np.mean(hits["other"]))
        return {"method": "leave-one-recording-out", "owner_recall": tpr, "other_rejection": tnr,
                "balanced_accuracy": (tpr + tnr) / 2,
                "test_owner_windows": len(hits["owner"]), "test_other_windows": len(hits["other"])}

    train_pos, train_neg, test_pos, test_neg = [], [], [], []
    for rec, w in zip(recordings, windows):
        cut = int(len(w) * 0.7)
        head, tail = w[:max(cut - 1, 0)], w[cut + 1:]      # gap: windows overlap by half
        (train_pos if rec.carrier == owner else train_neg).append(head)
        (test_pos if rec.carrier == owner else test_neg).append(tail)
    if not train_pos or not train_neg:
        raise ValueError(f"need recordings carried by {owner} and by at least one other person")
    model = train_owner_model(np.vstack(train_pos), np.vstack(train_neg), seed)
    return {"method": "time-blocked (optimistic: same session in train and test)",
            **_score_split(model, test_pos, test_neg)}

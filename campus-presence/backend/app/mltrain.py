"""Training + honest evaluation for the Wi-Fi localizer and the Isolation Forest.

Both models are trained and evaluated on SYNTHETIC data produced by simulation/. The
metrics printed here describe how well the models fit the simulator's assumptions, NOT how
they would perform on a real campus.
"""
from __future__ import annotations

import csv
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score, precision_recall_fscore_support,
                             roc_auc_score)

from simulation import campus

from .anomaly import FEATURES, AnomalyModel, train_isolation_forest
from .wifi import WifiLocalizer, train_localizer


# ---------------------------------------------------------------------------------------- Wi-Fi

def generate_wifi_dataset(n_per_zone: int, seed: int) -> tuple[list[dict], list[str]]:
    rng = np.random.default_rng(seed)
    fps, ys = [], []
    for z in campus.ZONE_IDS:
        for _ in range(n_per_zone):
            x, y = campus.sample_position(z, rng)
            # a mix of clean scans and scans with a temporarily weak AP
            deg = None
            if rng.random() < 0.08:
                deg = {str(rng.choice(campus.AP_IDS)): float(rng.uniform(6, 25))}
            fps.append(campus.sample_fingerprint(x, y, rng, degraded=deg))
            ys.append(z)
    return fps, ys


def write_wifi_csv(path: Path, fps: list[dict], ys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["zone", *campus.AP_IDS])
        for fp, z in zip(fps, ys):
            w.writerow([z, *[fp.get(a, "") for a in campus.AP_IDS]])


def read_wifi_csv(path: Path) -> tuple[list[dict], list[str]]:
    fps, ys = [], []
    with open(path) as fh:
        for row in csv.DictReader(fh):
            ys.append(row["zone"])
            fps.append({a: float(row[a]) for a in campus.AP_IDS if row.get(a) not in (None, "")})
    return fps, ys


def evaluate_wifi(model: WifiLocalizer, fps: list[dict], ys: list[str]) -> dict:
    preds = model.predict_many(fps)
    yhat = [p.zone for p in preds]
    labels = sorted(set(ys) | set(yhat))
    p, r, f, support = precision_recall_fscore_support(ys, yhat, labels=labels, zero_division=0)
    cm = confusion_matrix(ys, yhat, labels=labels)
    errors = [(y, h) for y, h in zip(ys, yhat) if y != h]

    def adjacent(a: str, b: str) -> bool:
        return a in campus.ZONE_BY_ID and b in campus.ZONE_BY_ID and campus.zone_distance(a, b) <= 12.5

    conf = np.array([pr.confidence for pr in preds])
    correct = np.array([y == h for y, h in zip(ys, yhat)])
    return {
        "n_test": len(ys),
        "accuracy": float(accuracy_score(ys, yhat)),
        "macro_precision": float(np.mean(p)), "macro_recall": float(np.mean(r)),
        "macro_f1": float(f1_score(ys, yhat, labels=labels, average="macro", zero_division=0)),
        "errors_adjacent_fraction": float(np.mean([adjacent(a, b) for a, b in errors])) if errors else 0.0,
        "mean_confidence_correct": float(conf[correct].mean()) if correct.any() else None,
        "mean_confidence_wrong": float(conf[~correct].mean()) if (~correct).any() else None,
        "per_zone": {z: {"precision": float(p[i]), "recall": float(r[i]), "f1": float(f[i]), "support": int(support[i])}
                     for i, z in enumerate(labels)},
        "labels": labels, "confusion_matrix": cm.tolist(),
    }


def train_wifi(n_train_per_zone: int = 400, n_test_per_zone: int = 150, seed: int = 0,
               kinds: tuple[str, ...] = ("knn", "rf"), fps_ys: tuple[list, list] | None = None) -> tuple[WifiLocalizer, dict]:
    """Train each kind on the training set, pick the better on a validation split, evaluate on an
    independent test set generated with a different seed."""
    fps, ys = fps_ys or generate_wifi_dataset(n_train_per_zone, seed)
    test_fps, test_ys = generate_wifi_dataset(n_test_per_zone, seed + 10_000)
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(fps))
    cut = int(0.8 * len(idx))
    tr, va = idx[:cut], idx[cut:]
    probe = train_localizer(np.zeros((1, len(campus.AP_IDS))), ["x"], campus.AP_IDS, "knn")   # for vectorise()
    results, best = {}, None
    for kind in kinds:
        m = train_localizer(probe.vectorise([fps[i] for i in tr]), [ys[i] for i in tr], campus.AP_IDS, kind, seed)
        acc = float(np.mean([p.zone == ys[i] for p, i in zip(m.predict_many([fps[i] for i in va]), va)]))
        results[kind] = {"validation_accuracy": acc}
        if best is None or acc > best[0]:
            best = (acc, kind)
    kind = best[1]
    final = train_localizer(probe.vectorise(fps), ys, campus.AP_IDS, kind, seed)
    report = evaluate_wifi(final, test_fps, test_ys)
    report.update({"selected_model": kind, "candidates": results, "n_train": len(fps), "data": "synthetic",
                   "physical_model": {"wall_loss_db": campus.WALL_LOSS_DB, "shadowing_db": campus.SHADOWING_DB,
                                      "fading_db": campus.FADING_DB},
                   "caveat": "Synthetic fingerprints from a log-distance model: fits the simulator, not a real campus.",
                   "trained_at": time.time()})
    final.metrics = {k: report[k] for k in ("accuracy", "macro_f1", "n_test", "selected_model")}
    return final, report


def save_wifi_report(report: dict, model_dir: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "wifi_report.json").write_text(json.dumps(report, indent=2))
    with open(model_dir / "wifi_confusion_matrix.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["true\\pred", *report["labels"]])
        for lab, row in zip(report["labels"], report["confusion_matrix"]):
            w.writerow([lab, *row])


# ---------------------------------------------------------------------------------- anomalies

def build_anomaly_dataset(svc, n_students: int, seed: int, scenarios: dict[str, int]) -> list[dict]:
    """Run the simulator in an in-memory DB and return one labelled feature row per evidence-bearing
    (student, session). Features come from the same pipeline code used at run time."""
    from simulation.generator import CampusSim
    from app.db import session_scope
    from app.models import SessionRow
    from app.pipeline import evaluate_session

    rows: list[dict] = []
    with session_scope(svc.SessionLocal) as db:
        sim = CampusSim(svc, n_students=n_students, seed=seed)
        sim.build(db)
        sim.generate_normal(db)
        rng = np.random.default_rng(seed + 1)
        sessions = sorted(sim.sessions.values(), key=lambda s: s.id)
        for name, count in scenarios.items():
            for _ in range(count):
                for _try in range(20):
                    s = sessions[int(rng.integers(len(sessions)))]
                    try:
                        sim.scenario(db, name, session=s, evaluate=False)
                        break
                    except RuntimeError:
                        continue
        for s in sessions:
            for r in evaluate_session(db, svc, s, now=s.end_ts + 60, persist=False, use_model=False, publish=False):
                if not r.eligible_for_if:
                    continue
                label = sim.labels.get((s.id, r.student_key), "normal")
                rows.append({"session_id": s.id, "student_key": r.student_key, "label": label,
                             "is_anomaly": int(label in ("proxy", "impossible_movement", "wifi_ble_mismatch",
                                                         "token_replay", "short_presence")),
                             "rule_hits": ",".join(sorted({h.rule for h in r.rules if h.severity in ("warn", "high")})),
                             **{f: round(r.features[f], 4) for f in FEATURES}})
    return rows


def write_feature_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["session_id", "student_key", "label", "is_anomaly", "rule_hits", *FEATURES]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def read_feature_csv(path: Path) -> list[dict]:
    rows = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            for f in FEATURES:
                r[f] = float(r[f])
            r["is_anomaly"] = int(r["is_anomaly"])
            rows.append(r)
    return rows


def train_anomaly(rows: list[dict], seed: int = 0, contamination: float = 0.05,
                  extra_normal: list[dict] | None = None, train_anomaly_fraction: float = 0.03
                  ) -> tuple[AnomalyModel, dict]:
    """Train on mostly-normal rows, evaluate on a held-out split containing every anomaly type.

    Isolation Forest is unsupervised and can only split on features that vary in its training data.
    A purely clean training set would make e.g. `token_reuse_count` constant (always 0), and the forest
    could never isolate a replay. So, like real operational data, the training set is *mostly normal
    with a small unlabeled contamination* (`train_anomaly_fraction` of rows, drawn from whole anomalous
    sessions that are then excluded from the test set). `extra_normal` are faculty-confirmed false positives.
    """
    rng = np.random.default_rng(seed)
    normal = [r for r in rows if r["label"] == "normal"]
    hard_neg = [r for r in rows if r["label"] == "false_positive"]
    anomalies = [r for r in rows if r["is_anomaly"]]
    order = rng.permutation(len(normal))
    cut = int(0.7 * len(normal))
    train_normal = [normal[i] for i in order[:cut]]
    test_normal = [normal[i] for i in order[cut:]]
    # whole anomalous sessions go to train or test, never both (proxy groups share a session)
    an_sessions = sorted({r["session_id"] for r in anomalies})
    rng.shuffle(an_sessions)
    budget = max(3, round(train_anomaly_fraction * len(train_normal)))
    contaminants, train_sessions = [], set()
    for sid in an_sessions:
        grp = [r for r in anomalies if r["session_id"] == sid]
        if len(contaminants) + len(grp) <= budget:
            contaminants += grp
            train_sessions.add(sid)
    anomalies = [r for r in anomalies if r["session_id"] not in train_sessions]
    train_rows = train_normal + contaminants + list(extra_normal or [])
    model = train_isolation_forest(train_rows, contamination=contamination, seed=seed,
                                   meta={"trained_on": "synthetic, mostly-normal behaviour", "n_train": len(train_rows),
                                         "n_unlabeled_contaminants": len(contaminants),
                                         "extra_confirmed_false_positives": len(extra_normal or []),
                                         "trained_at": time.time()})
    test = test_normal + hard_neg + anomalies
    y_true = np.array([r["is_anomaly"] for r in test])
    scores = model.score(test)
    y_pred = np.array([int(s.flagged) for s in scores])
    raw = np.array([s.raw for s in scores])
    p, r_, f, _ = precision_recall_fscore_support(y_true, y_pred, average="binary", zero_division=0)
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    per_scenario = {}
    for lab in sorted({r["label"] for r in test}):
        idx = [i for i, r in enumerate(test) if r["label"] == lab]
        flagged = float(np.mean([y_pred[i] for i in idx]))
        rule_hit = float(np.mean([bool(test[i]["rule_hits"]) for i in idx]))
        per_scenario[lab] = {"n": len(idx), "flagged_by_isolation_forest": flagged, "hit_by_deterministic_rules": rule_hit}
    sweep = []
    for c in (0.02, 0.05, 0.08, 0.12):
        m = train_isolation_forest(train_rows, contamination=c, seed=seed)
        pred = np.array([int(x.flagged) for x in m.score(test)])
        pp, rr, ff, _ = precision_recall_fscore_support(y_true, pred, average="binary", zero_division=0)
        normal_idx = [i for i, r in enumerate(test) if r["label"] == "normal"]
        sweep.append({"contamination": c, "precision": float(pp), "recall": float(rr), "f1": float(ff),
                      "normal_sessions_flagged": float(np.mean([pred[i] for i in normal_idx]))})
    report = {
        "threshold_sweep": sweep,
        "threshold_note": ("`contamination` sets the flagging cut-off. It trades review load (normal sessions flagged) "
                           "against recall; the default was chosen after looking at this synthetic split, so treat "
                           "the reported recall as optimistic."),
        "n_train_rows": len(train_rows), "n_train_unlabeled_contaminants": len(contaminants), "n_test": len(test), "n_test_anomalies": int(y_true.sum()),
        "accuracy": float(accuracy_score(y_true, y_pred)), "precision": float(p), "recall": float(r_), "f1": float(f),
        "roc_auc_raw_score": float(roc_auc_score(y_true, -raw)) if 0 < y_true.sum() < len(y_true) else None,
        "confusion_matrix": {"labels": ["normal", "anomaly"], "matrix": cm.tolist(),
                             "note": "rows = true, columns = predicted (flagged by Isolation Forest)"},
        "per_scenario": per_scenario, "features": FEATURES, "contamination": contamination,
        "data": "synthetic",
        "caveat": ("Anomalies are patterns defined by this project's simulator, so recall here shows the forest can "
                   "separate those patterns, not that it would catch real misconduct. 'false_positive' rows are "
                   "benign-but-odd cases, counted as normal. Isolation Forest flags unusual combinations; it does "
                   "not decide attendance."),
    }
    return model, report


def save_anomaly_report(report: dict, model_dir: Path, name: str = "anomaly_report.json") -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / name).write_text(json.dumps(report, indent=2))


def default_scenarios(n_students: int) -> dict[str, int]:
    k = max(1, n_students // 60)
    return {"proxy": k, "impossible_movement": 2 * k, "wifi_ble_mismatch": 2 * k, "token_replay": 2 * k,
            "short_presence": 3 * k, "false_positive": 2 * k}

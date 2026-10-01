#!/usr/bin/env python
"""Retrain the Isolation Forest including faculty feedback.

    python ml/retrain_anomaly_model.py [--repeat 5]

Reads ml/data/anomaly_features.csv (synthetic baseline) and ml/data/feedback_dataset.csv (written by
POST /feedback). Faculty-confirmed FALSE POSITIVES are added to the training data as normal behaviour
(repeated `--repeat` times, because an Isolation Forest cannot weight rows). Confirmed anomalies are
used only to report how the model scores them: an unsupervised forest has no use for labels.

This is the ONLY thing that changes the forest. RAG feedback never retrains it.
After retraining, restart the backend or call POST /models/reload.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import time

import _common  # noqa: F401
from _common import DATA, MODELS

from app import mltrain
from app.anomaly import FEATURES, AnomalyModel


def read_feedback(path) -> list[dict]:
    out = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            if any(r.get(f) in (None, "") for f in FEATURES):
                continue
            row = {f: float(r[f]) for f in FEATURES}
            row.update(label=r["label"], source=r.get("source", ""), scenario=r.get("scenario", ""), anomaly_id=r["anomaly_id"])
            out.append(row)
    return out


def flag_rate(model: AnomalyModel, rows: list[dict]) -> float | None:
    return None if not rows else sum(s.flagged for s in model.score(rows)) / len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeat", type=int, default=5, help="times each confirmed false positive is repeated in training")
    ap.add_argument("--contamination", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    base = DATA / "anomaly_features.csv"
    fb_path = DATA / "feedback_dataset.csv"
    if not base.exists():
        print("run `python ml/generate_dataset.py` first")
        return 1
    if not fb_path.exists():
        print("no feedback yet (ml/data/feedback_dataset.csv missing): mark anomalies in the dashboard first")
        return 1
    rows = mltrain.read_feature_csv(base)
    fb = read_feedback(fb_path)
    false_pos = [r for r in fb if r["label"] == "normal"]
    confirmed = [r for r in fb if r["label"] == "anomaly"]
    print(f"feedback rows: {len(fb)}  (confirmed false positives: {len(false_pos)}, confirmed anomalies: {len(confirmed)})")
    if not fb:
        print("nothing to learn from yet")
        return 1

    old_path = MODELS / "isolation_forest.joblib"
    before = AnomalyModel.load(old_path) if old_path.exists() else None
    model, rep = mltrain.train_anomaly(rows, seed=args.seed, contamination=args.contamination,
                                       extra_normal=false_pos * args.repeat)
    after_fp, after_conf = flag_rate(model, false_pos), flag_rate(model, confirmed)
    before_fp = flag_rate(before, false_pos) if before else None
    before_conf = flag_rate(before, confirmed) if before else None
    if before is not None:
        shutil.copy(old_path, MODELS / "isolation_forest.prev.joblib")
    model.save(old_path)
    mltrain.save_anomaly_report(rep, MODELS)
    hist = MODELS / "model_history.json"
    history = json.loads(hist.read_text()) if hist.exists() else []
    history.append({"ts": time.time(), "feedback_rows": len(fb), "false_positives_added": len(false_pos),
                    "repeat": args.repeat, "flag_rate_on_false_positives": {"before": before_fp, "after": after_fp},
                    "flag_rate_on_confirmed_anomalies": {"before": before_conf, "after": after_conf},
                    "synthetic_test": {k: rep[k] for k in ("precision", "recall", "f1")}})
    hist.write_text(json.dumps(history, indent=2))

    def pct(v):
        return "n/a" if v is None else f"{v * 100:.0f}%"
    print(f"flagged on the faculty-confirmed FALSE POSITIVES:  before {pct(before_fp)}  ->  after {pct(after_fp)}")
    print(f"flagged on the faculty-confirmed ANOMALIES:        before {pct(before_conf)}  ->  after {pct(after_conf)}")
    print("   (the false positives are in the training data now, so 'after' is optimistic for them;")
    print("    the number to watch is whether confirmed anomalies are still flagged)")
    print(f"synthetic held-out test: precision {rep['precision']:.2f}  recall {rep['recall']:.2f}  F1 {rep['f1']:.2f}")
    print("saved ml/models/isolation_forest.joblib (previous copy: isolation_forest.prev.joblib)")
    print("restart the backend or POST /models/reload to use it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

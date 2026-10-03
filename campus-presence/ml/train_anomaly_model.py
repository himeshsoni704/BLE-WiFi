#!/usr/bin/env python
"""Train the Isolation Forest on mostly-normal synthetic behaviour and evaluate on held-out anomalies.

    python ml/train_anomaly_model.py [--contamination 0.05]

Saves ml/models/isolation_forest.joblib and anomaly_report.json. Isolation Forest finds UNUSUAL
COMBINATIONS of features; it does not decide attendance. The injected anomalies are this project's
own synthetic patterns, so recall shows the forest separates them, not that it catches real cheating.
"""
from __future__ import annotations

import argparse

import _common  # noqa: F401
from _common import DATA, MODELS

from app import mltrain


def print_report(rep: dict) -> None:
    print(f"train rows: {rep['n_train_rows']} (incl. {rep['n_train_unlabeled_contaminants']} unlabeled anomalous rows, "
          f"as in real operational data)")
    print(f"held-out test: {rep['n_test']} rows, {rep['n_test_anomalies']} injected anomalies")
    print(f"  accuracy {rep['accuracy']:.3f}  precision {rep['precision']:.3f}  recall {rep['recall']:.3f}  "
          f"F1 {rep['f1']:.3f}  ROC-AUC(raw score) {rep['roc_auc_raw_score']:.3f}")
    cm = rep["confusion_matrix"]["matrix"]
    print("  confusion matrix (rows = truth, cols = flagged):")
    print(f"                 normal  anomaly")
    print(f"    normal      {cm[0][0]:6d}  {cm[0][1]:6d}")
    print(f"    anomaly     {cm[1][0]:6d}  {cm[1][1]:6d}")
    print("  per scenario: share flagged by Isolation Forest / share hit by deterministic rules")
    for lab, v in rep["per_scenario"].items():
        print(f"    {lab:22s} n={v['n']:4d}   IF {v['flagged_by_isolation_forest'] * 100:5.1f}%   rules {v['hit_by_deterministic_rules'] * 100:5.1f}%")
    print("  threshold sweep (contamination -> precision / recall / normal sessions flagged):")
    for s in rep["threshold_sweep"]:
        print(f"    {s['contamination']:.2f}  P {s['precision']:.2f}  R {s['recall']:.2f}  flagged-normal {s['normal_sessions_flagged'] * 100:.1f}%")
    if rep.get("weak_features"):
        print("  features the forest is nearly blind to (they almost never vary in training):")
        for w in rep["weak_features"]:
            print(f"    {w['feature']:<24} differs from the median in {w['fraction_of_training_rows_that_differ'] * 100:.1f}% of training rows")
        print(rep["weak_features_note"])
    print(rep["threshold_note"])
    print(rep["caveat"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--contamination", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    path = DATA / "anomaly_features.csv"
    if not path.exists():
        print(f"{path} not found: run `python ml/generate_dataset.py` first")
        return 1
    rows = mltrain.read_feature_csv(path)
    model, rep = mltrain.train_anomaly(rows, seed=args.seed, contamination=args.contamination)
    model.save(MODELS / "isolation_forest.joblib")
    mltrain.save_anomaly_report(rep, MODELS)
    print_report(rep)
    print("\nsaved ml/models/isolation_forest.joblib and anomaly_report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Train the Wi-Fi localizer (KNN vs Random Forest) on synthetic fingerprints.

    python ml/train_wifi_model.py

Saves ml/models/wifi_localization.joblib plus wifi_report.json / wifi_confusion_matrix.csv.
Metrics are on an INDEPENDENT synthetic test set. They describe the simulator, not a real campus.
"""
from __future__ import annotations

import argparse

import _common  # noqa: F401
from _common import DATA, MODELS

from app import mltrain


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--test-per-zone", type=int, default=150)
    args = ap.parse_args()

    csv_path = DATA / "wifi_fingerprints.csv"
    if csv_path.exists():
        fps_ys = mltrain.read_wifi_csv(csv_path)
        print(f"training on {len(fps_ys[1])} fingerprints from {csv_path.name}")
    else:
        fps_ys = None
        print("ml/data/wifi_fingerprints.csv not found: generating 400 per zone (run generate_dataset.py to persist it)")
    model, rep = mltrain.train_wifi(400, args.test_per_zone, args.seed, fps_ys=fps_ys)
    model.save(MODELS / "wifi_localization.joblib")
    mltrain.save_wifi_report(rep, MODELS)

    print(f"\nselected: {rep['selected_model']}  (validation accuracy: "
          + ", ".join(f"{k} {v['validation_accuracy']:.3f}" for k, v in rep['candidates'].items()) + ")")
    print(f"independent synthetic test set: n={rep['n_test']}")
    print(f"  accuracy        {rep['accuracy']:.3f}")
    print(f"  macro precision {rep['macro_precision']:.3f}")
    print(f"  macro recall    {rep['macro_recall']:.3f}")
    print(f"  macro F1        {rep['macro_f1']:.3f}")
    print(f"  {rep['errors_adjacent_fraction'] * 100:.0f}% of the errors are the room next door (<= 12.5 m)")
    print(f"  mean confidence: correct {rep['mean_confidence_correct']:.2f} vs wrong {rep['mean_confidence_wrong']:.2f} "
          "(confidence is NOT a calibrated probability)")
    worst = sorted(rep["per_zone"].items(), key=lambda kv: kv[1]["f1"])[:4]
    print("  weakest zones (F1): " + ", ".join(f"{z} {m['f1']:.2f}" for z, m in worst))
    print(f"\nsaved ml/models/wifi_localization.joblib, wifi_report.json, wifi_confusion_matrix.csv")
    print(rep["caveat"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

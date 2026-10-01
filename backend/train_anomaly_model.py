"""Train the Isolation Forest anomaly detector (brief section 16) on
synthetic campus attendance behaviour -- mostly normal, a configurable
share of injected anomalies -- then evaluate precision/recall/F1 against
the simulator's OWN ground-truth labels (used only for this offline
evaluation; the detector itself is trained on features alone, never on the
labels, matching brief section 17's "why Isolation Forest" rationale), and
save models/isolation_forest.joblib.

    python train_anomaly_model.py

Metrics are a SYNTHETIC benchmark; see train_wifi_model.py's docstring for
the same caveat (brief section 37).
"""
from __future__ import annotations

import argparse

import numpy as np
from sklearn.metrics import classification_report, confusion_matrix

from app.anomaly_iforest import AnomalyDetector
from app.wifi_knn import WifiFingerprintModel
from simulator.campus import features_from_event, generate_campus_dataset


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--students", type=int, default=600, help="bigger than the 300-student demo campus on purpose")
    ap.add_argument("--classrooms", type=int, default=20)
    ap.add_argument("--aps", type=int, default=10)
    ap.add_argument("--anomaly-rate", type=float, default=0.08)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--contamination", type=float, default=0.12,
                    help="see app/anomaly_iforest.py's module docstring for why this is above the "
                        "simulator's ~5.5%% true anomaly rate (a deliberate recall-favouring trade)")
    ap.add_argument("--out", default="models/isolation_forest.joblib")
    ap.add_argument("--wifi-model", default="models/wifi_localization.joblib",
                    help="an already-trained Wi-Fi model gives more realistic wifi_confidence features; "
                        "optional -- falls back to label-derived placeholders if not found")
    args = ap.parse_args(argv)

    rng = np.random.default_rng(args.seed)
    data = generate_campus_dataset(args.students, args.classrooms, args.aps, args.anomaly_rate, args.seed)
    classrooms_by_id = {c.classroom_id: c for c in data["classrooms"]}

    wifi_model = None
    try:
        wifi_model = WifiFingerprintModel.load(args.wifi_model)
    except FileNotFoundError:
        print(f"no Wi-Fi model at {args.wifi_model!r} yet -- run train_wifi_model.py first for more "
              f"realistic wifi_confidence features. Continuing with label-derived placeholders.\n")

    features = [features_from_event(e, classrooms_by_id, wifi_model) for e in data["events"]]
    y_anomalous = np.array([e.label != "normal" for e in data["events"]])

    order = rng.permutation(len(features))
    split = int(0.7 * len(features))
    train_idx, test_idx = order[:split], order[split:]

    # "Train on mostly normal behaviour" (brief section 17): drop the known-anomalous rows from
    # the training split entirely rather than mixing them in -- Isolation Forest only needs to
    # learn the shape of NORMAL data, and this is evaluated on a held-out split below.
    feats_train = [features[i] for i in train_idx if not y_anomalous[i]]
    if not feats_train:
        feats_train = [features[i] for i in train_idx]

    detector = AnomalyDetector(contamination=args.contamination, random_state=args.seed).fit(feats_train)

    feats_test = [features[i] for i in test_idx]
    y_test = y_anomalous[test_idx]
    preds = detector.predict_batch(feats_test)
    y_pred = np.array([p.is_anomalous for p in preds])

    print("=== SYNTHETIC BENCHMARK -- not a real-world accuracy measurement ===")
    print(f"{len(feats_train)} normal-only training rows")
    print(f"{len(feats_test)} test rows ({int(y_test.sum())} truly anomalous, "
          f"{int(len(y_test) - y_test.sum())} truly normal)\n")
    print(classification_report(y_test, y_pred, target_names=["normal", "anomalous"], zero_division=0))
    print("confusion matrix [[TN, FP], [FN, TP]]:")
    print(confusion_matrix(y_test, y_pred))
    print(f"\ncontamination={args.contamination}: several of the 10 features (ble_duration, mean_ble_rssi,\n"
          f"nearby_device_count) vary just as much for normal rows as for most anomaly types, which dilutes\n"
          f"Isolation Forest's averaged path length -- recall is intentionally favoured over precision here\n"
          f"(REVIEW_REQUIRED is a one-click human check; a missed proxy-attendance case is not). See\n"
          f"app/anomaly_iforest.py's module docstring for the measured precision/recall trade behind this.")

    detector.save(args.out)
    print(f"\nsaved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

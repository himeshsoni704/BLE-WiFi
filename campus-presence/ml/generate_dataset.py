#!/usr/bin/env python
"""Generate the SYNTHETIC datasets used for training.

    python ml/generate_dataset.py [--students 500] [--wifi-per-zone 400] [--seed 7]

Writes (all synthetic, see simulation/campus.py and simulation/generator.py):
    ml/data/wifi_fingerprints.csv   labelled Wi-Fi fingerprints (22 zones x N)
    ml/data/anomaly_features.csv    one labelled feature row per evidence-bearing (student, session)

The anomaly rows come from running the simulator through the SAME pipeline used at run time,
so training features match serving features.
"""
from __future__ import annotations

import argparse
import time
from collections import Counter

import _common  # noqa: F401
from _common import DATA, MODELS

from app import mltrain
from app.bootstrap import build_services
from app.config import Settings
from app.wifi import WifiLocalizer


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--students", type=int, default=500, help="simulated students for the anomaly dataset")
    ap.add_argument("--wifi-per-zone", type=int, default=400)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    t = time.time()
    fps, ys = mltrain.generate_wifi_dataset(args.wifi_per_zone, args.seed)
    mltrain.write_wifi_csv(DATA / "wifi_fingerprints.csv", fps, ys)
    print(f"wrote {len(ys)} Wi-Fi fingerprints ({len(set(ys))} zones) -> ml/data/wifi_fingerprints.csv")

    settings = Settings(database_url="sqlite://", model_dir=str(MODELS), data_dir=str(DATA),
                        jwt_secret="dataset", pepper="dataset")
    svc = build_services(settings)
    wifi_path = MODELS / "wifi_localization.joblib"
    if wifi_path.exists():
        svc.wifi_sim = WifiLocalizer.load(wifi_path)
        print("using existing", wifi_path.name, "to produce Wi-Fi zone estimates")
    else:
        print("no wifi_localization.joblib yet: training a temporary in-memory one for dataset generation")
        svc.wifi_sim, _ = mltrain.train_wifi(args.wifi_per_zone, 100, args.seed, fps_ys=(fps, ys))
    rows = mltrain.build_anomaly_dataset(svc, args.students, args.seed, mltrain.default_scenarios(args.students))
    mltrain.write_feature_csv(DATA / "anomaly_features.csv", rows)
    print(f"wrote {len(rows)} anomaly-feature rows -> ml/data/anomaly_features.csv  ({time.time() - t:.0f}s)")
    for label, n in sorted(Counter(r["label"] for r in rows).items(), key=lambda kv: -kv[1]):
        print(f"  {label:22s}{n:6d}")
    print("All of this data is SYNTHETIC.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

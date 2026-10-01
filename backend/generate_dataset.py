"""Generate the Proof-of-Presence synthetic campus dataset (brief section
26-28) and print a summary of what it contains.

    python generate_dataset.py

train_wifi_model.py / train_anomaly_model.py each call
simulator.campus.generate_campus_dataset() directly (it's fast and fully
deterministic given a seed), so running this script first is not required
-- it exists as a standalone way to inspect what the generator produces
before trusting it to train anything.
"""
from __future__ import annotations

import argparse
from collections import Counter

from simulator.campus import generate_campus_dataset


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--students", type=int, default=300)
    ap.add_argument("--classrooms", type=int, default=20)
    ap.add_argument("--aps", type=int, default=10)
    ap.add_argument("--anomaly-rate", type=float, default=0.08)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    data = generate_campus_dataset(args.students, args.classrooms, args.aps, args.anomaly_rate, args.seed)
    buildings = sorted({c.building for c in data["classrooms"]})
    floors = sorted({c.floor for c in data["classrooms"]})
    print(f"{len(data['students'])} students")
    print(f"{len(data['classrooms'])} classrooms across {buildings} (floors {floors})")
    print(f"{len(data['aps'])} Wi-Fi APs, {len(data['markers'])} BLE classroom markers")
    print(f"{len(data['sessions'])} sessions")

    labels = Counter(e.label for e in data["events"])
    print(f"\n{len(data['events'])} attendance events:")
    for label, n in labels.most_common():
        print(f"  {label:22s} {n:4d}  ({100 * n / len(data['events']):5.1f}%)")

    normal = [e for e in data["events"] if e.label == "normal"]
    if normal:
        avg_dwell = sum(e.leave_ts - e.enter_ts for e in normal) / len(normal)
        avg_peers = sum(len(e.nearby_peer_ids) for e in normal) / len(normal)
        print(f"\nnormal events: avg dwell {avg_dwell / 60:.1f} min, avg nearby peers {avg_peers:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

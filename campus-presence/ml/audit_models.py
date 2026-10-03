#!/usr/bin/env python
"""Audit the models honestly: how stable are the numbers, where do they fail, and how much do the rules cover?

    python ml/generate_dataset.py          # once: the synthetic feature data the forest audit reads
    python ml/audit_models.py [--seeds 8] [--json path]

Wi-Fi localiser: accuracy on a fresh synthetic set, whether `confidence` means what it says (a reliability table), and
how it degrades with extra RSSI noise and with access points that cannot be heard.

Isolation Forest: precision / recall / AUC across several seeds (each a different train/test split), and for every
injected scenario the share caught by the forest alone, by the deterministic rules alone, and by either. A forest that
looks good on one split can miss a scenario entirely on another; the spread is the honest number.

Everything here is SYNTHETIC. It shows how the models behave on the simulator's assumptions, not on a real campus.
"""
from __future__ import annotations

import argparse
import json

import _common  # noqa: F401
import numpy as np
from _common import DATA

from app import mltrain
from simulation import campus


def wifi_audit() -> dict:
    model, rep = mltrain.train_wifi(400, 150, seed=0)
    fps, ys = mltrain.generate_wifi_dataset(150, 777)                    # a seed neither training nor testing used
    preds = model.predict_many(fps)
    conf = np.array([p.confidence for p in preds])
    ok = np.array([p.zone == y for p, y in zip(preds, ys)])
    reliability = []
    for lo, hi in ((0, .4), (.4, .6), (.6, .8), (.8, .95), (.95, 1.01)):
        m = (conf >= lo) & (conf < hi)
        if m.sum():
            reliability.append({"confidence_from": lo, "confidence_to": min(hi, 1.0), "n": int(m.sum()),
                                "accuracy": round(float(ok[m].mean()), 3), "mean_confidence": round(float(conf[m].mean()), 3)})
    rng = np.random.default_rng(1)
    stress = {
        "RSSI noise +-4 dB extra": lambda fp: {a: v + rng.normal(0, 4) for a, v in fp.items()},
        "RSSI noise +-8 dB extra": lambda fp: {a: v + rng.normal(0, 8) for a, v in fp.items()},
        "3 of 10 access points not heard": lambda fp: {a: v for a, v in fp.items()
                                                       if a not in set(rng.choice(campus.AP_IDS, 3, replace=False))},
        "only the 4 strongest access points": lambda fp: dict(sorted(fp.items(), key=lambda kv: -kv[1])[:4]),
    }
    robustness = {k: round(float(np.mean([p.zone == y for p, y in zip(model.predict_many([fn(fp) for fp in fps]), ys)])), 3)
                  for k, fn in stress.items()}
    return {"selected_model": rep["selected_model"], "independent_test_accuracy": round(rep["accuracy"], 3),
            "zones": len(rep["labels"]), "errors_that_are_the_next_room": round(rep["errors_adjacent_fraction"], 3),
            "fresh_set_accuracy": round(float(ok.mean()), 3), "reliability": reliability, "robustness_accuracy": robustness}


def forest_audit(rows: list[dict], seeds: int) -> dict:
    per_seed, scen = [], {}
    normal_flag = {"forest": [], "rules": [], "either": []}
    weak = None
    for seed in range(seeds):
        model, rep = mltrain.train_anomaly(rows, seed=seed)
        weak = rep["weak_features"]
        split = mltrain.split_rows(rows, seed=seed)
        flags = [s.flagged for s in model.score(split["test"])]
        per_seed.append({"seed": seed, "precision": rep["precision"], "recall": rep["recall"], "auc": rep["roc_auc_raw_score"],
                         "token_reuse_in_training": any(r["label"] == "token_replay" for r in split["contaminants"])})
        for lab in sorted({r["label"] for r in split["test"]}):
            idx = [i for i, r in enumerate(split["test"]) if r["label"] == lab]
            f = [flags[i] for i in idx]
            ru = [bool(split["test"][i]["rule_hits"]) for i in idx]
            d = scen.setdefault(lab, {"forest": [], "rules": [], "either": []})
            d["forest"].append(float(np.mean(f)))
            d["rules"].append(float(np.mean(ru)))
            d["either"].append(float(np.mean([a or b for a, b in zip(f, ru)])))
            if lab == "normal":
                for k, v in (("forest", f), ("rules", ru), ("either", [a or b for a, b in zip(f, ru)])):
                    normal_flag[k].append(float(np.mean(v)))
    stat = lambda v: {"mean": round(float(np.mean(v)), 3), "std": round(float(np.std(v)), 3),
                      "min": round(float(np.min(v)), 3), "max": round(float(np.max(v)), 3)}
    return {"seeds": seeds,
            "precision": stat([s["precision"] for s in per_seed]), "recall": stat([s["recall"] for s in per_seed]),
            "auc": stat([s["auc"] for s in per_seed]),
            "per_scenario": {lab: {k: stat(v) for k, v in d.items()} for lab, d in scen.items()},
            "normal_sessions_flagged": {k: stat(v) for k, v in normal_flag.items()},
            "weak_features": weak,
            "weak_coverage": [lab for lab, d in scen.items() if lab not in ("normal", "false_positive") and min(d["either"]) < 0.9],
            "forest_never_saw_token_replay_in_training_for_seeds": [s["seed"] for s in per_seed if not s["token_reuse_in_training"]]}


def print_wifi(w: dict) -> None:
    print(f"\n=== Wi-Fi localiser ({w['selected_model']}, {w['zones']} zones)")
    print(f"  accuracy {w['independent_test_accuracy']:.3f} on the independent test set, {w['fresh_set_accuracy']:.3f} on a fresh one; "
          f"{w['errors_that_are_the_next_room']:.0%} of the errors are the room next door")
    print("  does `confidence` mean what it says? (bucket -> how often it was right)")
    for r in w["reliability"]:
        print(f"    confidence {r['confidence_from']:.2f}-{r['confidence_to']:.2f}  n={r['n']:4d}  right {r['accuracy']:.1%}  (mean confidence {r['mean_confidence']:.1%})")
    print("  robustness (accuracy under degraded input):")
    for k, v in w["robustness_accuracy"].items():
        print(f"    {k:<38} {v:.1%}")


def print_forest(f: dict) -> None:
    print(f"\n=== Isolation Forest, {f['seeds']} seeds (mean +- std [min..max])")
    for k in ("precision", "recall", "auc"):
        s = f[k]
        print(f"  {k:<10} {s['mean']:.3f} +- {s['std']:.3f}  [{s['min']:.2f}..{s['max']:.2f}]")
    print("  share of each scenario caught, on held-out data (forest alone / rules alone / either):")
    for lab, d in f["per_scenario"].items():
        if lab == "normal":
            continue
        print(f"    {lab:22s} forest {d['forest']['mean']:.2f} (min {d['forest']['min']:.2f})   rules {d['rules']['mean']:.2f}   either {d['either']['mean']:.2f} (min {d['either']['min']:.2f})")
    n = f["normal_sessions_flagged"]
    print(f"  normal sessions flagged: forest {n['forest']['mean']:.1%}, rules {n['rules']['mean']:.1%}, either {n['either']['mean']:.1%}")
    if f["weak_features"]:
        print("  features the forest is nearly blind to (almost never differ from the median in training):")
        for w in f["weak_features"]:
            print(f"    {w['feature']:<24} differs in {w['fraction_of_training_rows_that_differ']:.1%} of training rows")
    if f["forest_never_saw_token_replay_in_training_for_seeds"]:
        print("  seeds where no token replay was in the forest's training contamination: "
              f"{f['forest_never_saw_token_replay_in_training_for_seeds']} (see the forest-alone minimum above)")
    weak_cov = [(lab, d) for lab, d in f["per_scenario"].items() if lab not in ("normal", "false_positive") and d["either"]["min"] < 0.9]
    if weak_cov:
        print("  WEAK COVERAGE (caught by the forest or a rule in under 90% of cases on at least one split):")
        for lab, d in weak_cov:
            print(f"    {lab:22s} either: mean {d['either']['mean']:.2f}, worst split {d['either']['min']:.2f}"
                  f"   (rules alone {d['rules']['mean']:.2f}: " + ("no rule covers it" if d["rules"]["mean"] < 0.2 else "partly covered") + ")")
    print("  Read this with the caveat: all synthetic. The rules, not the forest, are what cover the weak features.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--json", help="also write the full results to this file")
    args = ap.parse_args()
    path = DATA / "anomaly_features.csv"
    if not path.exists():
        print(f"{path} not found: run `python ml/generate_dataset.py` first")
        return 1
    result = {"wifi": wifi_audit(), "forest": forest_audit(mltrain.read_feature_csv(path), args.seeds)}
    print_wifi(result["wifi"])
    print_forest(result["forest"])
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(result, fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

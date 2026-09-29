"""Train per-student owner models from calibration recordings.

    python -m app.train recordings/ --model-dir models

Recordings are CSVs named `<carrier>__<device>.csv` (see owner_model.py). One
model is trained per carrier who appears in the folder, using everyone else's
walking as the "other" class. Prints a hold-out score per student before saving
the final model, which is fitted on all the data. The score is
leave-one-recording-out when possible, so it measures generalisation to a new
session; a time-blocked fallback is labelled optimistic.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .owner_model import (
    STUDENT_ID_RE, ModelRegistry, dataset_for, evaluate, load_recordings, train_owner_model,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recordings", type=Path)
    ap.add_argument("--model-dir", type=Path, default=Path("models"))
    ap.add_argument("--students", help="comma-separated carriers to train (default: all)")
    args = ap.parse_args(argv)

    recordings = load_recordings(args.recordings)
    carriers = sorted({r.carrier for r in recordings})
    wanted = args.students.split(",") if args.students else carriers
    registry = ModelRegistry(args.model_dir)
    failed = False
    for student in wanted:
        if student not in carriers or not STUDENT_ID_RE.match(student):
            print(f"{student}: no recordings carried by this student", file=sys.stderr)
            failed = True
            continue
        try:
            report = evaluate(student, recordings)
            own, other = dataset_for(student, recordings)
            registry.put(student, train_owner_model(own, other))
        except ValueError as exc:
            print(f"{student}: {exc}", file=sys.stderr)
            failed = True
            continue
        print(
            f"{student}: balanced accuracy {report['balanced_accuracy']:.3f} [{report['method']}] "
            f"(owner recall {report['owner_recall']:.3f}, other rejection {report['other_rejection']:.3f}; "
            f"{report['test_owner_windows']}+{report['test_other_windows']} test windows) "
            f"-> {registry.path(student)}"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

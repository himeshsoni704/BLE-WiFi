import numpy as np
import pytest

from simulator.campus import (
    build_campus, build_sessions, build_students, classroom_fingerprint,
    generate_campus_dataset, rssi_between,
)


def test_build_campus_counts_match_request():
    classrooms, aps, markers = build_campus(n_classrooms=20, n_aps=10, floors=2)
    assert len(classrooms) == 20
    assert len(aps) == 10
    assert len(markers) == 20
    assert {m.classroom_id for m in markers} == {c.classroom_id for c in classrooms}
    assert len(set(c.classroom_id for c in classrooms)) == 20   # unique ids


def test_classrooms_span_requested_floors():
    classrooms, _, _ = build_campus(n_classrooms=20, n_aps=10, floors=2)
    assert {c.floor for c in classrooms} == {1, 2}


def test_rssi_decreases_with_distance():
    near = rssi_between(distance_m=2, walls_crossed=0)
    far = rssi_between(distance_m=40, walls_crossed=0)
    assert near > far


def test_rssi_decreases_with_walls():
    no_wall = rssi_between(distance_m=10, walls_crossed=0)
    one_wall = rssi_between(distance_m=10, walls_crossed=1)
    assert no_wall > one_wall


def test_classroom_fingerprint_is_nonempty_for_nearby_aps():
    classrooms, aps, _ = build_campus(20, 10, floors=2)
    rng = np.random.default_rng(0)
    fp = classroom_fingerprint(classrooms[0], aps, rng, drop_prob=0.0)
    assert len(fp) > 0
    assert all(isinstance(v, float) for v in fp.values())


def test_build_students_unique_ids():
    rng = np.random.default_rng(1)
    students = build_students(300, rng)
    assert len(students) == 300
    assert len(set(s.student_id for s in students)) == 300


def test_build_sessions_within_classroom_set():
    classrooms, _, _ = build_campus(20, 10)
    rng = np.random.default_rng(2)
    sessions = build_sessions(classrooms, 1_800_000_000.0, rng)
    ids = {c.classroom_id for c in classrooms}
    assert all(s.classroom_id in ids for s in sessions)
    assert all(s.end_ts > s.start_ts for s in sessions)


def test_generate_campus_dataset_shapes():
    data = generate_campus_dataset(n_students=50, n_classrooms=10, n_aps=6, seed=3)
    assert len(data["students"]) == 50
    assert len(data["classrooms"]) == 10
    assert len(data["aps"]) == 6
    assert len(data["events"]) == 50
    assert all(e.student_id in {s.student_id for s in data["students"]} for e in data["events"])


def test_generate_campus_dataset_has_a_mix_of_labels():
    data = generate_campus_dataset(n_students=300, n_classrooms=20, n_aps=10, anomaly_rate=0.15, seed=4)
    labels = {e.label for e in data["events"]}
    assert "normal" in labels
    assert len(labels) > 1   # at least one anomaly kind actually got injected
    anomalous = sum(1 for e in data["events"] if e.label != "normal")
    assert 0 < anomalous < len(data["events"])


def test_short_presence_event_has_short_duration():
    data = generate_campus_dataset(n_students=300, n_classrooms=20, n_aps=10, anomaly_rate=0.3, seed=7)
    shorts = [e for e in data["events"] if e.label == "short_presence"]
    assert shorts, "expected at least one short_presence event at this seed/rate"
    assert all((e.leave_ts - e.enter_ts) < 10 for e in shorts)


def test_wifi_ble_mismatch_event_has_a_different_wifi_classroom():
    data = generate_campus_dataset(n_students=300, n_classrooms=20, n_aps=10, anomaly_rate=0.3, seed=11)
    mismatches = [e for e in data["events"] if e.label == "wifi_ble_mismatch"]
    assert mismatches
    assert all(e.extra_classroom_id is not None and e.extra_classroom_id != e.classroom_id for e in mismatches)


def test_reproducible_with_same_seed():
    a = generate_campus_dataset(n_students=40, n_classrooms=8, n_aps=4, seed=42)
    b = generate_campus_dataset(n_students=40, n_classrooms=8, n_aps=4, seed=42)
    assert [e.label for e in a["events"]] == [e.label for e in b["events"]]
    assert [e.classroom_id for e in a["events"]] == [e.classroom_id for e in b["events"]]

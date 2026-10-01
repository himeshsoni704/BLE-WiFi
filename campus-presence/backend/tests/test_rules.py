import math

from app.config import Thresholds
from app.rules import RuleContext, evaluate_rules, max_rooms_in_window

T = Thresholds()


def dist(a, b):
    xy = {"101": (5, 10), "204": (35, 50), "205": (45, 50), "210": (95, 50)}
    return math.hypot(xy[a][0] - xy[b][0], xy[a][1] - xy[b][1])


def ctx(**kw):
    base = dict(own_rooms={"204"}, remote_observers=[], locations=[], marker_timeline=[], zone_distance=dist)
    base.update(kw)
    return RuleContext(**base)


def ev(**kw):
    e = {"classroom": "204",
         "classroom_ble": {"detected": True}, "wifi": {"available": True, "scans_used": 5, "match_fraction": 1.0,
                                                       "predicted_zone": "204", "confidence": 0.9}}
    for k, v in kw.items():
        e[k].update(v)
    return e


def names(hits):
    return {h.rule for h in hits}


def test_clean_evidence_has_no_hits():
    assert evaluate_rules(ev(), ctx(), T) == []


def test_token_reuse_and_abnormal():
    one = evaluate_rules(ev(), ctx(remote_observers=[("a", "205")]), T)
    assert names(one) == {"token_reuse"} and one[0].severity == "warn"
    three = evaluate_rules(ev(), ctx(remote_observers=[("a", "205"), ("b", "205"), ("c", "210")]), T)
    assert names(three) == {"token_reuse", "abnormal_token_reuse"}
    assert three[0].data["token_reuse_count"] == 3 and three[0].severity == "high"


def test_impossible_movement_uses_speed_not_just_distance():
    fast = [(0, "101", 5, 10), (10, "210", 95, 50)]                  # ~98 m in 10 s
    slow = [(0, "101", 5, 10), (3000, "210", 95, 50)]
    hit = evaluate_rules(ev(), ctx(locations=fast), T)
    assert names(hit) == {"impossible_movement"} and hit[0].data["speed_mps"] > 9
    assert evaluate_rules(ev(), ctx(locations=slow), T) == []
    assert evaluate_rules(ev(), ctx(locations=[(0, "204", 0, 0), (1, "204", 0, 0)]), T) == []   # same zone


def test_ble_wifi_contradiction():
    bad = ev(wifi={"match_fraction": 0.1, "predicted_zone": "205", "confidence": 0.38})
    hit = evaluate_rules(bad, ctx(), T)
    assert names(hit) == {"ble_wifi_contradiction"}
    assert hit[0].data["ble_room"] == "204" and hit[0].data["wifi_room"] == "205"
    # needs both signals and at least 2 scans
    assert evaluate_rules(ev(wifi={"match_fraction": 0.0, "scans_used": 1}), ctx(), T) == []
    assert evaluate_rules(ev(classroom_ble={"detected": False}, wifi={"match_fraction": 0.0}), ctx(), T) == []
    assert evaluate_rules(ev(wifi={"match_fraction": 0.5}), ctx(), T) == []                 # below the 0.6 mismatch bar


def test_rapid_session_switching():
    tl = [(0, "101"), (100, "204"), (200, "205")]
    assert names(evaluate_rules(ev(), ctx(marker_timeline=tl), T)) == {"rapid_session_switching"}
    assert evaluate_rules(ev(), ctx(marker_timeline=[(0, "101"), (100, "204")]), T) == []
    spread = [(0, "101"), (1000, "204"), (2000, "205")]                     # not within 5 minutes
    assert evaluate_rules(ev(), ctx(marker_timeline=spread), T) == []
    assert max_rooms_in_window(tl, 300)[0] == 3


def test_every_hit_explains_itself():
    hits = evaluate_rules(ev(wifi={"match_fraction": 0.0, "predicted_zone": "205", "confidence": 0.4}),
                          ctx(remote_observers=[("a", "205")] * 1, locations=[(0, "101", 0, 0), (5, "210", 0, 0)]), T)
    assert all(h.detail and h.severity in ("info", "warn", "high") for h in hits)

"""The text knowledge base (app/knowledge.py + app/knowledge/*.md) the explainer retrieves from and cites."""
import re

import pytest

from app import xai
from app.anomaly import FEATURES
from app.knowledge import KB_DIR, KnowledgeBase, load_passages, parse_passages
from app.llm.mock import FEATURE_CAUSES, RULE_CAUSES
from app.rag import tags_from_snapshot
from app.rules import RULES


@pytest.fixture(scope="module")
def kb():
    return KnowledgeBase()


def test_the_shipped_knowledge_base_loads_and_every_passage_is_small_and_tagged():
    ps = load_passages()
    assert len(ps) >= 12
    assert len({p.id for p in ps}) == len(ps)
    for p in ps:
        assert re.fullmatch(r"KB-[A-Z0-9-]+", p.id), p.id
        assert p.tags, f"{p.id} has no tags, so retrieval could never match it"
        assert 15 <= len(p.text.split()) <= 100, f"{p.id}: keep passages short ({len(p.text.split())} words)"


def test_parsing_handles_comments_always_flag_and_rejects_duplicate_ids():
    md = """<!-- ignored
## KB-NOPE | not a passage
-->
## KB-A | First
always: yes
tags: x y
Some text
over two lines.

## KB-B | Second
tags: z
Other text.
"""
    a, b = parse_passages(md)
    assert (a.id, a.title, a.tags, a.always, a.text) == ("KB-A", "First", ["x", "y"], True, "Some text over two lines.")
    assert (b.id, b.always) == ("KB-B", False)
    with pytest.raises(ValueError, match="duplicate"):
        parse_passages("## KB-A | one\ntags: a\ntext\n## KB-A | two\ntags: b\ntext\n")


def test_the_policy_passage_is_always_returned_and_unrelated_tags_return_only_that(kb):
    assert [r["id"] for r in kb.retrieve(["zzz_nothing_matches"])] == ["KB-POLICY"]
    assert "KB-POLICY" in [r["id"] for r in kb.retrieve(["rule_token_reuse"])]


@pytest.mark.parametrize("tags, expected_first", [
    (["rule_token_reuse", "token_reuse_multi_device", "feat_token_reuse_count"], "KB-RULE-TOKEN"),
    (["rule_ble_wifi_contradiction", "wifi_conf_low", "room_adjacent"], "KB-RULE-BLEWIFI"),
    (["rule_impossible_movement", "speed_extreme", "feat_estimated_speed"], "KB-RULE-MOVE"),
    (["rule_rapid_session_switching"], "KB-RULE-SWITCH"),
    (["short_presence", "feat_ble_duration"], "KB-CAUSE-EARLY"),
    (["marker_missing", "peers_consistent"], "KB-CAUSE-MARKER"),
    (["device_cluster", "peer_rssi_very_strong", "feat_rssi_twin_distance"], "KB-CAUSE-TWINS"),
])
def test_retrieval_puts_the_relevant_passage_first(kb, tags, expected_first):
    hits = [r for r in kb.retrieve(tags, k=4) if r["id"] != "KB-POLICY"]
    assert hits[0]["id"] == expected_first, [(h["id"], h["score"]) for h in hits]
    assert hits[0]["matched_tags"]                                      # it says why it matched


def test_retrieval_is_deterministic_and_respects_k(kb):
    tags = ["rule_ble_wifi_contradiction", "wifi_conf_low"]
    assert kb.retrieve(tags, k=2) == kb.retrieve(tags, k=2)
    assert len([r for r in kb.retrieve(tags, k=1) if r["id"] != "KB-POLICY"]) == 1


def test_every_rule_the_system_can_raise_is_covered_by_a_passage(kb):
    covered = {t for p in kb.passages for t in p.tags}
    for rule in RULES:
        assert f"rule_{rule.__name__}" in covered, f"no knowledge passage is tagged rule_{rule.__name__}"


def test_every_feature_the_forest_uses_is_covered_by_a_passage(kb):
    covered = {t for p in kb.passages for t in p.tags}
    missing = [f for f in FEATURES if f"feat_{f}" not in covered]
    assert not missing, f"features with no passage that explains them: {missing}"
    assert set(xai.FEATURE_INFO) == set(FEATURES)


def test_no_explanation_template_cites_a_passage_that_does_not_exist(kb):
    """The mock explainer's candidate causes name knowledge ids. A rename in the markdown must not leave one dangling,
    because a dangling citation would be rejected by the validator and the explanation would silently get worse."""
    ids = {p.id for p in kb.passages}
    cited = {i for causes in RULE_CAUSES.values() for c in causes for i in c[2]}
    cited |= {i for c in FEATURE_CAUSES.values() for i in c[2]}
    assert cited <= ids, sorted(cited - ids)


def test_tags_the_rag_computes_for_real_rule_hits_match_passages(kb):
    snap = {"rules": [{"rule": "token_reuse", "data": {"token_reuse_count": 3}},
                      {"rule": "ble_wifi_contradiction", "data": {"ble_room": "204", "wifi_room": "205"}}],
            "wifi": {"confidence": 0.3}, "classroom_ble": {}, "peers": {}}
    tags = tags_from_snapshot(snap, lambda a, b: 10.0)
    ids = [r["id"] for r in kb.retrieve(tags, k=4)]
    assert "KB-RULE-TOKEN" in ids and "KB-RULE-BLEWIFI" in ids


def test_the_markdown_files_live_next_to_the_module():
    assert KB_DIR.is_dir() and list(KB_DIR.glob("*.md"))

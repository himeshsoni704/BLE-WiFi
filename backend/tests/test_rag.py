from app.rag import CaseRetriever, VerifiedCase, anomaly_query_text

CASE_WIFI = VerifiedCase(1, "wifi_localization_error", "Wi-Fi predicted the wrong room because AP-03 "
                          "temporarily had an unusually weak signal in Room 204.",
                          "Faculty verified the student was actually in Room 204.")
CASE_BLE = VerifiedCase(2, "ble_marker_unavailable", "BLE marker was temporarily unavailable in Room 305.",
                        "Wi-Fi and peer BLE evidence together confirmed presence.")
CASE_TOKEN = VerifiedCase(3, "token_replay", "The same temporary token pattern was observed from multiple "
                          "devices within a short interval.",
                          "Investigation confirmed a genuine proxy-attendance attempt.")


def test_empty_index_returns_nothing():
    r = CaseRetriever()
    assert r.retrieve("anything") == []


def test_retrieves_the_most_textually_similar_case():
    r = CaseRetriever()
    r.index([CASE_WIFI, CASE_BLE, CASE_TOKEN])
    results = r.retrieve("Wi-Fi fingerprint predicted the wrong room, AP signal was weak", k=1)
    assert len(results) == 1
    assert results[0].case.case_id == 1


def test_k_limits_results():
    r = CaseRetriever()
    r.index([CASE_WIFI, CASE_BLE, CASE_TOKEN])
    assert len(r.retrieve("Wi-Fi token BLE room", k=2)) <= 2


def test_min_similarity_filters_irrelevant_cases():
    r = CaseRetriever()
    r.index([CASE_WIFI])
    results = r.retrieve("completely unrelated query about nothing in particular here", min_similarity=0.9)
    assert results == []


def test_add_case_is_immediately_retrievable():
    r = CaseRetriever()
    r.index([CASE_WIFI])
    r.add_case(CASE_TOKEN)
    results = r.retrieve("token replay multiple devices", k=1)
    assert results[0].case.case_id == 3


def test_retrieved_case_to_dict_shape():
    r = CaseRetriever()
    r.index([CASE_WIFI])
    rc = r.retrieve("Wi-Fi AP weak signal", k=1)[0]
    d = rc.to_dict()
    assert set(d.keys()) == {"case_id", "case_type", "issue", "resolution", "similarity"}


def test_anomaly_query_text_mentions_room_mismatch():
    text = anomaly_query_text({"ble_room": "204", "wifi_room": "205", "wifi_confidence": 0.38}, [])
    assert "204" in text and "205" in text


def test_anomaly_query_text_mentions_token_reuse():
    text = anomaly_query_text({"token_reuse_count": 3}, [])
    assert "multiple devices" in text


def test_anomaly_query_text_falls_back_when_nothing_notable():
    assert anomaly_query_text({}, []) == "unexplained anomaly"


def test_anomaly_query_text_includes_explicit_reasons():
    text = anomaly_query_text({}, ["impossible movement detected"])
    assert "impossible movement detected" in text

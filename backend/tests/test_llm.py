import asyncio
import logging

import pytest

from app.llm import GeminiLLMProvider, MockLLMProvider, get_llm_provider
from app.rag import RetrievedCase, VerifiedCase

EVIDENCE = {
    "student": "STU102", "expected_room": "204", "ble_room": "204", "wifi_room": "205",
    "wifi_confidence": 0.38, "token_reuse_count": 3, "movement_speed": 18.2,
    "isolation_score": -0.71, "reasons": ["BLE and Wi-Fi disagree about the room"],
}
CASE = VerifiedCase(17, "wifi_localization_error", "Wi-Fi AP-03 was temporarily weak in Room 204.",
                    "Faculty verified the student was physically present.")


def run(coro):
    return asyncio.run(coro)


def test_mock_explains_without_inventing_fields():
    text = run(MockLLMProvider().explain(EVIDENCE, []))
    assert "204" in text and "205" in text
    assert "3" in text   # token_reuse_count
    assert "18.2" in text   # movement_speed


def test_mock_mentions_retrieved_case():
    text = run(MockLLMProvider().explain(EVIDENCE, [RetrievedCase(CASE, 0.82)]))
    assert "17" in text
    assert "verified case" in text.lower()


def test_mock_handles_no_reasons_or_cases_gracefully():
    text = run(MockLLMProvider().explain({}, []))
    assert isinstance(text, str) and len(text) > 0


def test_mock_never_claims_a_verdict():
    text = run(MockLLMProvider().explain(EVIDENCE, []))
    assert "cheated" not in text.lower()
    assert "fraud" not in text.lower()


def test_get_llm_provider_defaults_to_mock(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert isinstance(get_llm_provider(), MockLLMProvider)


def test_get_llm_provider_explicit_mock(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    assert isinstance(get_llm_provider(), MockLLMProvider)


def test_get_llm_provider_falls_back_without_api_key(monkeypatch, caplog):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with caplog.at_level(logging.WARNING):
        provider = get_llm_provider()
    assert isinstance(provider, MockLLMProvider)
    assert "GEMINI_API_KEY" in caplog.text


def test_get_llm_provider_falls_back_when_sdk_missing(monkeypatch, caplog):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    with caplog.at_level(logging.WARNING):
        provider = get_llm_provider()
    # google-generativeai is not a project dependency, so this exercises the
    # real "SDK not installed" fallback path, not a mocked one.
    assert isinstance(provider, MockLLMProvider)


def test_get_llm_provider_unknown_value_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("LLM_PROVIDER", "not-a-real-provider")
    with caplog.at_level(logging.WARNING):
        provider = get_llm_provider()
    assert isinstance(provider, MockLLMProvider)


def test_gemini_provider_raises_cleanly_without_sdk_installed():
    with pytest.raises(RuntimeError, match="google-generativeai"):
        GeminiLLMProvider(api_key="fake-key-for-test")

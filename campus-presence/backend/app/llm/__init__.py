"""LLM provider selection and the explain-with-fallback wrapper."""
from __future__ import annotations

import logging

from .base import Explanation, LLMProvider, LLMUnavailable, evidence_view, grounding_check
from .gemini import GeminiLLMProvider
from .mock import MockLLMProvider

log = logging.getLogger("campus.llm")
__all__ = ["Explanation", "LLMProvider", "LLMUnavailable", "GeminiLLMProvider", "MockLLMProvider",
           "evidence_view", "grounding_check", "get_provider", "explain_with_fallback"]


def get_provider(settings, client=None) -> tuple[LLMProvider, str | None]:
    """Returns (provider, note). The app always gets a working provider: mock if Gemini is unavailable."""
    if settings.llm_provider == "gemini":
        try:
            return GeminiLLMProvider(settings.gemini_api_key or "", settings.gemini_model or "",
                                     settings.llm_timeout_s, client=client), None
        except LLMUnavailable as exc:
            log.warning("LLM_PROVIDER=gemini but unavailable (%s); using mock provider", exc)
            return MockLLMProvider(), f"Gemini not available ({exc}); using the offline mock explainer"
    if settings.llm_provider not in ("mock", ""):
        return MockLLMProvider(), f"unknown LLM_PROVIDER={settings.llm_provider!r}; using the offline mock explainer"
    return MockLLMProvider(), None


async def explain_with_fallback(primary: LLMProvider, evidence: dict, cases: list[dict]) -> Explanation:
    """Try `primary`; on failure or an ungrounded answer use the deterministic mock explainer."""
    mock = MockLLMProvider()
    if primary.name == "mock":
        return await mock.explain(evidence, cases)
    try:
        exp = await primary.explain(evidence, cases)
    except LLMUnavailable as exc:
        fb = await mock.explain(evidence, cases)
        fb.fallback_reason = str(exc)
        return fb
    if not exp.grounding.get("passed", False):
        fb = await mock.explain(evidence, cases)
        fb.fallback_reason = ("Gemini output mentioned values not present in the evidence "
                              f"({', '.join(exp.grounding['unverified_terms'])}); it was rejected")
        fb.rejected_text = exp.text
        return fb
    return exp

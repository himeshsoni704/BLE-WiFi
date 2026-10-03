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


async def explain_with_fallback(primary: LLMProvider, evidence: dict, cases: list[dict],
                                knowledge: list[dict] | None = None) -> Explanation:
    """Try `primary`; on failure, an ungrounded answer, or a failed citation check, use the deterministic explainer."""
    mock = MockLLMProvider()
    if primary.name == "mock":
        return await mock.explain(evidence, cases, knowledge)
    try:
        exp = await primary.explain(evidence, cases, knowledge)
    except LLMUnavailable as exc:
        fb = await mock.explain(evidence, cases, knowledge)
        fb.fallback_reason = str(exc)
        return fb
    reasons = []
    if not exp.grounding.get("passed", False):
        reasons.append("Gemini output mentioned values not present in the evidence "
                       f"({', '.join(exp.grounding['unverified_terms'])})")
    if not exp.validation.get("passed", True):
        reasons.append("its reasoning failed the citation checks (" + "; ".join(exp.validation["problems"][:3]) + ")")
    if reasons:
        fb = await mock.explain(evidence, cases, knowledge)
        fb.fallback_reason = " and ".join(reasons) + "; it was rejected"
        fb.rejected_text = exp.text
        return fb
    return exp

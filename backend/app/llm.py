"""LLM reasoning/explanation layer (brief sections 18-19), kept strictly
AFTER and SEPARATE from the attendance/anomaly decision:

    raw observations -> evidence fusion -> deterministic rules ->
    Isolation Forest -> ANOMALY -> structured evidence -> RAG retrieval ->
    LLM -> human-readable explanation

The LLM never decides presence or absence, and never sees anything beyond
the structured evidence dict and the cases RAG actually retrieved -- it is
explicitly instructed, in every prompt, to explain only what it's given
and never invent observations, locations, scores, or reasons (brief's own
requirement, verbatim).

Two providers behind one interface, selected by the LLM_PROVIDER env var:

    LLM_PROVIDER=mock    (default) -- deterministic, template-based,
                         needs no network or API key, so the app always runs.
    LLM_PROVIDER=gemini  -- Google's Gemini API (GEMINI_API_KEY,
                         GEMINI_MODEL env vars). Lazily imports
                         google-generativeai only when actually selected,
                         so MockLLMProvider never needs it installed. If
                         Gemini is requested but unreachable (missing key,
                         SDK not installed, network/quota error at call
                         time), falls back to the mock explanation with a
                         note -- never crashes the request.
"""
from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod

from .rag import RetrievedCase

log = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"


class LLMProvider(ABC):
    @abstractmethod
    async def explain(self, evidence: dict, retrieved_cases: list[RetrievedCase]) -> str:
        """evidence: the structured anomaly dict (brief section 18's own
        shape -- student, expected_room, ble_room, wifi_room,
        wifi_confidence, token_reuse_count, movement_speed,
        isolation_score, reasons, ...). Returns a short, human-readable
        explanation grounded ONLY in that dict and retrieved_cases."""


def _format_evidence(evidence: dict) -> str:
    lines = [f"- {k}: {v}" for k, v in evidence.items() if v is not None]
    return "\n".join(lines) if lines else "(no fields provided)"


def _format_cases(retrieved_cases: list[RetrievedCase]) -> str:
    if not retrieved_cases:
        return "(no similar verified cases found)"
    return "\n".join(
        f"- Case #{rc.case.case_id} [{rc.case.case_type}, similarity {rc.similarity:.2f}]: "
        f"{rc.case.issue} -> resolved as: {rc.case.resolution}"
        for rc in retrieved_cases
    )


class MockLLMProvider(LLMProvider):
    """Deterministic, template-based explanation built only from the
    structured evidence and retrieved cases actually passed in. This is
    NOT a lesser stand-in only for when Gemini is unavailable -- it's also
    exactly what the brief means by "the LLM must not invent evidence":
    every sentence here traces to a specific field in `evidence` or a
    specific retrieved case, which is the same discipline GeminiLLMProvider's
    prompt instructs the real model to follow."""

    async def explain(self, evidence: dict, retrieved_cases: list[RetrievedCase]) -> str:
        reasons = evidence.get("reasons") or []
        sentences: list[str] = []

        if reasons:
            sentences.append("This record was flagged because " + "; and ".join(reasons) + ".")
        else:
            sentences.append("This record was flagged by the anomaly model without a specific rule trigger.")

        ble_room, wifi_room = evidence.get("ble_room"), evidence.get("wifi_room")
        if ble_room and wifi_room and ble_room != wifi_room:
            conf = evidence.get("wifi_confidence")
            conf_s = f" (Wi-Fi confidence {conf:.0%})" if isinstance(conf, (int, float)) else ""
            sentences.append(f"BLE evidence indicated room {ble_room}, while the Wi-Fi fingerprint "
                             f"indicated room {wifi_room}{conf_s}.")

        if evidence.get("token_reuse_count"):
            sentences.append(f"The same temporary token pattern was observed from "
                             f"{evidence['token_reuse_count']} different devices in a short interval.")

        speed = evidence.get("movement_speed")
        if isinstance(speed, (int, float)) and speed > 2.5:
            sentences.append(f"The estimated movement speed ({speed:.1f} m/s) exceeds what's physically "
                             f"plausible indoors.")

        iso = evidence.get("isolation_score")
        if isinstance(iso, (int, float)):
            sentences.append(f"The Isolation Forest model scored this combination of signals as anomalous "
                             f"(raw score {iso:.2f}, not a probability).")

        if retrieved_cases:
            top = retrieved_cases[0]
            sentences.append(f"This resembles a previously verified case (#{top.case.case_id}, "
                             f"{top.case.case_type}): {top.case.resolution}")

        sentences.append("This is an explanation of the evidence already collected, not an independent "
                         "determination of misconduct -- a human reviewer makes the final call.")
        return " ".join(sentences)


class GeminiLLMProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = DEFAULT_GEMINI_MODEL):
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise RuntimeError(
                "LLM_PROVIDER=gemini requires the google-generativeai package "
                "(pip install google-generativeai)"
            ) from exc
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model)
        self._model_name = model
        self._fallback = MockLLMProvider()

    def _build_prompt(self, evidence: dict, retrieved_cases: list[RetrievedCase]) -> str:
        return (
            "You are explaining why an automated campus attendance system flagged a record for "
            "human review. You do NOT decide whether the student was present, absent, or cheated -- "
            "that decision has already been made by deterministic rules and an Isolation Forest model, "
            "upstream of you. Your ONLY job is to explain, in 2-4 plain-language sentences, what the "
            "evidence below shows.\n\n"
            "STRICT RULES:\n"
            "- Only reference facts that appear in the Evidence or Similar Verified Cases sections below.\n"
            "- Never invent an observation, a location, a score, a device, or a reason that isn't listed.\n"
            "- Never state or imply a final verdict (e.g. 'the student cheated' or 'this is fraud') -- "
            "say the record should be reviewed, and by whom the pattern was observed.\n"
            "- If a Similar Verified Case is relevant, you may mention it as a precedent worth comparing "
            "against, not as proof.\n\n"
            f"Evidence:\n{_format_evidence(evidence)}\n\n"
            f"Similar verified cases:\n{_format_cases(retrieved_cases)}\n\n"
            "Write the explanation now:"
        )

    async def explain(self, evidence: dict, retrieved_cases: list[RetrievedCase]) -> str:
        prompt = self._build_prompt(evidence, retrieved_cases)
        try:
            response = await asyncio.to_thread(self._model.generate_content, prompt)
            text = (response.text or "").strip()
            if not text:
                raise ValueError("empty response from Gemini")
            return text
        except Exception as exc:   # network, quota, bad key, SDK error, ... -- the app must still respond
            log.warning("Gemini explain() failed (%s: %s); falling back to the mock explanation",
                       type(exc).__name__, exc)
            fallback = await self._fallback.explain(evidence, retrieved_cases)
            return f"[Gemini unavailable, showing a template explanation instead] {fallback}"


def get_llm_provider() -> LLMProvider:
    """Reads LLM_PROVIDER (default "mock"). "gemini" without a usable
    GEMINI_API_KEY or SDK falls back to MockLLMProvider at startup (logged,
    not raised) -- the brief's explicit requirement that the whole app
    keeps running even when Gemini can't be reached."""
    provider = os.environ.get("LLM_PROVIDER", "mock").strip().lower()
    if provider == "mock":
        return MockLLMProvider()
    if provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            log.warning("LLM_PROVIDER=gemini but GEMINI_API_KEY is not set; falling back to MockLLMProvider")
            return MockLLMProvider()
        model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        try:
            return GeminiLLMProvider(api_key, model)
        except RuntimeError as exc:
            log.warning("%s; falling back to MockLLMProvider", exc)
            return MockLLMProvider()
    log.warning("unknown LLM_PROVIDER=%r; falling back to MockLLMProvider", provider)
    return MockLLMProvider()

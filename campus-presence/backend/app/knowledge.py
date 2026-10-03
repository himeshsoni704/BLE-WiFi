"""A small text knowledge base for the explainer: short passages about what each rule and feature means, common benign
causes, and policy. Retrieval is deterministic (tag overlap plus TF-IDF cosine); nothing is sent anywhere to build it.

It is the explainer's only source of general knowledge. The explainer must cite passages by id, and a citation is
checked against what was actually retrieved, so it cannot lean on facts from its own memory without that being visible.
Edit knowledge/system_kb.md (or drop more .md files next to it) to add campus-specific policy.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

KB_DIR = Path(__file__).parent / "knowledge"
_HEADING = re.compile(r"^##\s+([A-Z0-9][A-Z0-9_-]*)\s*\|\s*(.+?)\s*$")


@dataclass
class Passage:
    id: str
    title: str
    tags: list[str]
    text: str
    always: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def parse_passages(markdown: str) -> list[Passage]:
    markdown = re.sub(r"<!--.*?-->", "", markdown, flags=re.S)
    out: list[Passage] = []
    cur: dict | None = None
    for line in markdown.splitlines():
        m = _HEADING.match(line)
        if m:
            if cur:
                out.append(_finish(cur))
            cur = {"id": m.group(1), "title": m.group(2), "tags": [], "always": False, "body": []}
        elif cur is not None:
            low = line.strip().lower()
            if low.startswith("tags:"):
                cur["tags"] = line.split(":", 1)[1].split()
            elif low.startswith("always:"):
                cur["always"] = low.split(":", 1)[1].strip() in ("yes", "true", "1")
            elif line.strip():
                cur["body"].append(line.strip())
    if cur:
        out.append(_finish(cur))
    ids = [p.id for p in out]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate knowledge passage ids: {sorted({i for i in ids if ids.count(i) > 1})}")
    return out


def _finish(cur: dict) -> Passage:
    return Passage(cur["id"], cur["title"], cur["tags"], " ".join(cur["body"]), cur["always"])


def load_passages(directory: Path = KB_DIR) -> list[Passage]:
    passages: list[Passage] = []
    for f in sorted(directory.glob("*.md")):
        passages += parse_passages(f.read_text(encoding="utf-8"))
    ids = [p.id for p in passages]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate knowledge passage ids across files")
    return passages


class KnowledgeBase:
    def __init__(self, passages: list[Passage] | None = None):
        self.passages = passages if passages is not None else load_passages()
        self._by_id = {p.id: p for p in self.passages}
        docs = [self._doc(p) for p in self.passages]
        self._vec = TfidfVectorizer(token_pattern=r"[A-Za-z0-9_]+", lowercase=True, sublinear_tf=True) if docs else None
        self._matrix = self._vec.fit_transform(docs) if docs else None

    @staticmethod
    def _doc(p: Passage) -> str:
        return " ".join([*p.tags, *p.tags, p.title, p.text])         # tags twice: they are the strongest signal

    def size(self) -> int:
        return len(self.passages)

    def get(self, passage_id: str) -> Passage | None:
        return self._by_id.get(passage_id)

    def retrieve(self, tags: list[str], text: str = "", k: int = 4, min_score: float = 0.12) -> list[dict]:
        """Top-k passages for an anomaly, plus every `always` passage. Each result carries its score and why it matched."""
        if not self.passages:
            return []
        q = self._vec.transform([" ".join(tags) + " " + text])
        cos = (self._matrix @ q.T).toarray().ravel()
        tagset = set(tags)
        scored = []
        for i, p in enumerate(self.passages):
            overlap = sorted(tagset & set(p.tags))
            score = float(cos[i]) + 0.1 * min(3, len(overlap))
            scored.append((score, i, overlap))
        scored.sort(key=lambda t: (-t[0], t[1]))
        picked = [(s, i, o) for s, i, o in scored if not self.passages[i].always and s >= min_score][:k]
        picked += [(1.0, i, []) for i, p in enumerate(self.passages) if p.always]
        return [{**self.passages[i].to_dict(), "score": round(s, 3), "matched_tags": o} for s, i, o in picked]

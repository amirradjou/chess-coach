"""An optional plain-language summary of the digest, written by a model and checked against it.

The model only explains: it gets the counted patterns and their evidence, and every sentence it
writes that cites a move not in the evidence is dropped. No model is the default; a local Ollama
model costs nothing, and any OpenAI-compatible server (a free tier, or your own key) works too.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

from chess_coach.coach.digest import Digest, describe

PRESETS = {
    "ollama": ("http://localhost:11434/v1", "qwen2.5:7b", ""),
    "openrouter": ("https://openrouter.ai/api/v1", "", "OPENROUTER_API_KEY"),
}
# "23...Bxd1", "28. Qg6+", "6...a6", "12.O-O": a move number, then a piece, castling or a pawn.
MOVE = re.compile(r"\b\d{1,3}\.(?:\.\.)?\s?(?:[KQRBN]|O-O|[a-h])[^\s,;:)]*")

SYSTEM = (
    "You are a chess coach. Using only the facts given, write three or four short paragraphs to "
    "the player, in plain words: what their patterns say, which positions to look at again, and "
    "one habit to practise for each. Cite moves exactly as written in the facts (like "
    "23...Bxd1). Do not invent moves, numbers or games."
)


@dataclass(frozen=True, slots=True)
class Model:
    base_url: str
    model: str
    key_env: str = ""


def model_for(provider: str, model: str | None = None) -> Model:
    if provider not in PRESETS:
        raise ValueError(f"unknown provider {provider!r}; use one of {', '.join(PRESETS)}")
    base_url, default, key_env = PRESETS[provider]
    if not (model or default):
        raise ValueError(f"{provider} needs --model")
    return Model(base_url, model or default, key_env)


def facts(d: Digest, top: int = 5) -> str:
    lines = [f"Baseline: mistakes or blunders on {d.error_rate:.1%} of {d.moves} moves."]
    for p in d.move_patterns[:top]:
        moves = ", ".join(f"{e.move} (best {e.best_san})" for e in p.evidence)
        lines.append(
            f"- {describe(p.features)}: {p.rate:.0%} vs {p.baseline:.0%} over {p.n} moves. "
            f"Worst: {moves}"
        )
    for p in d.game_patterns[:top]:
        lines.append(f"- Games {describe(p.features).lower()}: lost {p.hits} of {p.n}.")
    for m in d.motifs[:top]:
        example = m.evidence[0] if m.evidence else None
        tail = f" e.g. {example.move} (best {example.best_san})" if example else ""
        lines.append(f"- Motif {m.tag}: {m.moves} moves in {m.games} games.{tail}")
    return "\n".join(lines)


def allowed_moves(d: Digest) -> set[str]:
    ev = [e for p in d.move_patterns for e in p.evidence] + [
        e for m in d.motifs for e in m.evidence
    ]
    out = set()
    for e in ev:
        out.add(_norm(e.move))
        sep = "." if e.color == "white" else "..."
        out.add(_norm(f"{e.move_number}{sep}{e.best_san}"))
    return out


def _norm(move: str) -> str:
    return move.replace(" ", "").rstrip(".!?+#")


def sentences(paragraph: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", paragraph.strip())


def cites_evidence(sentence: str, allowed: set[str]) -> bool:
    """Whether every move the sentence cites is one of the evidence moves."""
    return all(_norm(m) in allowed for m in MOVE.findall(sentence))


def check(text: str, allowed: set[str]) -> tuple[str, int]:
    """Drop every sentence that cites a move outside the evidence. Returns the text and the count
    of dropped sentences."""
    kept, dropped = [], 0
    for paragraph in text.split("\n\n"):
        found = sentences(paragraph)
        good = [s for s in found if cites_evidence(s, allowed)]
        dropped += len(found) - len(good)
        if good:
            kept.append(" ".join(good))
    return "\n\n".join(kept), dropped


def complete(m: Model, prompt: str, timeout: float = 600) -> str:
    headers = {}
    if m.key_env:
        key = os.environ.get(m.key_env)
        if not key:
            raise RuntimeError(f"set {m.key_env} to use {m.base_url}")
        headers["Authorization"] = f"Bearer {key}"
    r = httpx.post(
        f"{m.base_url}/chat/completions",
        headers=headers,
        json={
            "model": m.model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def narrate(d: Digest, m: Model) -> str:
    """The checked summary as a Markdown section ("" when the digest has nothing to explain)."""
    if not d.move_patterns and not d.motifs:
        return ""
    text, dropped = check(complete(m, facts(d)), allowed_moves(d))
    note = (
        f" {dropped} sentence(s) citing moves outside the evidence were removed." if dropped else ""
    )
    return f"## In plain words\n\n{text}\n\n_Written by {m.model} from the facts above.{note}_\n"

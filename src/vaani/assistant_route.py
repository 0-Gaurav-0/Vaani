"""Structured assistant routing decisions (AI router + clarify matching)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


INTENTS = frozenset({"play", "open", "qa", "codex", "skill", "paste", "clarify"})
TARGETS = frozenset(
    {"youtube", "prime", "netflix", "hotstar", "sonyliv", "app", "site", ""}
)
CONFIDENCE_ACT = 0.65


@dataclass(frozen=True)
class RouteOption:
    label: str
    intent: str
    query: str = ""
    target: str = ""


@dataclass(frozen=True)
class RouteDecision:
    intent: str
    query: str = ""
    target: str = ""
    confidence: float = 0.0
    options: tuple[RouteOption, ...] = field(default_factory=tuple)

    @property
    def should_clarify(self) -> bool:
        if self.intent == "clarify":
            return True
        if self.options and self.confidence < CONFIDENCE_ACT:
            return True
        return False


def _clip_str(value: object, *, limit: int = 240) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:limit]


def _parse_option(raw: object) -> RouteOption | None:
    if not isinstance(raw, dict):
        return None
    intent = _clip_str(raw.get("intent"), limit=32).casefold()
    if intent not in INTENTS or intent == "clarify":
        return None
    label = _clip_str(raw.get("label"), limit=120)
    query = _clip_str(raw.get("query"))
    target = _clip_str(raw.get("target"), limit=32).casefold()
    if target not in TARGETS:
        target = ""
    if not label:
        label = f"{intent} {query or target}".strip() or intent
    return RouteOption(label=label, intent=intent, query=query, target=target)


def parse_route_payload(text: str) -> RouteDecision | None:
    """Parse model JSON (raw or fenced) into a RouteDecision."""
    blob = (text or "").strip()
    if not blob:
        return None
    if blob.startswith("```"):
        blob = re.sub(r"^```(?:json)?\s*", "", blob, flags=re.I)
        blob = re.sub(r"\s*```$", "", blob)
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", blob, flags=re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    intent = _clip_str(data.get("intent"), limit=32).casefold()
    if intent not in INTENTS:
        return None
    query = _clip_str(data.get("query"))
    target = _clip_str(data.get("target"), limit=32).casefold()
    if target not in TARGETS:
        target = ""
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    options_raw = data.get("options")
    options: list[RouteOption] = []
    if isinstance(options_raw, list):
        for item in options_raw[:5]:
            opt = _parse_option(item)
            if opt is not None:
                options.append(opt)
    return RouteDecision(
        intent=intent,
        query=query,
        target=target,
        confidence=confidence,
        options=tuple(options),
    )


def format_clarify_body(options: tuple[RouteOption, ...] | list[RouteOption]) -> str:
    lines = []
    for i, opt in enumerate(options[:5], start=1):
        lines.append(f"{i}. {opt.label}")
    return "\n".join(lines)


def match_clarify_choice(
    spoken: str, options: tuple[RouteOption, ...] | list[RouteOption]
) -> RouteOption | None:
    """Match a follow-up utterance to a clarify option (number or label/target)."""
    normalized = " ".join((spoken or "").casefold().split())
    if not normalized or not options:
        return None
    # "1", "option 2", "number 3", "pehla", etc.
    num = re.search(r"\b(?:option|number|no\.?|#)?\s*([1-5])\b", normalized)
    if num:
        idx = int(num.group(1)) - 1
        if 0 <= idx < len(options):
            return options[idx]
    hindi_ord = {"pehla": 0, "dusra": 1, "teesra": 2, "chautha": 3, "pañchwan": 4, "panchwan": 4}
    for word, idx in hindi_ord.items():
        if re.search(rf"\b{word}\b", normalized) and idx < len(options):
            return options[idx]
    # Exact / substring label or target match (prefer longest label hit).
    best: RouteOption | None = None
    best_len = 0
    for opt in options:
        label = opt.label.casefold()
        target = (opt.target or "").casefold()
        query = (opt.query or "").casefold()
        for needle in (label, target, query):
            if needle and len(needle) >= 3 and needle in normalized:
                if len(needle) > best_len:
                    best = opt
                    best_len = len(needle)
        # Single-token target words.
        if target and re.search(rf"\b{re.escape(target)}\b", normalized):
            return opt
    return best


def default_media_options(query: str) -> tuple[RouteOption, ...]:
    q = _clip_str(query) or "this"
    return (
        RouteOption(f"Play on YouTube: {q}", "play", q, "youtube"),
        RouteOption(f"Search Prime Video: {q}", "play", q, "prime"),
        RouteOption(f"Search Netflix: {q}", "play", q, "netflix"),
        RouteOption("Just type what I said", "paste", query, ""),
    )

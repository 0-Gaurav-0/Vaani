"""LLM plan parse helpers — validate payloads and wire Groq-backed llm_parse."""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from typing import Any

from vaani.intent.catalog_card import build_catalog_card
from vaani.intent.schema import IntentPlan, PlanStep, SlotSpec, Verb
from vaani.platform.protocol import PlatformId
from vaani.verbs.registry import Registry

_MAX_STEPS = 6
_REFUSE_REASON_MAX = 200
_logger = logging.getLogger(__name__)

PARSE_SYSTEM_PROMPT = """\
You are Vaani's intent compiler, not a chat assistant.
Reply with ONE JSON object only — no prose, no markdown, do not echo the catalog.
Keys: action (plan|delegate|refuse), confidence (0-1), steps (array), \
delegate_prompt (string|null), refuse_reason (string|null).
Each step: {"verb":"<catalog name>","slots":{...}}.
Honor platform and platform_notes. Use only verbs listed in the catalog.
Prefer the smallest plan. For "open Chrome/Brave and search X", prefer ONE \
site.search step with slots.query and slots.browser (chrome|brave) — do not \
emit a separate app.open before search.
Other compounds → ordered steps.
Refuse polite chit-chat / thanks. Delegate only when no catalog verb fits.
Never invent shell commands or verbs.
Example: {"action":"plan","confidence":0.9,"steps":[{"verb":"site.search","slots":{"query":"Zapto","browser":"chrome"}}],"delegate_prompt":null,"refuse_reason":null}
"""


def validate_plan_payload(
    payload: object,
    enabled_verbs: Mapping[str, Verb],
    *,
    utterance: str,
    raw_utterance: str,
) -> IntentPlan | None:
    """Validate model JSON against the enabled registry; return IntentPlan or None."""
    if not isinstance(payload, Mapping):
        return None
    action = payload.get("action")
    if action not in ("plan", "delegate", "refuse"):
        return None
    confidence = _as_float(payload.get("confidence", 0.0))
    if confidence is None:
        return None

    if action == "plan":
        steps = _parse_steps(payload.get("steps"), enabled_verbs)
        if steps is None:
            return None
        return IntentPlan(
            steps=steps,
            utterance=utterance,
            raw_utterance=raw_utterance,
            source="llm",
            confidence=confidence,
        )

    if action == "delegate":
        prompt = payload.get("delegate_prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return None
        if _nonempty_steps(payload.get("steps")):
            return None
        return IntentPlan(
            steps=(),
            utterance=utterance,
            raw_utterance=raw_utterance,
            source="llm",
            confidence=confidence,
            delegate_prompt=prompt.strip(),
        )

    # refuse
    reason = payload.get("refuse_reason")
    if not isinstance(reason, str) or not reason.strip():
        return None
    reason = reason.strip()
    if len(reason) > _REFUSE_REASON_MAX:
        return None
    if _nonempty_steps(payload.get("steps")):
        return None
    return IntentPlan(
        steps=(),
        utterance=utterance,
        raw_utterance=raw_utterance,
        source="llm",
        confidence=confidence,
        refuse_reason=reason,
    )


def make_llm_parse(
    groq: Any,
    key_provider: Callable[[], str | None],
    registry: Registry,
    platform_fn: Callable[[], PlatformId],
    *,
    get_context_blurb: Callable[[], str | None] | None = None,
) -> Callable[[str], IntentPlan | None]:
    """Build a Groq-backed ``llm_parse(utterance) -> IntentPlan | None`` callable."""

    def llm_parse(utterance: str) -> IntentPlan | None:
        try:
            key = key_provider()
            if not key:
                return None
            platform = platform_fn()
            catalog = build_catalog_card(registry, platform)
            user_payload: dict[str, Any] = {
                "utterance": utterance,
                "catalog": catalog,
            }
            if get_context_blurb is not None:
                blurb = get_context_blurb()
                if blurb:
                    user_payload["context"] = blurb
            raw_text = groq.parse_intent(
                PARSE_SYSTEM_PROMPT,
                json.dumps(user_payload, ensure_ascii=False),
                key,
            )
            if not raw_text:
                _logger.info(
                    "event=llm_parse_stage stage=understand status=empty_response utterance=%r",
                    utterance[:120],
                )
                return None
            _logger.info(
                "event=llm_parse_stage stage=understand status=raw chars=%s preview=%r",
                len(raw_text),
                raw_text[:400],
            )
            payload = _loads_json(raw_text)
            if payload is None:
                _logger.info(
                    "event=llm_parse_stage stage=understand status=bad_json chars=%s preview=%r",
                    len(raw_text),
                    raw_text[:400],
                )
                return None
            enabled = {verb.name: verb for verb in registry.enabled(platform)}
            plan = validate_plan_payload(
                payload,
                enabled,
                utterance=utterance,
                raw_utterance=utterance,
            )
            if plan is None:
                _logger.info(
                    "event=llm_parse_stage stage=understand status=invalid_plan payload=%r",
                    _safe_preview(payload),
                )
                return None
            action = (
                "refuse"
                if plan.refuse_reason
                else "delegate"
                if plan.delegate_prompt
                else "plan"
            )
            _logger.info(
                "event=llm_parse_stage stage=understand status=ok action=%s steps=%s plan=%s",
                action,
                len(plan.steps),
                _format_plan_steps(plan),
            )
            return plan
        except Exception:
            _logger.info("event=llm_parse_stage stage=understand status=error", exc_info=True)
            return None

    return llm_parse


def _format_plan_steps(plan: IntentPlan) -> str:
    if plan.refuse_reason:
        return f"refuse:{plan.refuse_reason[:80]}"
    if plan.delegate_prompt:
        return f"delegate:{plan.delegate_prompt[:80]}"
    parts: list[str] = []
    for step in plan.steps:
        slot_bits = ",".join(f"{k}={v!r}" for k, v in step.slots.items())
        parts.append(f"{step.verb}({slot_bits})")
    return " -> ".join(parts) if parts else "(empty)"


def _safe_preview(payload: object) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(payload)
    return text[:400]


def _loads_json(text: str) -> object | None:
    cleaned = _strip_fences(text.strip())
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    extracted = _extract_json_object(cleaned)
    if extracted is None:
        return None
    try:
        return json.loads(extracted)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _strip_fences(cleaned: str) -> str:
    if cleaned.startswith("```") and cleaned.endswith("```"):
        if "\n" not in cleaned:
            return cleaned[3:-3].strip()
        first, _, rest = cleaned.partition("\n")
        if first == "```" or re.fullmatch(r"```[\w-]+", first):
            return rest[:-3].strip()
    # Leading fence without matching end (common model glitch).
    if cleaned.startswith("```"):
        first, _, rest = cleaned.partition("\n")
        if first == "```" or re.fullmatch(r"```[\w-]+", first):
            cleaned = rest
        else:
            cleaned = cleaned[3:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
        return cleaned.strip()
    return cleaned


def _extract_json_object(text: str) -> str | None:
    """Return the first top-level `{...}` slice, ignoring leading/trailing prose."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _nonempty_steps(raw: object) -> bool:
    return isinstance(raw, list) and len(raw) > 0


def _parse_steps(
    raw: object,
    enabled_verbs: Mapping[str, Verb],
) -> tuple[PlanStep, ...] | None:
    if not isinstance(raw, list) or not raw:
        return None
    if len(raw) > _MAX_STEPS:
        return None
    steps: list[PlanStep] = []
    for item in raw:
        step = _parse_step(item, enabled_verbs)
        if step is None:
            return None
        steps.append(step)
    return tuple(steps)


def _parse_step(
    item: object,
    enabled_verbs: Mapping[str, Verb],
) -> PlanStep | None:
    if not isinstance(item, Mapping):
        return None
    verb_name = item.get("verb")
    if not isinstance(verb_name, str) or verb_name not in enabled_verbs:
        return None
    verb = enabled_verbs[verb_name]
    raw_slots = item.get("slots", {})
    if raw_slots is None:
        raw_slots = {}
    if not isinstance(raw_slots, Mapping):
        return None
    slots = _coerce_slots(raw_slots, verb.slots)
    if slots is None:
        return None
    note = item.get("note")
    if note is not None and not isinstance(note, str):
        return None
    return PlanStep(verb=verb_name, slots=slots, note=note)


def _coerce_slots(
    raw: Mapping[str, Any],
    specs: Mapping[str, SlotSpec],
) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    for name, spec in specs.items():
        if name not in raw:
            if spec.required:
                if spec.default is not None:
                    out[name] = spec.default
                    continue
                return None
            if spec.default is not None:
                out[name] = spec.default
            continue
        coerced = _coerce_value(raw[name], spec.type)
        if coerced is None and raw[name] is not None:
            return None
        if coerced is None and spec.required:
            return None
        if coerced is not None:
            out[name] = coerced
    return out


def _coerce_value(value: Any, type_name: str) -> Any | None:
    if value is None:
        return None
    if type_name in ("str", "path", "enum"):
        if isinstance(value, str):
            return value
        return str(value)
    if type_name == "int":
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                return None
        return None
    if type_name == "float":
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return None
        return None
    if type_name == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "1", "yes", "on"):
                return True
            if lowered in ("false", "0", "no", "off"):
                return False
        return None
    # Unknown type names: accept as-is if present.
    return value


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None

"""LLM plan parse helpers — payload validation first (no network)."""
from __future__ import annotations

from typing import Any, Mapping

from vaani.intent.schema import IntentPlan, PlanStep, SlotSpec, Verb

_MAX_STEPS = 6
_REFUSE_REASON_MAX = 200


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

"""Classify assistant utterances: action vs question vs paste."""
from __future__ import annotations

import re

# Explicit handoff to Hermes / coding agent — skip open/play/clarify routing.
# Include common STT mangling (Vaani→Wani/Vani, etc.).
_AGENT_NAMES = (
    r"(?:vaani|vani|wani|wanni|vahni|vernie|verni|hermes|"
    r"the\s+agent|agent)"
)
_AGENT_HANDOFF_PATTERNS = (
    # "ask Vaani to …" / "ask Wani. Hi" / bare "ask Vaani"
    re.compile(
        rf"^(?:please\s+)?ask\s+{_AGENT_NAMES}\b\s*(?:to\s+)?[.,!?:;-]*\s*(.*)$",
        re.I,
    ),
    # "tell Hermes …" / "tell the agent to …"
    re.compile(
        rf"^(?:please\s+)?tell\s+{_AGENT_NAMES}\b\s*(?:to\s+)?[.,!?:;-]*\s*(.*)$",
        re.I,
    ),
    # "say hi to the agent" / "say hello to Vaani"
    re.compile(
        rf"^(?:please\s+)?say\s+(.+?)\s+to\s+{_AGENT_NAMES}\s*$",
        re.I,
    ),
    # "pass this to the agent: …" / "send to hermes …"
    re.compile(
        rf"^(?:please\s+)?(?:pass|send)\s+(?:this\s+)?to\s+{_AGENT_NAMES}\s*[.,!?:;-]*\s*(.*)$",
        re.I,
    ),
    # "agent: …" / "hey agent …" / "hermes: …"
    # Do NOT match "hey Vaani …" — that is the wake phrase for local assistant
    # (QA / play / open). Hermes handoff uses ask/tell/pass/agent:/hermes:.
    re.compile(
        r"^(?:hey\s+|ok\s+|okay\s+)?(?:the\s+agent|agent|hermes)"
        r"\s*[.,!?:;-]+\s*(.+)$",
        re.I,
    ),
)

_ACTION_PATTERNS = (
    r"\b(open|launch|start|play|watch|search|run|visit|browse|surf|navigate)\b",
    r"\b(kholo|khol|chalao|chalu|shuru|dikhao|dikha|jao|chalo|bajao|baja|suno)\b",
    r"\b(gaana|gana|song|music|trailer)\b",
    r"\byoutube\b",
    r"\bskill\b",
    # Whisper often clips "play" → "ple" at the start of short holds.
    r"^ple\b",
)

_AGENT_PATTERNS = (
    r"\bfix\b",
    r"\brefactor\b",
    r"\bimplement\b",
    r"\bdebug\b",
    r"\bwrite (a |the )?(script|function|class|test|code)\b",
    r"\brun (the )?tests?\b",
)

_QUESTION_PATTERNS = (
    r"^(who|what|when|where|why|how|which|whom|whose)\b",
    r"\b(who is|what is|what are|when is|where is|why is|how many|how much)\b",
    r"\b(what should|which .+ (should|do|is|are))\b",
    r"\b(kaun|kya|kab|kahan|kyun|kaise|kis|kons[aei])\b",
)

# Opinion / help asks that often omit "?" in speech transcripts.
_QA_REQUEST_PATTERNS = (
    r"\b(recommend|recommendation|suggest|suggestion|advise|advise me)\b",
    r"\b(can you|could you|would you|will you|do you know)\b",
    r"\b(should i|any (good|best)|looking for|help me)\b",
    r"\b(give me|show me|find me|tell me)\b",
    r"\b(best|top)\b.+\b(manga|anime|movie|book|song|show|series|app|tool|restaurant|place)\b",
    r"\b(batao|bataao|suggest karo|recommend karo)\b",
)

_PASTE_HINTS = (
    r"^(please )?(type|paste|write|insert|dictate)\b",
    r"\b(send this|email this|message this|paste this|type this)\b",
    r"\bplease send this to\b",
)

# Work-tracking language → nudge Hermes toward Basecamp (see vani-task/AGENTS.md).
_WORK_CONTEXT_PATTERNS = (
    r"\b(to-?dos?|tasks?|assignments?|assignees?)\b",
    r"\b(assigned to|working on|overdue)\b",
    r"\b(project status|status of (this |the )?(to-?do|task|project))\b",
    r"\b(initiatives?|workload)\b",
    r"\bbasecamp\b",
)

_WORK_BASECAMP_HINT = (
    "[Vaani context] Work todos/tasks/assignments/project status: check Basecamp "
    "first (basecamp skill/CLI). If this clearly is not Basecamp, ask which "
    "system before digging elsewhere.\n\n"
)

# Short provenance tag — policy lives in the preloaded ``vaani`` Hermes skill
# (+ AGENTS.md). Do not paste a full policy essay into every prompt.
_VAANI_SOURCE_TAG = "[source: vaani]\n\n"
_LEGACY_POLICY_PREFIX_RE = re.compile(
    r"^\[Vaani policy\][^\n]*(?:\n(?!\[)[^\n]*)*\n+",
    re.I,
)

# Status / report language that contains "update" but is read-only.
_MUTATION_ALLOW_RE = re.compile(
    r"(?:"
    r"\b(?:what(?:'s|s)?|give\s+me|get|show|any|status)\s+(?:the\s+)?update\b|"
    r"\bupdate\s+on\s+(?:that|this|it|the)\b|"
    r"\bstatus\s+update\b|"
    r"\bwhat\s+(?:has\s+)?changed\b|"
    r"\bchange\s+log\b"
    r")",
    re.I,
)

# Hermes via Vaani must not mutate remote systems or the workspace.
_MUTATION_RE = re.compile(
    r"(?:"
    r"\b(?:delete|remove|destroy|drop|trash|erase|purge)\b|"
    r"\b(?:hatao|hata\s*do|mita(?:o| do)|delete\s*karo)\b|"
    r"\b(?:update|edit|modify|alter|rename|reassign)\b|"
    r"\b(?:create|insert|upsert)\b|"
    r"\b(?:post|publish|submit)\b|"
    r"\b(?:send|email|message|notify|bhejo|bhej\s*do)\b|"
    r"\b(?:commit|push|merge|rebase|force[- ]?push)\b|"
    r"\b(?:apply[_ -]?patch|write\s+(?:to\s+)?(?:file|code)|overwrite)\b|"
    r"\b(?:fix|implement|refactor)\b|"
    r"\bmark\s+(?:as\s+)?(?:done|complete|completed|finished)\b|"
    r"\b(?:complete|close|reopen)\s+(?:the\s+)?(?:todo|task|issue|pr|pull\s*request)\b|"
    r"\b(?:add|create)\s+(?:a\s+)?(?:todo|task|comment|issue|pr|file|commit)\b|"
    r"\bcomment\s+on\b|"
    r"\bchange\s+(?:the\s+)?(?:assignee|status|title|due\s*date)\b"
    r")",
    re.I,
)

AGENT_MUTATION_REFUSE = (
    "I can only read that — I won’t delete, update, or change anything."
)


def looks_like_action(text: str) -> bool:
    normalized = " ".join((text or "").casefold().split())
    if not normalized:
        return False
    return any(re.search(p, normalized) for p in _ACTION_PATTERNS)


# Strict play verbs — used to block LLM "play on YouTube" on silence/lyric junk.
# Include STT mangling: "ple" / "plesa" for play/please.
_PLAY_REQUEST_RE = re.compile(
    r"(?:"
    r"\b(?:play|watch|chalao|chala\s*do|bajao|baja\s*do|suno|play\s*karo)\b"
    r"|^(?:ple|plesa)\b"
    r"|\bplesa\b"
    r"|^(?:please)\s+\S+"
    r"|\b(?:gaana|gana|song|music|trailer|video)\b.+\b(?:chalao|bajao|suno|play|watch|dikhao)\b"
    r"|\b(?:chalao|bajao|suno|play|watch|dikhao)\b.+\b(?:gaana|gana|song|music|trailer|video)\b"
    r"|\b(?:youtube|you\s*tube)\b.+\b(?:play|watch|search|chalao|bajao|suno|pe)\b"
    r"|\b(?:play|watch|chalao|bajao|suno)\b.+\b(?:youtube|you\s*tube)\b"
    r")",
    re.I,
)


def looks_like_play_request(text: str) -> bool:
    """True only when the user clearly asked to play/watch media.

    Bare lyric fragments / STT junk ("pyaara", "jhaala") must not qualify —
    the LLM router otherwise invents intent=play and opens YouTube.
    Bare ``play`` / ``play it`` is transport (resume), not a song request.
    """
    normalized = " ".join((text or "").casefold().split())
    if not normalized:
        return False
    if re.fullmatch(
        r"(?:please\s+)?(?:play|plesa|ple)(?:\s+(?:it|this|music|playback))?",
        normalized,
    ):
        return False
    if _PLAY_REQUEST_RE.search(normalized):
        return True
    # Trailing Hinglish play verbs: "despacito bajao"
    if re.search(
        r".+\b(?:bajao|baja\s*do|play\s*karo|chalao|chala\s*do|suno)\s*$",
        normalized,
    ):
        return True
    return False


def clean_play_query(query: str, *, fallback: str = "") -> str:
    """Strip STT play/please crumbs so the YouTube search is the title."""
    text = " ".join(((query or "").strip() or (fallback or "").strip()).split())
    if not text:
        return ""
    text = re.sub(
        r"^(?:please|plesa|ple|play|watch|chalao|bajao|suno)\s+",
        "",
        text,
        flags=re.I,
    ).strip()
    return text


# Silence / lyric crumbs that must not open YouTube. Real titles stay allowed —
# Vaani local play/open is not read-only; only Hermes mutations are.
_PLAY_JUNK_TOKENS = frozenset(
    {
        "pyaara",
        "pyar",
        "jhaala",
        "jhal",
        "kara",
        "do",
        "the",
        "it",
        "this",
        "that",
        "you",
        "thank",
        "thanks",
        "mm",
        "hmm",
        "huh",
        "ok",
        "okay",
        "so",
    }
)


def is_weak_play_query(query: str, *, raw: str = "") -> bool:
    """True when a router ``play`` intent looks like silence junk, not a title.

    Used instead of requiring a perfect play verb — STT often mangles
    ``play sanghu tere`` → ``plesa sanghoote de``, which must still play.
    """
    probe = " ".join((raw or "").casefold().split())
    if probe and re.fullmatch(
        r"(?:thank\s+you|thanks\s+for\s+watching)\.?",
        probe,
    ):
        return True
    cleaned = clean_play_query(query, fallback=raw).casefold()
    if not cleaned or len(cleaned) < 3:
        return True
    words = cleaned.split()
    if len(words) == 1 and (cleaned in _PLAY_JUNK_TOKENS or len(cleaned) < 5):
        return True
    if all(w in _PLAY_JUNK_TOKENS for w in words):
        return True
    return False


def looks_like_work_context(text: str) -> bool:
    """True when utterance looks like work tracking (todos/tasks/status/people)."""
    normalized = " ".join((text or "").casefold().split())
    if not normalized:
        return False
    return any(re.search(p, normalized) for p in _WORK_CONTEXT_PATTERNS)


_SESSION_CONTINUE_PATTERNS = (
    re.compile(
        r"^(?:please\s+)?(?:continue|follow\s*up|resume)\b(?:\s+(?:on|with|that|this|it))?\s*[.,!?:;-]*\s*(.*)$",
        re.I,
    ),
    re.compile(
        r"^(?:please\s+)?(?:in|on)\s+(?:that|the|this)\s+(?:same\s+)?(?:session|task|one|thread)\b\s*[.,!?:;-]*\s*(.*)$",
        re.I,
    ),
    re.compile(
        r"^(?:please\s+)?(?:same\s+session|in\s+the\s+same\s+session)\b\s*[.,!?:;-]*\s*(.*)$",
        re.I,
    ),
    re.compile(
        r"^(?:please\s+)?(?:update|status|what's\s+the\s+update|whats\s+the\s+update|what\s+is\s+the\s+update)\s+(?:on|for)\s+that\b\s*[.,!?:;-]*\s*(.*)$",
        re.I,
    ),
    re.compile(
        r"^(?:please\s+)?(?:also|and)\s+(?:do|add|check|ask)\s+(?:that\s+)?(?:in|on)\s+(?:that|the\s+same)\s+(?:session|task|one)\b\s*[.,!?:;-]*\s*(.*)$",
        re.I,
    ),
)


def extract_session_continue(text: str) -> str | None:
    """If utterance is a same-session follow-up, return the follow-up payload (may be empty)."""
    normalized = " ".join((text or "").strip().split())
    if not normalized:
        return None
    for pattern in _SESSION_CONTINUE_PATTERNS:
        match = pattern.match(normalized)
        if match is None:
            continue
        payload = " ".join((match.group(1) or "").split()).strip(" .,!?:;")
        if payload.casefold() in {"", "that", "this", "it", "one", "task", "session"}:
            return ""
        return payload
    return None


def looks_like_agent_mutation(text: str) -> bool:
    """True when the agent prompt looks like a write/mutate request.

    Used to hard-block Hermes handoffs (read-only policy). Status phrases like
    ``what's the update on that`` are allowed.
    """
    normalized = " ".join((text or "").casefold().split())
    if not normalized:
        return False
    if _MUTATION_ALLOW_RE.search(normalized):
        # Still block if a clear mutate verb appears alongside status language.
        if not re.search(
            r"\b(?:delete|remove|send|commit|push|hatao|bhejo|mark\s+done)\b",
            normalized,
        ):
            return False
    return bool(_MUTATION_RE.search(normalized))


def with_vaani_source_tag(prompt: str) -> str:
    """Mark the handoff as coming from Vaani (Hermes loads the ``vaani`` skill)."""
    text = _LEGACY_POLICY_PREFIX_RE.sub("", (prompt or "").lstrip(), count=1).lstrip()
    if text.startswith("[source: vaani]"):
        return text
    return _VAANI_SOURCE_TAG + text


# Back-compat alias for older imports/tests.
def with_read_only_policy_hint(prompt: str) -> str:
    return with_vaani_source_tag(prompt)


def with_work_context_hint(prompt: str, *, utterance: str | None = None) -> str:
    """Prepend Basecamp-first hint when the user request looks work-related."""
    probe = utterance if utterance is not None else prompt
    if not looks_like_work_context(probe):
        return prompt
    if "[Vaani context]" in (prompt or ""):
        return prompt
    return _WORK_BASECAMP_HINT + (prompt or "")


def extract_agent_handoff(text: str) -> str | None:
    """If utterance is an explicit agent handoff, return the message to forward.

    Examples:
      ``ask Vaani to summarize this`` → ``summarize this``
      ``say hi to the agent`` → ``hi``
      ``tell Hermes fix the flaky test`` → ``fix the flaky test``
    """
    normalized = " ".join((text or "").strip().split())
    if not normalized:
        return None
    for pattern in _AGENT_HANDOFF_PATTERNS:
        match = pattern.match(normalized)
        if match is None:
            continue
        payload = " ".join((match.group(1) or "").split()).strip(" .,!?:;")
        # Bare "ask Vaani" / "ask the agent" → greet Hermes so the UI opens.
        return payload or "hi"
    return None


def classify_assistant_intent(text: str) -> str:
    """Return ``action``, ``qa``, ``codex``, or ``paste``."""
    normalized = " ".join((text or "").casefold().split())
    if not normalized:
        return "paste"
    if extract_agent_handoff(text):
        return "codex"
    if looks_like_action(normalized):
        return "action"
    for pattern in _AGENT_PATTERNS:
        if re.search(pattern, normalized):
            return "codex"
    for pattern in _PASTE_HINTS:
        if re.search(pattern, normalized):
            return "paste"
    if normalized.endswith("?"):
        return "qa"
    for pattern in _QUESTION_PATTERNS:
        if re.search(pattern, normalized):
            return "qa"
    for pattern in _QA_REQUEST_PATTERNS:
        if re.search(pattern, normalized):
            return "qa"
    return "paste"

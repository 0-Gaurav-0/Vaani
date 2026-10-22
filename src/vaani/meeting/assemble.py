"""Merge per-part Gemini words into one speaker-labelled transcript.

Parts after the first start ``OVERLAP_S`` before their cut. Words both parts
heard in that overlap (same normalised text, within ``MATCH_S``) vote on which
global speaker each part-local label is. Each part contributes only the words
from its own cut onward, so the overlap is not duplicated.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .gemini import Word, parse_words

MATCH_S = 1.0


@dataclass
class Turn:
    speaker: int  # global speaker number, 1-based in order of first appearance
    start: float
    text: str


def _norm(text: str) -> str:
    return re.sub(r"\W+", "", text.casefold())


def link_speakers(prev: list[tuple[float, str, int]], cur: list[tuple[float, str, str]]) -> dict[str, int]:
    """Map current part's labels to global ids using overlap matches.

    ``prev``: (abs_time, norm_text, global_id) for the previous part's words in
    the overlap; ``cur``: (abs_time, norm_text, local_label).
    """
    votes: dict[str, Counter] = defaultdict(Counter)
    j = 0
    for t, text, label in cur:
        while j < len(prev) and prev[j][0] < t - MATCH_S:
            j += 1
        k = j
        while k < len(prev) and prev[k][0] <= t + MATCH_S:
            if prev[k][1] == text and text:
                votes[label][prev[k][2]] += 1
                break
            k += 1
    mapping: dict[str, int] = {}
    taken: set[int] = set()
    # Strongest evidence first; one local label per global id.
    for label, counter in sorted(votes.items(), key=lambda kv: -sum(kv[1].values())):
        for gid, _ in counter.most_common():
            if gid not in taken:
                mapping[label] = gid
                taken.add(gid)
                break
    return mapping


def assemble(parts: list[dict], bodies: list[dict]) -> list[Turn]:
    """``parts``: specs from prepare (start, cut, end); ``bodies``: Gemini JSON per part."""
    next_id = 1
    turns: list[Turn] = []
    prev_overlap: list[tuple[float, str, int]] = []
    for part, body in zip(parts, bodies):
        blocks, words = parse_words(body)
        start, cut = float(part["start"]), float(part["cut"])
        abs_words = [(start + w.start, w) for w in words]
        overlap_end = cut  # words before the cut belong to the previous part
        cur_overlap = [(t, _norm(w.text), w.speaker) for t, w in abs_words if t < overlap_end]
        mapping = link_speakers(prev_overlap, cur_overlap) if prev_overlap else {}
        for _, w in abs_words:
            if w.speaker not in mapping:
                mapping[w.speaker] = next_id
                next_id += 1
        kept = [(t, w) for t, w in abs_words if t >= cut]
        turns.extend(_turns(kept, blocks, mapping))
        # Words of this part inside the *next* part's overlap window.
        nxt = parts.index(part) + 1
        if nxt < len(parts):
            from_t = float(parts[nxt]["start"])
            prev_overlap = sorted(
                (t, _norm(w.text), mapping[w.speaker]) for t, w in abs_words if t >= from_t
            )
    return _merge_adjacent(turns)


def _turns(kept: list[tuple[float, Word]], blocks: list[str], mapping: dict[str, int]) -> list[Turn]:
    out: list[Turn] = []
    group: list[tuple[float, Word]] = []

    def flush() -> None:
        if not group:
            return
        first, last = group[0][1], group[-1][1]
        if first.block == last.block and last.ei > first.si:
            text = blocks[first.block][first.si : last.ei]
        else:
            text = " ".join(w.text for _, w in group)
        out.append(Turn(mapping[first.speaker], group[0][0], text.strip()))
        group.clear()

    for t, w in kept:
        if group and (w.speaker != group[-1][1].speaker or w.block != group[-1][1].block):
            flush()
        group.append((t, w))
    flush()
    return out


def _merge_adjacent(turns: list[Turn]) -> list[Turn]:
    merged: list[Turn] = []
    for turn in turns:
        if merged and merged[-1].speaker == turn.speaker:
            merged[-1].text = f"{merged[-1].text} {turn.text}".strip()
        elif turn.text:
            merged.append(turn)
    return merged


def hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def to_markdown(title: str, turns: list[Turn], names: dict[int, str] | None = None) -> str:
    names = names or {}
    lines = [f"# {title}", ""]
    for turn in turns:
        who = names.get(turn.speaker, f"Speaker {turn.speaker}")
        lines.append(f"**{who}** [{hms(turn.start)}]  ")
        lines.append(turn.text)
        lines.append("")
    return "\n".join(lines)

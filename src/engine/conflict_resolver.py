"""
conflict_resolver: Detect and resolve conflicts between RFCs.

When two RFCs propose overlapping opcode ranges, semantically incompatible
specifications, or scope collisions, this module detects the conflict and
can propose a synthesis RFC to merge the proposals.
"""

from __future__ import annotations

import re
import copy
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Set, Tuple

from .rfc_engine import (
    RFC,
    RFCState,
    RFCEngine,
    VotePosition,
)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class ConflictType(Enum):
    """Categories of RFC conflicts."""
    OPCODE_OVERLAP = "OPCODE_OVERLAP"
    SEMANTIC_CONFLICT = "SEMANTIC_CONFLICT"
    SCOPE_OVERLAP = "SCOPE_OVERLAP"


@dataclass
class RFCConflict:
    """Describes a conflict between two RFCs."""
    rfc_a: int
    rfc_b: int
    conflict_type: ConflictType
    description: str


# ---------------------------------------------------------------------------
# Helpers — opcode range extraction
# ---------------------------------------------------------------------------

_OPCODE_RE = re.compile(r"0x([0-9A-Fa-f]{2})\s*(?:[-–]\s*0x([0-9A-Fa-f]{2}))?")


def _extract_opcode_ranges(text: str) -> List[Tuple[int, int]]:
    """Return list of (start, end) opcode ranges found in *text*."""
    ranges: List[Tuple[int, int]] = []
    for m in _OPCODE_RE.finditer(text):
        start = int(m.group(1), 16)
        end = int(m.group(2), 16) if m.group(2) else start
        ranges.append((start, end))
    return ranges


def _ranges_overlap(
    a_start: int, a_end: int, b_start: int, b_end: int,
) -> bool:
    return a_start <= b_end and b_start <= a_end


# ---------------------------------------------------------------------------
# Scope keywords — lightweight heuristic
# ---------------------------------------------------------------------------

_SCOPE_KEYWORDS = {
    "ISA", "VM", "opcode", "bytecode",
    "SIGNAL", "agent", "communication",
    "A2A", "protocol",
    "fleet", "coordination",
    "tool", "infrastructure",
}


def _extract_scope_keywords(text: str) -> Set[str]:
    """Return the set of scope keywords found in *text* (case-insensitive)."""
    lower = text.lower()
    found: Set[str] = set()
    for kw in _SCOPE_KEYWORDS:
        if kw.lower() in lower:
            found.add(kw.upper())
    return found


# ---------------------------------------------------------------------------
# ConflictResolver
# ---------------------------------------------------------------------------

class ConflictResolver:
    """
    Detect conflicts among RFCs and propose synthesis resolutions.

    The resolver operates on an ``RFCEngine`` instance, inspecting active
    RFCs for three classes of conflict:

    1. **OPCODE_OVERLAP** — two RFCs claim the same opcode range.
    2. **SEMANTIC_CONFLICT** — two RFCs specify contradictory behavior for
       the same concept (heuristic: shared keywords + explicit conflict
       markers).
    3. **SCOPE_OVERLAP** — two RFCs address overlapping problem domains.
    """

    # Conflict-marker phrases that signal explicit contradiction
    _CONTRADICTION_MARKERS = [
        "contradicts", "conflicts with", "incompatible with",
        "alternative to", "instead of", "opposes", "rejects",
    ]

    def __init__(self, engine: RFCEngine) -> None:
        self._engine = engine

    # ---- detection --------------------------------------------------------

    def detect_conflicts(self, rfc_numbers: Optional[List[int]] = None) -> List[RFCConflict]:
        """
        Scan the given RFCs (defaults to all open + synthesis) and return
        a list of detected conflicts.  Each pair is reported at most once
        (sorted by number, lower first).
        """
        if rfc_numbers is None:
            rfc_numbers = [
                r.number for r in self._engine.all_rfcs().values()
                if r.state in (
                    RFCState.PROPOSAL, RFCState.DISCUSSION,
                    RFCState.EVIDENCE, RFCState.SYNTHESIS,
                )
            ]

        conflicts: List[RFCConflict] = []
        rfc_map = {n: self._engine.get_rfc(n) for n in rfc_numbers}
        seen: set = set()

        for i, num_a in enumerate(rfc_numbers):
            for num_b in rfc_numbers[i + 1:]:
                pair_key = (min(num_a, num_b), max(num_a, num_b))
                if pair_key in seen:
                    continue
                seen.add(pair_key)

                rfc_a = rfc_map[num_a]
                rfc_b = rfc_map[num_b]
                if rfc_a is None or rfc_b is None:
                    continue

                c = self._check_pair(rfc_a, rfc_b)
                if c is not None:
                    conflicts.append(c)

        return conflicts

    def _check_pair(self, a: RFC, b: RFC) -> Optional[RFCConflict]:
        """Return the first conflict found between *a* and *b*, or None."""
        # 1. Opcode overlap
        conflict = self._check_opcode_overlap(a, b)
        if conflict:
            return conflict

        # 2. Semantic conflict
        conflict = self._check_semantic_conflict(a, b)
        if conflict:
            return conflict

        # 3. Scope overlap
        conflict = self._check_scope_overlap(a, b)
        if conflict:
            return conflict

        return None

    def _check_opcode_overlap(self, a: RFC, b: RFC) -> Optional[RFCConflict]:
        """Detect overlapping opcode ranges between two RFCs."""
        combined_text_a = f"{a.body} {a.specification} {a.motivation}"
        combined_text_b = f"{b.body} {b.specification} {b.motivation}"
        ranges_a = _extract_opcode_ranges(combined_text_a)
        ranges_b = _extract_opcode_ranges(combined_text_b)
        overlapping: List[str] = []
        for sa, ea in ranges_a:
            for sb, eb in ranges_b:
                if _ranges_overlap(sa, ea, sb, eb):
                    overlap_start = max(sa, sb)
                    overlap_end = min(ea, eb)
                    overlapping.append(
                        f"0x{overlap_start:02X}-0x{overlap_end:02X}"
                    )
        if overlapping:
            return RFCConflict(
                rfc_a=a.number,
                rfc_b=b.number,
                conflict_type=ConflictType.OPCODE_OVERLAP,
                description=(
                    f"Opcode overlap between RFC-{a.number} and RFC-{b.number}: "
                    + ", ".join(overlapping)
                ),
            )
        return None

    def _check_semantic_conflict(self, a: RFC, b: RFC) -> Optional[RFCConflict]:
        """
        Detect semantic conflicts via explicit contradiction markers and
        shared scope keywords.
        """
        # Each RFC mentions the other with a contradiction marker
        combined_a = f"{a.body} {a.specification} {a.motivation}"
        combined_b = f"{b.body} {b.specification} {b.motivation}"
        lower_a = combined_a.lower()
        lower_b = combined_b.lower()

        a_mentions_b = f"rfc-{b.number:04d}" in lower_a or f"rfc {b.number}" in lower_a
        b_mentions_a = f"rfc-{a.number:04d}" in lower_b or f"rfc {a.number}" in lower_b

        if not (a_mentions_b or b_mentions_a):
            return None

        has_contradiction = any(
            marker in lower_a or marker in lower_b
            for marker in self._CONTRADICTION_MARKERS
        )
        if not has_contradiction:
            return None

        return RFCConflict(
            rfc_a=a.number,
            rfc_b=b.number,
            conflict_type=ConflictType.SEMANTIC_CONFLICT,
            description=(
                f"RFC-{a.number} and RFC-{b.number} reference each other "
                f"with contradiction markers, indicating a semantic conflict."
            ),
        )

    def _check_scope_overlap(self, a: RFC, b: RFC) -> Optional[RFCConflict]:
        """
        Detect scope overlap: two RFCs address overlapping domains.

        Threshold: at least 3 shared scope keywords.
        """
        combined_a = f"{a.title} {a.body} {a.specification} {a.motivation}"
        combined_b = f"{b.title} {b.body} {b.specification} {b.motivation}"
        keywords_a = _extract_scope_keywords(combined_a)
        keywords_b = _extract_scope_keywords(combined_b)
        shared = keywords_a & keywords_b
        if len(shared) >= 3:
            return RFCConflict(
                rfc_a=a.number,
                rfc_b=b.number,
                conflict_type=ConflictType.SCOPE_OVERLAP,
                description=(
                    f"RFC-{a.number} and RFC-{b.number} share scope keywords: "
                    + ", ".join(sorted(shared))
                ),
            )
        return None

    # ---- resolution -------------------------------------------------------

    def propose_resolution(
        self,
        conflict: RFCConflict,
        preferred_rfc: int,
    ) -> RFC:
        """
        Generate a synthesis RFC that merges the two conflicting RFCs,
        biasing toward *preferred_rfc*.

        The new RFC is created in SYNTHESIS state with a body that
        describes the merge rationale and references both parent RFCs.
        """
        a = self._engine.get_rfc(conflict.rfc_a)
        b = self._engine.get_rfc(conflict.rfc_b)
        if a is None or b is None:
            raise KeyError("One or both RFCs in conflict not found")

        # Determine preferred vs other
        if preferred_rfc == conflict.rfc_a:
            pref, other = a, b
        elif preferred_rfc == conflict.rfc_b:
            pref, other = b, a
        else:
            raise ValueError(
                f"preferred_rfc {preferred_rfc} is not part of this conflict "
                f"({conflict.rfc_a}, {conflict.rfc_b})"
            )

        synthesis_title = (
            f"Synthesis: {pref.title} + {other.title}"
        )
        synthesis_body = (
            f"# Synthesis of RFC-{conflict.rfc_a} and RFC-{conflict.rfc_b}\n\n"
            f"**Conflict type:** {conflict.conflict_type.value}\n\n"
            f"{conflict.description}\n\n"
            f"## Preferred Position (RFC-{pref.number})\n\n"
            f"{pref.body or pref.specification}\n\n"
            f"## Alternative Position (RFC-{other.number})\n\n"
            f"{other.body or other.specification}\n\n"
            f"## Resolution Rationale\n\n"
            f"RFC-{pref.number} is preferred as the base. "
            f"Elements from RFC-{other.number} are incorporated where compatible."
        )
        synthesis_spec = (
            f"## Merged Specification\n\n"
            f"Primary spec from RFC-{pref.number}:\n{pref.specification}\n\n"
            f"Contributions from RFC-{other.number}:\n{other.specification}"
        )
        synthesis_questions = list(pref.open_questions) + list(other.open_questions)

        synth_stub = self._engine.create_rfc(
            title=synthesis_title,
            author="conflict-resolver",
            body=synthesis_body,
            specification=synthesis_spec,
            open_questions=synthesis_questions,
        )
        synth_num = synth_stub.number
        # Walk through the state graph to SYNTHESIS
        self._engine.advance_state(synth_num, RFCState.PROPOSAL)
        self._engine.advance_state(synth_num, RFCState.EVIDENCE)
        self._engine.advance_state(synth_num, RFCState.SYNTHESIS)
        # Re-fetch to get the current state
        return self._engine.get_rfc(synth_num)

    def merge_rfcs(
        self,
        conflict: RFCConflict,
        synthesis_number: int,
    ) -> None:
        """
        Mark both conflicting RFCs as SUPERSEDED once both authors (or the
        synthesis) have reached consensus.

        The synthesis RFC must have consensus (checked via the engine) and
        must be in ACCEPTED state.
        """
        synth = self._engine.get_rfc(synthesis_number)
        if synth is None:
            raise KeyError(f"Synthesis RFC {synthesis_number} not found")
        if synth.state != RFCState.ACCEPTED:
            raise ValueError(
                f"Synthesis RFC must be ACCEPTED to merge; "
                f"currently {synth.state.value}"
            )

        for old_num in (conflict.rfc_a, conflict.rfc_b):
            if old_num == synthesis_number:
                continue
            self._engine.supersede(old_num, synthesis_number)

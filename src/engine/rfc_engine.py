"""
flux-rfc-engine: Structured Disagreement Resolution for the FLUX Fleet.

Implements the full RFC lifecycle: creation, review, voting, consensus
detection, state transitions, and supersession.  Zero external deps —
only dataclasses, datetime, and json from the standard library.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RFCState(Enum):
    """Lifecycle states for an RFC."""
    DRAFT = "DRAFT"
    PROPOSAL = "PROPOSAL"
    DISCUSSION = "DISCUSSION"
    EVIDENCE = "EVIDENCE"
    SYNTHESIS = "SYNTHESIS"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    WITHDRAWN = "WITHDRAWN"


class VotePosition(Enum):
    """Possible vote positions."""
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ABSTAIN = "ABSTAIN"
    DEFER = "DEFER"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Vote:
    """A single vote cast on an RFC."""
    voter: str
    position: VotePosition
    comment: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class RFC:
    """Core RFC document model."""
    number: int
    title: str
    author: str
    state: RFCState = RFCState.DRAFT
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    body: str = ""
    motivation: str = ""
    specification: str = ""
    open_questions: List[str] = field(default_factory=list)
    votes: List[Vote] = field(default_factory=list)
    superseded_by: Optional[int] = None

    # ---- helpers ----------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict."""
        return {
            "number": self.number,
            "title": self.title,
            "author": self.author,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "body": self.body,
            "motivation": self.motivation,
            "specification": self.specification,
            "open_questions": list(self.open_questions),
            "votes": [
                {
                    "voter": v.voter,
                    "position": v.position.value,
                    "comment": v.comment,
                    "timestamp": v.timestamp.isoformat(),
                }
                for v in self.votes
            ],
            "superseded_by": self.superseded_by,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RFC:
        """Deserialize from a dict produced by ``to_dict``."""
        votes = [
            Vote(
                voter=v["voter"],
                position=VotePosition(v["position"]),
                comment=v.get("comment", ""),
                timestamp=datetime.fromisoformat(v["timestamp"])
                if v.get("timestamp")
                else datetime.now(timezone.utc),
            )
            for v in data.get("votes", [])
        ]
        return cls(
            number=data["number"],
            title=data["title"],
            author=data["author"],
            state=RFCState(data["state"]),
            created_at=datetime.fromisoformat(data["created_at"])
            if data.get("created_at")
            else datetime.now(timezone.utc),
            updated_at=datetime.fromisoformat(data["updated_at"])
            if data.get("updated_at")
            else datetime.now(timezone.utc),
            body=data.get("body", ""),
            motivation=data.get("motivation", ""),
            specification=data.get("specification", ""),
            open_questions=list(data.get("open_questions", [])),
            votes=votes,
            superseded_by=data.get("superseded_by"),
        )


# ---------------------------------------------------------------------------
# Valid state-transition graph
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS: Dict[RFCState, set] = {
    RFCState.DRAFT: {RFCState.PROPOSAL, RFCState.WITHDRAWN},
    RFCState.PROPOSAL: {RFCState.DISCUSSION, RFCState.EVIDENCE, RFCState.WITHDRAWN, RFCState.REJECTED},
    RFCState.DISCUSSION: {RFCState.SYNTHESIS, RFCState.EVIDENCE, RFCState.WITHDRAWN, RFCState.REJECTED},
    RFCState.EVIDENCE: {RFCState.DISCUSSION, RFCState.SYNTHESIS, RFCState.WITHDRAWN, RFCState.REJECTED},
    RFCState.SYNTHESIS: {RFCState.ACCEPTED, RFCState.DISCUSSION, RFCState.WITHDRAWN, RFCState.REJECTED},
    RFCState.ACCEPTED: set(),  # terminal — only supersede can move it
    RFCState.REJECTED: set(),  # terminal
    RFCState.SUPERSEDED: set(),  # terminal
    RFCState.WITHDRAWN: set(),  # terminal
}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class RFCEngine:
    """
    Manages the full RFC lifecycle.

    All state is held in-memory in a dict keyed by RFC number.  For
    persistence, see ``git_persistence.GitPersistence``.
    """

    def __init__(self, next_number: int = 1) -> None:
        self._rfcs: Dict[int, RFC] = {}
        self._next_number: int = next_number

    # ---- creation ---------------------------------------------------------

    def create_rfc(
        self,
        title: str,
        author: str,
        body: str = "",
        motivation: str = "",
        specification: str = "",
        open_questions: Optional[List[str]] = None,
    ) -> RFC:
        """Create a new RFC in DRAFT state and return it."""
        rfc = RFC(
            number=self._next_number,
            title=title,
            author=author,
            body=body,
            motivation=motivation,
            specification=specification,
            open_questions=open_questions or [],
        )
        self._rfcs[rfc.number] = rfc
        self._next_number += 1
        return copy.deepcopy(rfc)

    # ---- queries ----------------------------------------------------------

    def get_rfc(self, number: int) -> Optional[RFC]:
        """Return a copy of the RFC, or None."""
        rfc = self._rfcs.get(number)
        return copy.deepcopy(rfc) if rfc else None

    def get_open_rfcs(self) -> List[RFC]:
        """All RFCs currently in PROPOSAL or DISCUSSION."""
        return [
            copy.deepcopy(r)
            for r in self._rfcs.values()
            if r.state in (RFCState.PROPOSAL, RFCState.DISCUSSION)
        ]

    def get_canonical_rfcs(self) -> List[RFC]:
        """All ACCEPTED RFCs."""
        return [
            copy.deepcopy(r)
            for r in self._rfcs.values()
            if r.state == RFCState.ACCEPTED
        ]

    # ---- state transitions ------------------------------------------------

    def submit_for_review(self, rfc_number: int) -> RFC:
        """Move a DRAFT RFC to PROPOSAL."""
        return self.advance_state(rfc_number, RFCState.PROPOSAL)

    def advance_state(self, rfc_number: int, new_state: RFCState) -> RFC:
        """
        Transition an RFC to *new_state*.

        Raises ``ValueError`` if the transition is not in the valid
        transition graph.
        """
        rfc = self._rfcs.get(rfc_number)
        if rfc is None:
            raise KeyError(f"RFC {rfc_number} not found")
        if new_state not in _VALID_TRANSITIONS.get(rfc.state, set()):
            raise ValueError(
                f"Invalid transition: {rfc.state.value} -> {new_state.value}"
            )
        rfc.state = new_state
        rfc.updated_at = datetime.now(timezone.utc)
        return copy.deepcopy(rfc)

    # ---- voting -----------------------------------------------------------

    def cast_vote(
        self,
        rfc_number: int,
        voter: str,
        position: VotePosition,
        comment: str = "",
    ) -> RFC:
        """
        Cast (or replace) a vote on an RFC.

        The RFC must be in a votable state (PROPOSAL, DISCUSSION, EVIDENCE,
        or SYNTHESIS).  If the voter already voted, their previous vote is
        replaced.
        """
        votable = {
            RFCState.PROPOSAL, RFCState.DISCUSSION,
            RFCState.EVIDENCE, RFCState.SYNTHESIS,
        }
        rfc = self._rfcs.get(rfc_number)
        if rfc is None:
            raise KeyError(f"RFC {rfc_number} not found")
        if rfc.state not in votable:
            raise ValueError(
                f"Cannot vote on RFC in {rfc.state.value} state"
            )

        # Replace existing vote by same voter
        for idx, v in enumerate(rfc.votes):
            if v.voter == voter:
                rfc.votes[idx] = Vote(voter=voter, position=position,
                                      comment=comment)
                rfc.updated_at = datetime.now(timezone.utc)
                return copy.deepcopy(rfc)

        rfc.votes.append(Vote(voter=voter, position=position, comment=comment))
        rfc.updated_at = datetime.now(timezone.utc)
        return copy.deepcopy(rfc)

    def check_consensus(self, rfc_number: int) -> bool:
        """
        Consensus heuristic: **3 APPROVE** votes with **zero REJECT** votes.

        This mirrors the fleet requirement: Oracle1 APPROVE + 2 additional
        agent APPROVEs, and all objections must be addressed.
        """
        rfc = self._rfcs.get(rfc_number)
        if rfc is None:
            raise KeyError(f"RFC {rfc_number} not found")

        approve_count = sum(
            1 for v in rfc.votes if v.position == VotePosition.APPROVE
        )
        reject_count = sum(
            1 for v in rfc.votes if v.position == VotePosition.REJECT
        )
        return approve_count >= 3 and reject_count == 0

    # ---- supersession -----------------------------------------------------

    def supersede(self, old_number: int, new_number: int) -> None:
        """
        Mark *old_number* as SUPERSEDED by *new_number*.

        The new RFC must be in ACCEPTED state.
        """
        old_rfc = self._rfcs.get(old_number)
        new_rfc = self._rfcs.get(new_number)
        if old_rfc is None:
            raise KeyError(f"RFC {old_number} not found")
        if new_rfc is None:
            raise KeyError(f"RFC {new_number} not found")
        if new_rfc.state != RFCState.ACCEPTED:
            raise ValueError(
                f"RFC {new_number} must be ACCEPTED to supersede another, "
                f"but is {new_rfc.state.value}"
            )
        old_rfc.state = RFCState.SUPERSEDED
        old_rfc.superseded_by = new_number
        old_rfc.updated_at = datetime.now(timezone.utc)

    # ---- introspection ----------------------------------------------------

    @property
    def next_number(self) -> int:
        return self._next_number

    def all_rfcs(self) -> Dict[int, RFC]:
        return {k: copy.deepcopy(v) for k, v in self._rfcs.items()}

    def load_rfc(self, rfc: RFC) -> None:
        """Inject an RFC into the engine (useful for persistence round-trips)."""
        self._rfcs[rfc.number] = rfc
        if rfc.number >= self._next_number:
            self._next_number = rfc.number + 1

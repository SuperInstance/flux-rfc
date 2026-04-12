"""
Comprehensive tests for the flux-rfc engine.

Covers:
- RFC lifecycle (draft -> proposal -> discussion -> accepted)
- Voting and consensus detection
- State transition validation
- Supersession
- Conflict detection (opcode overlap, semantic, scope)
- Conflict resolution and merge
- Rejection
- Git persistence round-trip
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import List

# Relative imports — run from repo root with: python -m pytest tests/
from src.engine.rfc_engine import (
    RFC,
    RFCState,
    RFCEngine,
    Vote,
    VotePosition,
)
from src.engine.conflict_resolver import (
    ConflictResolver,
    ConflictType,
    RFCConflict,
)
from src.persistence.git_persistence import GitPersistence


# ===================================================================
# Helpers
# ===================================================================

def _make_engine_with_numbers(start: int = 1) -> RFCEngine:
    """Create an engine with next_number set."""
    return RFCEngine(next_number=start)


def _add_votes_for_consensus(engine: RFCEngine, rfc_num: int) -> None:
    """Add 3 APPROVE votes (no REJECT) to achieve consensus."""
    for voter in ("Oracle1", "Agent-A", "Agent-B"):
        engine.cast_vote(rfc_num, voter, VotePosition.APPROVE, "LGTM")


# ===================================================================
# 1. RFC Lifecycle Tests
# ===================================================================

class TestRFCLifecycle(unittest.TestCase):
    """Full lifecycle: DRAFT -> PROPOSAL -> DISCUSSION -> ACCEPTED."""

    def test_create_rfc_starts_in_draft(self) -> None:
        engine = _make_engine_with_numbers()
        rfc = engine.create_rfc("Test RFC", "Quill")
        self.assertEqual(rfc.state, RFCState.DRAFT)
        self.assertEqual(rfc.number, 1)
        self.assertEqual(rfc.title, "Test RFC")
        self.assertEqual(rfc.author, "Quill")

    def test_submit_for_review_moves_to_proposal(self) -> None:
        engine = _make_engine_with_numbers()
        rfc = engine.create_rfc("Test RFC", "Quill")
        result = engine.submit_for_review(1)
        self.assertEqual(result.state, RFCState.PROPOSAL)
        # Verify in-memory update
        current = engine.get_rfc(1)
        self.assertIsNotNone(current)
        self.assertEqual(current.state, RFCState.PROPOSAL)

    def test_full_lifecycle_draft_to_accepted(self) -> None:
        engine = _make_engine_with_numbers()
        rfc = engine.create_rfc(
            "Full Lifecycle RFC", "Quill",
            motivation="We need this.",
            specification="Do the thing.",
        )
        num = rfc.number

        # DRAFT -> PROPOSAL
        engine.submit_for_review(num)
        self.assertEqual(engine.get_rfc(num).state, RFCState.PROPOSAL)

        # PROPOSAL -> EVIDENCE
        engine.advance_state(num, RFCState.EVIDENCE)
        self.assertEqual(engine.get_rfc(num).state, RFCState.EVIDENCE)

        # EVIDENCE -> DISCUSSION
        engine.advance_state(num, RFCState.DISCUSSION)
        self.assertEqual(engine.get_rfc(num).state, RFCState.DISCUSSION)

        # DISCUSSION -> SYNTHESIS
        engine.advance_state(num, RFCState.SYNTHESIS)
        self.assertEqual(engine.get_rfc(num).state, RFCState.SYNTHESIS)

        # Add consensus votes
        _add_votes_for_consensus(engine, num)
        self.assertTrue(engine.check_consensus(num))

        # SYNTHESIS -> ACCEPTED
        engine.advance_state(num, RFCState.ACCEPTED)
        self.assertEqual(engine.get_rfc(num).state, RFCState.ACCEPTED)

    def test_auto_incrementing_number(self) -> None:
        engine = _make_engine_with_numbers()
        r1 = engine.create_rfc("First", "A")
        r2 = engine.create_rfc("Second", "B")
        r3 = engine.create_rfc("Third", "C")
        self.assertEqual(r1.number, 1)
        self.assertEqual(r2.number, 2)
        self.assertEqual(r3.number, 3)
        self.assertEqual(engine.next_number, 4)

    def test_create_rfc_with_all_fields(self) -> None:
        engine = _make_engine_with_numbers()
        rfc = engine.create_rfc(
            title="Detailed RFC",
            author="Quill",
            body="This is the body.",
            motivation="Because we need it.",
            specification="Spec details here.",
            open_questions=["Q1?", "Q2?"],
        )
        self.assertEqual(rfc.body, "This is the body.")
        self.assertEqual(rfc.motivation, "Because we need it.")
        self.assertEqual(rfc.specification, "Spec details here.")
        self.assertEqual(rfc.open_questions, ["Q1?", "Q2?"])

    def test_get_rfc_returns_copy(self) -> None:
        """Mutating the returned RFC should not affect the engine."""
        engine = _make_engine_with_numbers()
        engine.create_rfc("Test", "Quill")
        rfc = engine.get_rfc(1)
        assert rfc is not None
        rfc.title = "MUTATED"
        original = engine.get_rfc(1)
        assert original is not None
        self.assertEqual(original.title, "Test")


# ===================================================================
# 2. Voting and Consensus Tests
# ===================================================================

class TestVotingAndConsensus(unittest.TestCase):

    def _make_proposal(self, engine: RFCEngine) -> int:
        rfc = engine.create_rfc("Votable RFC", "Quill")
        engine.submit_for_review(rfc.number)
        return rfc.number

    def test_cast_vote(self) -> None:
        engine = _make_engine_with_numbers()
        num = self._make_proposal(engine)
        result = engine.cast_vote(
            num, "Oracle1", VotePosition.APPROVE, "Looks good"
        )
        self.assertEqual(len(result.votes), 1)
        self.assertEqual(result.votes[0].voter, "Oracle1")
        self.assertEqual(result.votes[0].position, VotePosition.APPROVE)

    def test_replace_vote(self) -> None:
        engine = _make_engine_with_numbers()
        num = self._make_proposal(engine)
        engine.cast_vote(num, "Agent-A", VotePosition.ABSTAIN, "Need more info")
        engine.cast_vote(num, "Agent-A", VotePosition.APPROVE, "Now satisfied")
        rfc = engine.get_rfc(num)
        assert rfc is not None
        self.assertEqual(len(rfc.votes), 1)
        self.assertEqual(rfc.votes[0].position, VotePosition.APPROVE)

    def test_consensus_three_approves_no_reject(self) -> None:
        engine = _make_engine_with_numbers()
        num = self._make_proposal(engine)
        _add_votes_for_consensus(engine, num)
        self.assertTrue(engine.check_consensus(num))

    def test_no_consensus_with_reject(self) -> None:
        engine = _make_engine_with_numbers()
        num = self._make_proposal(engine)
        engine.cast_vote(num, "Oracle1", VotePosition.APPROVE)
        engine.cast_vote(num, "Agent-A", VotePosition.APPROVE)
        engine.cast_vote(num, "Agent-B", VotePosition.REJECT, "Bad idea")
        self.assertFalse(engine.check_consensus(num))

    def test_no_consensus_insufficient_approves(self) -> None:
        engine = _make_engine_with_numbers()
        num = self._make_proposal(engine)
        engine.cast_vote(num, "Oracle1", VotePosition.APPROVE)
        engine.cast_vote(num, "Agent-A", VotePosition.APPROVE)
        self.assertFalse(engine.check_consensus(num))

    def test_defer_vote_does_not_count_as_approve(self) -> None:
        engine = _make_engine_with_numbers()
        num = self._make_proposal(engine)
        engine.cast_vote(num, "Oracle1", VotePosition.APPROVE)
        engine.cast_vote(num, "Agent-A", VotePosition.APPROVE)
        engine.cast_vote(num, "Agent-B", VotePosition.DEFER, "Need more time")
        self.assertFalse(engine.check_consensus(num))

    def test_vote_on_terminal_state_raises(self) -> None:
        engine = _make_engine_with_numbers()
        rfc = engine.create_rfc("Test", "Quill")
        # DRAFT is not votable
        with self.assertRaises(ValueError):
            engine.cast_vote(rfc.number, "X", VotePosition.APPROVE)

    def test_vote_on_nonexistent_rfc_raises(self) -> None:
        engine = _make_engine_with_numbers()
        with self.assertRaises(KeyError):
            engine.cast_vote(999, "X", VotePosition.APPROVE)


# ===================================================================
# 3. State Transition Validation Tests
# ===================================================================

class TestStateTransitionValidation(unittest.TestCase):

    def test_draft_to_accepted_is_invalid(self) -> None:
        engine = _make_engine_with_numbers()
        engine.create_rfc("Test", "Quill")
        with self.assertRaises(ValueError):
            engine.advance_state(1, RFCState.ACCEPTED)

    def test_draft_to_rejected_is_invalid(self) -> None:
        engine = _make_engine_with_numbers()
        engine.create_rfc("Test", "Quill")
        with self.assertRaises(ValueError):
            engine.advance_state(1, RFCState.REJECTED)

    def test_draft_to_withdrawn_is_valid(self) -> None:
        engine = _make_engine_with_numbers()
        engine.create_rfc("Test", "Quill")
        result = engine.advance_state(1, RFCState.WITHDRAWN)
        self.assertEqual(result.state, RFCState.WITHDRAWN)

    def test_accepted_is_terminal(self) -> None:
        engine = _make_engine_with_numbers()
        rfc = engine.create_rfc("Test", "Quill")
        engine.submit_for_review(rfc.number)
        engine.advance_state(rfc.number, RFCState.DISCUSSION)
        engine.advance_state(rfc.number, RFCState.SYNTHESIS)
        _add_votes_for_consensus(engine, rfc.number)
        engine.advance_state(rfc.number, RFCState.ACCEPTED)
        with self.assertRaises(ValueError):
            engine.advance_state(rfc.number, RFCState.DISCUSSION)

    def test_rejected_is_terminal(self) -> None:
        engine = _make_engine_with_numbers()
        engine.create_rfc("Test", "Quill")
        engine.submit_for_review(1)
        engine.advance_state(1, RFCState.REJECTED)
        with self.assertRaises(ValueError):
            engine.advance_state(1, RFCState.DISCUSSION)

    def test_all_valid_transitions(self) -> None:
        """Enumerate every valid transition and confirm it works."""
        engine = _make_engine_with_numbers()
        valid = {
            RFCState.DRAFT: [RFCState.PROPOSAL, RFCState.WITHDRAWN],
            RFCState.PROPOSAL: [RFCState.DISCUSSION, RFCState.EVIDENCE,
                                RFCState.WITHDRAWN, RFCState.REJECTED],
            RFCState.DISCUSSION: [RFCState.SYNTHESIS, RFCState.EVIDENCE,
                                  RFCState.WITHDRAWN, RFCState.REJECTED],
            RFCState.EVIDENCE: [RFCState.DISCUSSION, RFCState.SYNTHESIS,
                                RFCState.WITHDRAWN, RFCState.REJECTED],
            RFCState.SYNTHESIS: [RFCState.ACCEPTED, RFCState.DISCUSSION,
                                 RFCState.WITHDRAWN, RFCState.REJECTED],
        }
        for start, targets in valid.items():
            for target in targets:
                eng = _make_engine_with_numbers()
                eng.create_rfc("T", "A")
                # Walk to start state
                _walk_to_state(eng, 1, start)
                eng.advance_state(1, target)
                self.assertEqual(eng.get_rfc(1).state, target,
                                 f"Failed: {start.value} -> {target.value}")

    def test_nonexistent_rfc_raises(self) -> None:
        engine = _make_engine_with_numbers()
        with self.assertRaises(KeyError):
            engine.advance_state(999, RFCState.PROPOSAL)


def _walk_to_state(engine: RFCEngine, num: int, target: RFCState) -> None:
    """Walk an RFC through the graph to reach *target* (for testing)."""
    path_map = {
        RFCState.DRAFT: [],
        RFCState.PROPOSAL: [RFCState.PROPOSAL],
        RFCState.DISCUSSION: [RFCState.PROPOSAL, RFCState.DISCUSSION],
        RFCState.EVIDENCE: [RFCState.PROPOSAL, RFCState.EVIDENCE],
        RFCState.SYNTHESIS: [RFCState.PROPOSAL, RFCState.DISCUSSION, RFCState.SYNTHESIS],
    }
    for state in path_map.get(target, []):
        if state == RFCState.PROPOSAL:
            engine.submit_for_review(num)
        else:
            engine.advance_state(num, state)


# ===================================================================
# 4. Supersession Tests
# ===================================================================

class TestSupersession(unittest.TestCase):

    def test_supersede_moves_old_to_superseded(self) -> None:
        engine = _make_engine_with_numbers()
        old = engine.create_rfc("Old RFC", "Quill")
        engine.submit_for_review(old.number)
        engine.advance_state(old.number, RFCState.DISCUSSION)
        engine.advance_state(old.number, RFCState.SYNTHESIS)
        _add_votes_for_consensus(engine, old.number)
        engine.advance_state(old.number, RFCState.ACCEPTED)

        new = engine.create_rfc("New RFC", "Quill")
        engine.submit_for_review(new.number)
        engine.advance_state(new.number, RFCState.DISCUSSION)
        engine.advance_state(new.number, RFCState.SYNTHESIS)
        _add_votes_for_consensus(engine, new.number)
        engine.advance_state(new.number, RFCState.ACCEPTED)

        engine.supersede(old.number, new.number)
        self.assertEqual(engine.get_rfc(old.number).state, RFCState.SUPERSEDED)
        self.assertEqual(engine.get_rfc(old.number).superseded_by, new.number)

    def test_supersede_requires_accepted_new(self) -> None:
        engine = _make_engine_with_numbers()
        old = engine.create_rfc("Old", "Quill")
        engine.submit_for_review(old.number)
        engine.advance_state(old.number, RFCState.DISCUSSION)
        engine.advance_state(old.number, RFCState.SYNTHESIS)
        _add_votes_for_consensus(engine, old.number)
        engine.advance_state(old.number, RFCState.ACCEPTED)

        new = engine.create_rfc("New", "Quill")
        # New is still in DRAFT
        with self.assertRaises(ValueError):
            engine.supersede(old.number, new.number)

    def test_supersede_nonexistent_raises(self) -> None:
        engine = _make_engine_with_numbers()
        with self.assertRaises(KeyError):
            engine.supersede(999, 888)


# ===================================================================
# 5. Conflict Detection Tests
# ===================================================================

class TestConflictDetection(unittest.TestCase):

    def test_opcode_overlap_detected(self) -> None:
        engine = _make_engine_with_numbers()
        rfc_a = engine.create_rfc(
            "Opcode Range A", "Agent-X",
            specification="Claim opcode 0x50-0x5F for agent communication.",
        )
        engine.submit_for_review(rfc_a.number)

        rfc_b = engine.create_rfc(
            "Opcode Range B", "Agent-Y",
            specification="Reserve opcode 0x58-0x60 for I/O operations.",
        )
        engine.submit_for_review(rfc_b.number)

        resolver = ConflictResolver(engine)
        conflicts = resolver.detect_conflicts()
        self.assertTrue(len(conflicts) >= 1)
        types = {c.conflict_type for c in conflicts}
        self.assertIn(ConflictType.OPCODE_OVERLAP, types)

    def test_semantic_conflict_detected(self) -> None:
        engine = _make_engine_with_numbers()
        rfc_a = engine.create_rfc(
            "Approach Alpha", "Agent-X",
            body="We should use approach alpha. This contradicts RFC-0002.",
        )
        engine.submit_for_review(rfc_a.number)

        rfc_b = engine.create_rfc(
            "Approach Beta", "Agent-Y",
            body="Approach beta is incompatible with RFC-0001.",
        )
        engine.submit_for_review(rfc_b.number)

        resolver = ConflictResolver(engine)
        conflicts = resolver.detect_conflicts()
        types = {c.conflict_type for c in conflicts}
        self.assertIn(ConflictType.SEMANTIC_CONFLICT, types)

    def test_scope_overlap_detected(self) -> None:
        engine = _make_engine_with_numbers()
        rfc_a = engine.create_rfc(
            "ISA Extension", "Agent-X",
            body="This RFC proposes ISA changes to the VM opcode format, "
                 "affecting the bytecode interpreter and SIGNAL language.",
        )
        engine.submit_for_review(rfc_a.number)

        rfc_b = engine.create_rfc(
            "VM Overhaul", "Agent-Y",
            body="A VM overhaul that modifies the ISA, bytecode format, "
                 "and agent communication protocol, plus A2A protocol changes.",
        )
        engine.submit_for_review(rfc_b.number)

        resolver = ConflictResolver(engine)
        conflicts = resolver.detect_conflicts()
        types = {c.conflict_type for c in conflicts}
        self.assertIn(ConflictType.SCOPE_OVERLAP, types)

    def test_no_conflict_for_unrelated_rfcs(self) -> None:
        engine = _make_engine_with_numbers()
        rfc_a = engine.create_rfc(
            "Fleet Naming Convention", "Agent-X",
            body="How we name fleet agents.",
        )
        engine.submit_for_review(rfc_a.number)

        rfc_b = engine.create_rfc(
            "Documentation Standards", "Agent-Y",
            body="How we write documentation.",
        )
        engine.submit_for_review(rfc_b.number)

        resolver = ConflictResolver(engine)
        conflicts = resolver.detect_conflicts()
        self.assertEqual(len(conflicts), 0)

    def test_detect_conflicts_scopes_specific_numbers(self) -> None:
        """Pass explicit RFC numbers to detect_conflicts."""
        engine = _make_engine_with_numbers(start=100)
        rfc_a = engine.create_rfc(
            "Opcode Claim", "Agent-X",
            specification="Reserve 0xA0-0xAF.",
        )
        engine.submit_for_review(rfc_a.number)
        rfc_b = engine.create_rfc(
            "Another Claim", "Agent-Y",
            specification="Use 0xA5-0xB0.",
        )
        engine.submit_for_review(rfc_b.number)
        # Add an unrelated one we don't want scanned
        rfc_c = engine.create_rfc("Unrelated", "Agent-Z")
        engine.submit_for_review(rfc_c.number)

        resolver = ConflictResolver(engine)
        conflicts = resolver.detect_conflicts([rfc_a.number, rfc_b.number])
        self.assertTrue(len(conflicts) >= 1)


# ===================================================================
# 6. Conflict Resolution and Merge Tests
# ===================================================================

class TestConflictResolution(unittest.TestCase):

    def test_propose_resolution_creates_synthesis(self) -> None:
        engine = _make_engine_with_numbers()
        rfc_a = engine.create_rfc(
            "Approach A", "Agent-X",
            specification="Use 0x50-0x5F.",
            open_questions=["Is this range big enough?"],
        )
        engine.submit_for_review(rfc_a.number)

        rfc_b = engine.create_rfc(
            "Approach B", "Agent-Y",
            specification="Use 0x58-0x68.",
            open_questions=["Should we extend further?"],
        )
        engine.submit_for_review(rfc_b.number)

        resolver = ConflictResolver(engine)
        conflicts = resolver.detect_conflicts()
        self.assertTrue(len(conflicts) >= 1)

        synthesis = resolver.propose_resolution(conflicts[0], preferred_rfc=rfc_a.number)
        self.assertEqual(synthesis.state, RFCState.SYNTHESIS)
        self.assertIn("Synthesis", synthesis.title)
        self.assertIn("RFC-1", synthesis.body)
        self.assertIn("RFC-2", synthesis.body)
        # Should have merged open questions
        self.assertTrue(len(synthesis.open_questions) >= 2)

    def test_merge_rfcs_requires_accepted_synthesis(self) -> None:
        engine = _make_engine_with_numbers()
        rfc_a = engine.create_rfc(
            "A", "Agent-X", specification="0x50-0x5F."
        )
        engine.submit_for_review(rfc_a.number)
        rfc_b = engine.create_rfc(
            "B", "Agent-Y", specification="0x58-0x68."
        )
        engine.submit_for_review(rfc_b.number)

        resolver = ConflictResolver(engine)
        conflicts = resolver.detect_conflicts()
        synthesis = resolver.propose_resolution(conflicts[0], rfc_a.number)

        # Synthesis is in SYNTHESIS, not ACCEPTED — merge should fail
        with self.assertRaises(ValueError):
            resolver.merge_rfcs(conflicts[0], synthesis.number)

    def test_merge_rfcs_succeeds_when_accepted(self) -> None:
        engine = _make_engine_with_numbers()
        rfc_a = engine.create_rfc(
            "A", "Agent-X", specification="0x50-0x5F."
        )
        engine.submit_for_review(rfc_a.number)
        rfc_b = engine.create_rfc(
            "B", "Agent-Y", specification="0x58-0x68."
        )
        engine.submit_for_review(rfc_b.number)

        resolver = ConflictResolver(engine)
        conflicts = resolver.detect_conflicts()
        synthesis = resolver.propose_resolution(conflicts[0], rfc_a.number)

        # Approve the synthesis
        _add_votes_for_consensus(engine, synthesis.number)
        engine.advance_state(synthesis.number, RFCState.ACCEPTED)

        resolver.merge_rfcs(conflicts[0], synthesis.number)

        self.assertEqual(engine.get_rfc(rfc_a.number).state, RFCState.SUPERSEDED)
        self.assertEqual(engine.get_rfc(rfc_b.number).state, RFCState.SUPERSEDED)


# ===================================================================
# 7. Rejection Tests
# ===================================================================

class TestRejection(unittest.TestCase):

    def test_reject_from_proposal(self) -> None:
        engine = _make_engine_with_numbers()
        rfc = engine.create_rfc("Bad Idea", "Quill")
        engine.submit_for_review(rfc.number)
        engine.advance_state(rfc.number, RFCState.REJECTED)
        self.assertEqual(engine.get_rfc(rfc.number).state, RFCState.REJECTED)
        self.assertEqual(len(engine.get_open_rfcs()), 0)
        self.assertEqual(len(engine.get_canonical_rfcs()), 0)

    def test_reject_from_synthesis(self) -> None:
        engine = _make_engine_with_numbers()
        rfc = engine.create_rfc("Failed Synthesis", "Quill")
        engine.submit_for_review(rfc.number)
        engine.advance_state(rfc.number, RFCState.DISCUSSION)
        engine.advance_state(rfc.number, RFCState.SYNTHESIS)
        engine.advance_state(rfc.number, RFCState.REJECTED)
        self.assertEqual(engine.get_rfc(rfc.number).state, RFCState.REJECTED)

    def test_rejected_rfcs_not_in_open_or_canonical(self) -> None:
        engine = _make_engine_with_numbers()
        rfc = engine.create_rfc("Doomed", "Quill")
        engine.submit_for_review(rfc.number)
        engine.advance_state(rfc.number, RFCState.REJECTED)
        self.assertNotIn(
            rfc.number,
            [r.number for r in engine.get_open_rfcs()]
        )
        self.assertNotIn(
            rfc.number,
            [r.number for r in engine.get_canonical_rfcs()]
        )


# ===================================================================
# 8. Query Tests
# ===================================================================

class TestQueries(unittest.TestCase):

    def test_get_open_rfcs_includes_proposal_and_discussion(self) -> None:
        engine = _make_engine_with_numbers()
        p = engine.create_rfc("Proposal RFC", "A")
        engine.submit_for_review(p.number)
        d = engine.create_rfc("Discussion RFC", "B")
        engine.submit_for_review(d.number)
        engine.advance_state(d.number, RFCState.DISCUSSION)
        # Add an EVIDENCE one (not open)
        e = engine.create_rfc("Evidence RFC", "C")
        engine.submit_for_review(e.number)
        engine.advance_state(e.number, RFCState.EVIDENCE)

        open_rfcs = engine.get_open_rfcs()
        open_nums = {r.number for r in open_rfcs}
        self.assertIn(p.number, open_nums)
        self.assertIn(d.number, open_nums)
        self.assertNotIn(e.number, open_nums)

    def test_get_canonical_rfcs(self) -> None:
        engine = _make_engine_with_numbers()
        a = engine.create_rfc("Accepted RFC", "Quill")
        engine.submit_for_review(a.number)
        engine.advance_state(a.number, RFCState.DISCUSSION)
        engine.advance_state(a.number, RFCState.SYNTHESIS)
        _add_votes_for_consensus(engine, a.number)
        engine.advance_state(a.number, RFCState.ACCEPTED)

        canonical = engine.get_canonical_rfcs()
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0].number, a.number)

    def test_get_rfc_nonexistent_returns_none(self) -> None:
        engine = _make_engine_with_numbers()
        self.assertIsNone(engine.get_rfc(999))


# ===================================================================
# 9. RFC Serialization Round-Trip
# ===================================================================

class TestSerialization(unittest.TestCase):

    def test_to_dict_round_trip(self) -> None:
        engine = _make_engine_with_numbers()
        engine.create_rfc(
            "Serialize Test", "Quill",
            body="Body text.",
            motivation="Motivation text.",
            specification="Spec text.",
            open_questions=["Q1"],
        )
        engine.submit_for_review(1)
        engine.cast_vote(1, "Oracle1", VotePosition.APPROVE, "+1")

        # Re-fetch to get the updated copy with votes
        rfc = engine.get_rfc(1)
        assert rfc is not None
        data = rfc.to_dict()
        restored = RFC.from_dict(data)

        self.assertEqual(restored.number, rfc.number)
        self.assertEqual(restored.title, rfc.title)
        self.assertEqual(restored.state, rfc.state)
        self.assertEqual(restored.body, rfc.body)
        self.assertEqual(restored.motivation, rfc.motivation)
        self.assertEqual(restored.specification, rfc.specification)
        self.assertEqual(restored.open_questions, rfc.open_questions)
        self.assertEqual(len(restored.votes), 1)
        self.assertEqual(restored.votes[0].voter, "Oracle1")
        self.assertEqual(restored.votes[0].position, VotePosition.APPROVE)


# ===================================================================
# 10. Git Persistence Round-Trip Tests
# ===================================================================

class TestGitPersistence(unittest.TestCase):

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.persistence = GitPersistence(self.tmpdir, auto_commit=False)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_and_load_rfc(self) -> None:
        engine = _make_engine_with_numbers()
        rfc = engine.create_rfc(
            "Persistence Test", "Quill",
            motivation="Testing persistence.",
            specification="Spec goes here.",
            open_questions=["Does it work?"],
        )
        engine.submit_for_review(rfc.number)
        engine.cast_vote(rfc.number, "Oracle1", VotePosition.APPROVE, "Yes")

        # Re-fetch from engine to get updated state and votes
        rfc = engine.get_rfc(rfc.number)
        assert rfc is not None

        # Save
        path = self.persistence.save_rfc(rfc)
        self.assertTrue(path.exists())
        self.assertIn("PROPOSALS", str(path))

        # Load
        loaded = self.persistence.load_rfc(rfc.number)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.number, rfc.number)
        self.assertEqual(loaded.title, "Persistence Test")
        self.assertEqual(loaded.author, "Quill")
        self.assertEqual(loaded.state, RFCState.PROPOSAL)
        self.assertEqual(loaded.motivation, "Testing persistence.")
        self.assertEqual(loaded.specification, "Spec goes here.")
        self.assertEqual(loaded.open_questions, ["Does it work?"])
        self.assertEqual(len(loaded.votes), 1)

    def test_move_rfc_between_directories(self) -> None:
        engine = _make_engine_with_numbers()
        rfc = engine.create_rfc("Moving RFC", "Quill")
        engine.submit_for_review(rfc.number)

        # Save in PROPOSALS
        path1 = self.persistence.save_rfc(rfc)
        self.assertIn("PROPOSALS", str(path1))

        # Move to DISCUSSION
        path2 = self.persistence.move_rfc(rfc, RFCState.DISCUSSION)
        self.assertIn("DISCUSSION", str(path2))
        # Old file should be gone
        self.assertFalse(path1.exists())

    def test_load_all_rfcs(self) -> None:
        engine = _make_engine_with_numbers()
        rfc1 = engine.create_rfc("First", "A")
        engine.submit_for_review(rfc1.number)
        rfc2 = engine.create_rfc("Second", "B")
        engine.submit_for_review(rfc2.number)

        self.persistence.save_rfc(rfc1)
        self.persistence.save_rfc(rfc2)

        loaded = self.persistence.load_all_rfcs()
        numbers = {r.number for r in loaded}
        self.assertEqual(numbers, {1, 2})

    def test_load_into_engine(self) -> None:
        # Create an engine, save RFCs, load into a fresh engine
        engine1 = _make_engine_with_numbers()
        engine1.create_rfc("Persisted A", "Quill")
        engine1.submit_for_review(1)
        engine1.create_rfc("Persisted B", "Quill")
        engine1.submit_for_review(2)
        engine1.cast_vote(2, "Oracle1", VotePosition.APPROVE)

        # Re-fetch to get updated state/votes, then save
        rfc1 = engine1.get_rfc(1)
        rfc2 = engine1.get_rfc(2)
        assert rfc1 is not None and rfc2 is not None
        self.persistence.save_rfc(rfc1)
        self.persistence.save_rfc(rfc2)

        # Fresh engine
        engine2 = _make_engine_with_numbers()
        count = self.persistence.load_into_engine(engine2)
        self.assertEqual(count, 2)
        self.assertIsNotNone(engine2.get_rfc(1))
        self.assertIsNotNone(engine2.get_rfc(2))
        r = engine2.get_rfc(2)
        assert r is not None
        self.assertEqual(len(r.votes), 1)

    def test_generate_index(self) -> None:
        engine = _make_engine_with_numbers()
        rfc = engine.create_rfc("Indexed RFC", "Quill")
        engine.submit_for_review(rfc.number)

        self.persistence.save_rfc(rfc)
        index = self.persistence.generate_index(engine)

        self.assertIn("rfcs", index)
        self.assertEqual(len(index["rfcs"]), 1)
        self.assertEqual(index["rfcs"][0]["number"], 1)
        self.assertEqual(index["rfcs"][0]["title"], "Indexed RFC")
        self.assertEqual(index["rfcs"][0]["status"], "PROPOSAL")
        self.assertEqual(index["next_number"], 2)

        # Index file should exist on disk
        self.assertTrue(self.persistence._index_path.exists())

    def test_accepted_rfc_saved_to_canonical(self) -> None:
        engine = _make_engine_with_numbers()
        engine.create_rfc("Canonical RFC", "Quill")
        engine.submit_for_review(1)
        engine.advance_state(1, RFCState.DISCUSSION)
        engine.advance_state(1, RFCState.SYNTHESIS)
        _add_votes_for_consensus(engine, 1)
        engine.advance_state(1, RFCState.ACCEPTED)

        rfc = engine.get_rfc(1)
        assert rfc is not None
        path = self.persistence.save_rfc(rfc)
        self.assertIn("CANONICAL", str(path))

    def test_archive_rfc_saved_to_archive(self) -> None:
        engine = _make_engine_with_numbers()
        engine.create_rfc("Withdrawn RFC", "Quill")
        engine.advance_state(1, RFCState.WITHDRAWN)

        rfc = engine.get_rfc(1)
        assert rfc is not None
        path = self.persistence.save_rfc(rfc)
        self.assertIn("ARCHIVE", str(path))

    def test_full_persistence_round_trip(self) -> None:
        """
        End-to-end: create -> save -> load into fresh engine ->
        verify state, votes, consensus, operations.
        """
        # Engine 1: create and save
        eng1 = _make_engine_with_numbers()
        eng1.create_rfc(
            "Round Trip", "Quill",
            motivation="Testing full round trip.",
            specification="Spec.",
            open_questions=["Q?"],
        )
        eng1.submit_for_review(1)
        eng1.cast_vote(1, "Oracle1", VotePosition.APPROVE)
        eng1.cast_vote(1, "Agent-A", VotePosition.APPROVE)
        eng1.cast_vote(1, "Agent-B", VotePosition.APPROVE)

        # Re-fetch to get updated copy
        rfc = eng1.get_rfc(1)
        assert rfc is not None
        self.persistence.save_rfc(rfc)
        self.persistence.generate_index(eng1)

        # Engine 2: load from persistence
        eng2 = _make_engine_with_numbers()
        self.persistence.load_into_engine(eng2)

        # Verify
        loaded = eng2.get_rfc(1)
        assert loaded is not None
        self.assertEqual(loaded.state, RFCState.PROPOSAL)
        self.assertEqual(loaded.title, "Round Trip")
        self.assertEqual(loaded.motivation, "Testing full round trip.")
        self.assertEqual(loaded.open_questions, ["Q?"])
        self.assertEqual(len(loaded.votes), 3)
        self.assertTrue(eng2.check_consensus(1))


# ===================================================================
# Run
# ===================================================================

if __name__ == "__main__":
    unittest.main()

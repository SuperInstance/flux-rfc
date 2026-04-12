"""
Extended tests for flux-rfc engine, conflict resolver, and persistence.

Covers edge cases not exercised by the main test suite:
- Markdown parsing (header extraction, section boundaries, vote tables)
- GitPersistence load_rfc by number
- Conflict resolution edge cases
- RFC enumeration queries
- load_rfc into engine with high-numbered RFCs
"""

from __future__ import annotations

import tempfile
import shutil
import unittest
from pathlib import Path

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
)
from src.persistence.git_persistence import (
    GitPersistence,
    _parse_rfc_markdown,
    _slugify,
    _rfc_filename,
    _rfc_to_markdown,
)


# ===================================================================
# Markdown Parsing Tests
# ===================================================================

class TestMarkdownParsing(unittest.TestCase):
    """Tests for the _parse_rfc_markdown helper."""

    def test_parse_minimal_rfc(self):
        text = (
            "# RFC 0042: Minimal\n"
            "\n"
            "**Author:** Alice\n"
            "**Date:** 2026-01-01\n"
            "**Status:** DRAFT\n"
        )
        data = _parse_rfc_markdown(text)
        self.assertEqual(data["number"], 42)
        self.assertEqual(data["title"], "Minimal")
        self.assertEqual(data["author"], "Alice")
        self.assertEqual(data["state"], "DRAFT")

    def test_parse_rfc_with_sections(self):
        text = (
            "# RFC 0007: Sections Test\n"
            "\n"
            "**Author:** Bob\n"
            "**Date:** 2026-04-12\n"
            "**Status:** PROPOSAL\n"
            "\n"
            "## Motivation\n"
            "\n"
            "We need this because reasons.\n"
            "\n"
            "## Body\n"
            "\n"
            "Here is the body text.\n"
            "\n"
            "## Specification\n"
            "\n"
            "The spec says X.\n"
        )
        data = _parse_rfc_markdown(text)
        self.assertEqual(data["number"], 7)
        self.assertEqual(data["motivation"], "We need this because reasons.")
        self.assertEqual(data["body"], "Here is the body text.")
        self.assertEqual(data["specification"], "The spec says X.")

    def test_parse_open_questions(self):
        text = (
            "# RFC 0010: Questions\n"
            "\n"
            "**Author:** Carol\n"
            "**Date:** 2026-04-12\n"
            "**Status:** DISCUSSION\n"
            "\n"
            "## Open Questions\n"
            "\n"
            "- Should we do X?\n"
            "- What about Y?\n"
            "- How to handle Z?\n"
        )
        data = _parse_rfc_markdown(text)
        self.assertEqual(len(data["open_questions"]), 3)
        self.assertIn("Should we do X?", data["open_questions"])
        self.assertIn("What about Y?", data["open_questions"])
        self.assertIn("How to handle Z?", data["open_questions"])

    def test_parse_vote_table(self):
        text = (
            "# RFC 0020: Voted\n"
            "\n"
            "**Author:** Dave\n"
            "**Date:** 2026-04-12\n"
            "**Status:** SYNTHESIS\n"
            "\n"
            "## Fleet Votes\n"
            "\n"
            "| Agent | Vote | Comment |\n"
            "|-------|------|---------|\n"
            "| Oracle1 | APPROVE | LGTM |\n"
            "| Agent-A | REJECT | Concerns |\n"
            "| Agent-B | ABSTAIN | Need info |\n"
        )
        data = _parse_rfc_markdown(text)
        votes = data["votes"]
        self.assertEqual(len(votes), 3)
        self.assertEqual(votes[0]["voter"], "Oracle1")
        self.assertEqual(votes[0]["position"], "APPROVE")
        self.assertEqual(votes[1]["position"], "REJECT")
        self.assertEqual(votes[2]["position"], "ABSTAIN")

    def test_parse_objection_maps_to_reject(self):
        text = (
            "# RFC 0030: Objected\n"
            "\n"
            "**Author:** Eve\n"
            "**Date:** 2026-04-12\n"
            "**Status:** PROPOSAL\n"
            "\n"
            "## Fleet Votes\n"
            "\n"
            "| Agent | Vote | Comment |\n"
            "|-------|------|---------|\n"
            "| Agent-X | OBJECTION | Wrong approach |\n"
        )
        data = _parse_rfc_markdown(text)
        self.assertEqual(len(data["votes"]), 1)
        self.assertEqual(data["votes"][0]["position"], "REJECT")

    def test_parse_superseded_by(self):
        text = (
            "# RFC 0005: Old\n"
            "\n"
            "**Author:** Frank\n"
            "**Date:** 2026-04-12\n"
            "**Status:** SUPERSEDED\n"
            "**Obsoletes:** RFC-0010\n"
        )
        data = _parse_rfc_markdown(text)
        self.assertEqual(data.get("superseded_by"), 10)

    def test_parse_empty_text(self):
        data = _parse_rfc_markdown("")
        self.assertNotIn("number", data)

    def test_parse_non_rfc_markdown(self):
        data = _parse_rfc_markdown("# Some random document\n\nNo RFC header here.")
        self.assertNotIn("number", data)


# ===================================================================
# Filename / Slugify Helpers
# ===================================================================

class TestFilenameHelpers(unittest.TestCase):

    def test_slugify_simple(self):
        self.assertEqual(_slugify("Hello World"), "hello-world")

    def test_slugify_special_chars(self):
        self.assertEqual(_slugify("RFC: ISA v2 — The Next Gen!"), "rfc-isa-v2-the-next-gen")

    def test_rfc_filename(self):
        rfc = RFC(number=42, title="Test RFC Title", author="A")
        self.assertEqual(_rfc_filename(rfc), "rfc-0042-test-rfc-title.md")

    def test_rfc_filename_long_title_truncated(self):
        rfc = RFC(number=1, title="A" * 100, author="A")
        filename = _rfc_filename(rfc)
        # slug truncated to 60 chars then prefixed with rfc-0001-
        self.assertTrue(filename.startswith("rfc-0001-"))
        self.assertTrue(len(filename) < 100)


# ===================================================================
# Markdown Generation Tests
# ===================================================================

class TestMarkdownGeneration(unittest.TestCase):

    def test_rfc_to_markdown_contains_header(self):
        rfc = RFC(number=1, title="Test", author="Alice", state=RFCState.PROPOSAL)
        md = _rfc_to_markdown(rfc)
        self.assertIn("# RFC 0001: Test", md)
        self.assertIn("**Author:** Alice", md)
        self.assertIn("**Status:** PROPOSAL", md)

    def test_rfc_to_markdown_with_votes(self):
        rfc = RFC(number=2, title="Voted", author="Bob", state=RFCState.SYNTHESIS)
        rfc.votes = [
            Vote(voter="Oracle1", position=VotePosition.APPROVE, comment="Yes"),
            Vote(voter="Agent-A", position=VotePosition.REJECT, comment="No"),
        ]
        md = _rfc_to_markdown(rfc)
        self.assertIn("## Fleet Votes", md)
        self.assertIn("Oracle1", md)
        self.assertIn("APPROVE", md)
        self.assertIn("Agent-A", md)
        self.assertIn("REJECT", md)

    def test_rfc_to_markdown_with_open_questions(self):
        rfc = RFC(
            number=3, title="Questions", author="Carol",
            open_questions=["Q1?", "Q2?"],
        )
        md = _rfc_to_markdown(rfc)
        self.assertIn("## Open Questions", md)
        self.assertIn("- Q1?", md)
        self.assertIn("- Q2?", md)

    def test_rfc_to_markdown_superseded_by(self):
        rfc = RFC(
            number=5, title="Old", author="Dave",
            state=RFCState.SUPERSEDED, superseded_by=10,
        )
        md = _rfc_to_markdown(rfc)
        self.assertIn("RFC-0010", md)

    def test_rfc_to_markdown_obsoletes_none(self):
        rfc = RFC(number=6, title="Current", author="Eve")
        md = _rfc_to_markdown(rfc)
        self.assertIn("**Obsoletes:** None", md)


# ===================================================================
# Persistence Edge Cases
# ===================================================================

class TestPersistenceEdgeCases(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.persistence = GitPersistence(self.tmpdir, auto_commit=False)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_nonexistent_rfc_returns_none(self):
        result = self.persistence.load_rfc(9999)
        self.assertIsNone(result)

    def test_load_all_from_empty_repo(self):
        rfcs = self.persistence.load_all_rfcs()
        self.assertEqual(len(rfcs), 0)

    def test_load_into_engine_empty(self):
        engine = RFCEngine()
        count = self.persistence.load_into_engine(engine)
        self.assertEqual(count, 0)

    def test_save_and_load_round_trip_preserves_votes(self):
        engine = RFCEngine()
        rfc = engine.create_rfc("Vote Test", "Author")
        engine.submit_for_review(rfc.number)
        engine.cast_vote(rfc.number, "A", VotePosition.APPROVE, "+1")
        engine.cast_vote(rfc.number, "B", VotePosition.ABSTAIN, "meh")
        rfc = engine.get_rfc(rfc.number)
        assert rfc is not None

        self.persistence.save_rfc(rfc)
        loaded = self.persistence.load_rfc(rfc.number)
        assert loaded is not None
        self.assertEqual(len(loaded.votes), 2)
        self.assertEqual(loaded.votes[0].voter, "A")
        self.assertEqual(loaded.votes[0].position, VotePosition.APPROVE)
        self.assertEqual(loaded.votes[1].voter, "B")
        self.assertEqual(loaded.votes[1].position, VotePosition.ABSTAIN)

    def test_generate_index_without_engine(self):
        """generate_index should work from filesystem when no engine given."""
        engine = RFCEngine()
        rfc = engine.create_rfc("FS Index", "Author")
        engine.submit_for_review(rfc.number)
        rfc = engine.get_rfc(rfc.number)
        assert rfc is not None
        self.persistence.save_rfc(rfc)

        # Generate index from filesystem (no engine arg)
        index = self.persistence.generate_index()
        self.assertEqual(len(index["rfcs"]), 1)
        self.assertEqual(index["rfcs"][0]["title"], "FS Index")


# ===================================================================
# Conflict Resolution Edge Cases
# ===================================================================

class TestConflictResolutionEdgeCases(unittest.TestCase):

    def test_propose_resolution_invalid_preferred_raises(self):
        engine = RFCEngine()
        rfc_a = engine.create_rfc("A", "X", specification="0x50-0x5F.")
        engine.submit_for_review(rfc_a.number)
        rfc_b = engine.create_rfc("B", "Y", specification="0x58-0x68.")
        engine.submit_for_review(rfc_b.number)

        resolver = ConflictResolver(engine)
        conflicts = resolver.detect_conflicts()
        self.assertTrue(len(conflicts) >= 1)

        with self.assertRaises(ValueError):
            resolver.propose_resolution(conflicts[0], preferred_rfc=999)

    def test_detect_conflicts_empty_engine(self):
        engine = RFCEngine()
        resolver = ConflictResolver(engine)
        conflicts = resolver.detect_conflicts()
        self.assertEqual(len(conflicts), 0)

    def test_detect_conflicts_single_rfc(self):
        engine = RFCEngine()
        rfc = engine.create_rfc("Solo", "A", specification="0x50-0x5F.")
        engine.submit_for_review(rfc.number)
        resolver = ConflictResolver(engine)
        conflicts = resolver.detect_conflicts()
        self.assertEqual(len(conflicts), 0)

    def test_detect_conflicts_only_terminal_rfcs(self):
        """Terminal-state RFCs should not be scanned by default."""
        engine = RFCEngine()
        rfc = engine.create_rfc("Accepted", "A")
        engine.submit_for_review(rfc.number)
        engine.advance_state(rfc.number, RFCState.DISCUSSION)
        engine.advance_state(rfc.number, RFCState.SYNTHESIS)
        engine.cast_vote(rfc.number, "O1", VotePosition.APPROVE)
        engine.cast_vote(rfc.number, "O2", VotePosition.APPROVE)
        engine.cast_vote(rfc.number, "O3", VotePosition.APPROVE)
        engine.advance_state(rfc.number, RFCState.ACCEPTED)

        resolver = ConflictResolver(engine)
        conflicts = resolver.detect_conflicts()
        self.assertEqual(len(conflicts), 0)

    def test_propose_resolution_with_b_as_preferred(self):
        """Verify that choosing RFC-B as preferred swaps the synthesis."""
        engine = RFCEngine()
        rfc_a = engine.create_rfc("A", "X", specification="0x50-0x5F.", open_questions=["Q-A"])
        engine.submit_for_review(rfc_a.number)
        rfc_b = engine.create_rfc("B", "Y", specification="0x58-0x68.", open_questions=["Q-B"])
        engine.submit_for_review(rfc_b.number)

        resolver = ConflictResolver(engine)
        conflicts = resolver.detect_conflicts()
        synthesis = resolver.propose_resolution(conflicts[0], preferred_rfc=rfc_b.number)
        assert synthesis is not None
        # B should be the preferred one in the synthesis body
        self.assertIn("RFC-2", synthesis.body)
        self.assertIn("Preferred Position", synthesis.body)


# ===================================================================
# Engine Query Edge Cases
# ===================================================================

class TestEngineQueryEdgeCases(unittest.TestCase):

    def test_all_rfcs_empty(self):
        engine = RFCEngine()
        self.assertEqual(len(engine.all_rfcs()), 0)

    def test_all_rfcs_returns_all(self):
        engine = RFCEngine()
        engine.create_rfc("First", "A")
        engine.create_rfc("Second", "B")
        engine.create_rfc("Third", "C")
        self.assertEqual(len(engine.all_rfcs()), 3)

    def test_next_number_property(self):
        engine = RFCEngine(next_number=100)
        self.assertEqual(engine.next_number, 100)
        engine.create_rfc("Test", "A")
        self.assertEqual(engine.next_number, 101)

    def test_load_rfc_updates_next_number(self):
        engine = RFCEngine(next_number=1)
        rfc = RFC(number=50, title="Loaded", author="A")
        engine.load_rfc(rfc)
        self.assertEqual(engine.next_number, 51)

    def test_get_open_rfcs_excludes_evidence_and_synthesis(self):
        engine = RFCEngine()
        p = engine.create_rfc("Proposal", "A")
        engine.submit_for_review(p.number)
        e = engine.create_rfc("Evidence", "B")
        engine.submit_for_review(e.number)
        engine.advance_state(e.number, RFCState.EVIDENCE)

        open_rfcs = engine.get_open_rfcs()
        nums = {r.number for r in open_rfcs}
        self.assertIn(p.number, nums)
        self.assertNotIn(e.number, nums)


if __name__ == "__main__":
    unittest.main()

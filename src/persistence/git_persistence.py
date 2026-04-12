"""
git_persistence: Git-backed persistence for RFCs.

Reads/writes RFCs as markdown files in the directory layout described in
SCHEMA.md, and maintains a ``registry/rfcs.json`` index file.

Directory layout (per SCHEMA.md):

    rfc/PROPOSALS/     — PROPOSAL state
    rfc/EVIDENCE/      — EVIDENCE state
    rfc/DISCUSSION/    — DISCUSSION state
    rfc/SYNTHESIS/     — SYNTHESIS state
    rfc/CANONICAL/     — ACCEPTED state
    rfc/ARCHIVE/       — REJECTED, SUPERSEDED, WITHDRAWN
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..engine.rfc_engine import (
    RFC,
    RFCState,
    RFCEngine,
    VotePosition,
)


# ---------------------------------------------------------------------------
# Mapping: RFCState → directory name
# ---------------------------------------------------------------------------

_STATE_DIR_MAP: Dict[RFCState, str] = {
    RFCState.DRAFT: "PROPOSALS",
    RFCState.PROPOSAL: "PROPOSALS",
    RFCState.DISCUSSION: "DISCUSSION",
    RFCState.EVIDENCE: "EVIDENCE",
    RFCState.SYNTHESIS: "SYNTHESIS",
    RFCState.ACCEPTED: "CANONICAL",
    RFCState.REJECTED: "ARCHIVE",
    RFCState.SUPERSEDED: "ARCHIVE",
    RFCState.WITHDRAWN: "ARCHIVE",
}


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def _slugify(title: str) -> str:
    """Convert a title to a kebab-case slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return re.sub(r"-+", "-", slug)


def _rfc_filename(rfc: RFC) -> str:
    """Return the filename for an RFC, e.g. ``rfc-0001-my-title.md``."""
    slug = _slugify(rfc.title)[:60]  # keep it reasonable
    return f"rfc-{rfc.number:04d}-{slug}.md"


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------

def _rfc_to_markdown(rfc: RFC) -> str:
    """Render an RFC as a markdown document following TEMPLATE.md."""
    status = rfc.state.value
    obsoletes = f"RFC-{rfc.superseded_by:04d}" if rfc.superseded_by else "None"

    lines = [
        f"# RFC {rfc.number:04d}: {rfc.title}",
        "",
        f"**Author:** {rfc.author}",
        f"**Date:** {rfc.created_at.strftime('%Y-%m-%d')}",
        f"**Status:** {status}",
        f"**Obsoletes:** {obsoletes}",
        "",
        "---",
        "",
    ]

    if rfc.motivation:
        lines += [
            "## Motivation",
            "",
            rfc.motivation,
            "",
        ]

    if rfc.body:
        lines += [
            "## Body",
            "",
            rfc.body,
            "",
        ]

    if rfc.specification:
        lines += [
            "## Specification",
            "",
            rfc.specification,
            "",
        ]

    if rfc.open_questions:
        lines.append("## Open Questions")
        lines.append("")
        for q in rfc.open_questions:
            lines.append(f"- {q}")
        lines.append("")

    if rfc.votes:
        lines += [
            "## Fleet Votes",
            "",
            "| Agent | Vote | Comment |",
            "|-------|------|---------|",
        ]
        for v in rfc.votes:
            escaped_comment = v.comment.replace("|", "\\|")
            lines.append(
                f"| {v.voter} | {v.position.value} | {escaped_comment} |"
            )
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown → RFC (lightweight parser)
# ---------------------------------------------------------------------------

_RFC_HEADER_RE = re.compile(
    r"^#\s+RFC\s+(\d+):\s+(.+)$", re.MULTILINE
)


def _parse_rfc_markdown(text: str) -> Dict[str, Any]:
    """Parse a markdown RFC into a dict matching ``RFC.to_dict`` shape."""
    data: Dict[str, Any] = {}

    # Title / number
    m = _RFC_HEADER_RE.search(text)
    if m:
        data["number"] = int(m.group(1))
        data["title"] = m.group(2).strip()

    # Metadata fields (simple regex)
    for field_name, pattern in [
        ("author", r"\*\*Author:\*\*\s*(.+)"),
        ("date", r"\*\*Date:\*\*\s*(.+)"),
        ("status", r"\*\*Status:\*\*\s*(.+)"),
    ]:
        fm = re.search(pattern, text)
        if fm:
            if field_name == "number":
                data["number"] = int(fm.group(1).strip())
            elif field_name == "status":
                data["state"] = fm.group(1).strip()
            else:
                data[field_name] = fm.group(1).strip()

    # Parse section contents
    for section, key in [
        ("Motivation", "motivation"),
        ("Body", "body"),
        ("Specification", "specification"),
    ]:
        sec_match = re.search(
            rf"## {re.escape(section)}\s*\n(.*?)(?=\n## |\Z)",
            text,
            re.DOTALL,
        )
        if sec_match:
            data[key] = sec_match.group(1).strip()

    # Open questions
    oq_match = re.search(
        r"## Open Questions\s*\n(.*?)(?=\n## |\Z)", text, re.DOTALL
    )
    if oq_match:
        questions = [
            line.lstrip("- ").strip()
            for line in oq_match.group(1).strip().splitlines()
            if line.strip().startswith("-")
        ]
        data["open_questions"] = questions

    # Superseded by
    obs_match = re.search(r"\*\*Obsoletes:\*\*\s*RFC-(\d+)", text)
    if obs_match:
        data["superseded_by"] = int(obs_match.group(1))

    # Parse votes from table
    data["votes"] = []
    vote_section = re.search(
        r"## Fleet Votes\s*\n.*?\|[-| ]+\|(?:\n(\|.*\|)*)", text, re.DOTALL
    )
    if vote_section:
        for vline in vote_section.group(0).splitlines():
            cells = [c.strip() for c in vline.strip().strip("|").split("|")]
            if len(cells) >= 3 and cells[1] in (
                "APPROVE", "REJECT", "ABSTAIN", "DEFER", "OBJECTION"
            ):
                position = cells[1]
                if position == "OBJECTION":
                    position = "REJECT"
                data["votes"].append({
                    "voter": cells[0],
                    "position": position,
                    "comment": cells[2] if len(cells) > 2 else "",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

    return data


# ---------------------------------------------------------------------------
# GitPersistence
# ---------------------------------------------------------------------------

class GitPersistence:
    """
    Reads and writes RFCs as markdown files in the SCHEMA.md directory
    layout, maintaining ``registry/rfcs.json`` as the index.

    Parameters
    ----------
    repo_root
        Absolute path to the flux-rfc repository root.
    auto_commit
        If True, every write operation runs ``git add`` + ``git commit``.
        Disabled by default so tests don't need a git repo.
    """

    def __init__(
        self,
        repo_root: str,
        auto_commit: bool = False,
    ) -> None:
        self._root = Path(repo_root)
        self._rfc_dir = self._root / "rfc"
        self._registry_dir = self._root / "registry"
        self._index_path = self._registry_dir / "rfcs.json"
        self._auto_commit = auto_commit

        # Ensure base directories exist
        for subdir in (
            "PROPOSALS", "EVIDENCE", "DISCUSSION",
            "SYNTHESIS", "CANONICAL", "ARCHIVE",
        ):
            (self._rfc_dir / subdir).mkdir(parents=True, exist_ok=True)
        self._registry_dir.mkdir(parents=True, exist_ok=True)

    # ---- public API -------------------------------------------------------

    def save_rfc(self, rfc: RFC) -> Path:
        """
        Write an RFC as a markdown file in the directory matching its
        current state.  Returns the path of the written file.
        """
        dir_name = _STATE_DIR_MAP[rfc.state]
        target_dir = self._rfc_dir / dir_name
        target_dir.mkdir(parents=True, exist_ok=True)
        filepath = target_dir / _rfc_filename(rfc)
        filepath.write_text(_rfc_to_markdown(rfc), encoding="utf-8")
        self._git_commit(f"rfc({rfc.number:04d}): {rfc.title} [{rfc.state.value}]")
        return filepath

    def load_rfc(self, number: int) -> Optional[RFC]:
        """
        Find and load an RFC by number, searching all state directories.

        Returns the parsed RFC or None if not found.
        """
        for subdir in _STATE_DIR_MAP.values():
            dir_path = self._rfc_dir / subdir
            if not dir_path.exists():
                continue
            for fpath in dir_path.iterdir():
                if fpath.suffix == ".md" and f"-{number:04d}-" in fpath.name:
                    return self._load_rfc_file(fpath)
                # Also try exact prefix match
                if fpath.suffix == ".md" and fpath.name.startswith(f"rfc-{number:04d}"):
                    return self._load_rfc_file(fpath)
        return None

    def load_all_rfcs(self) -> List[RFC]:
        """Load every RFC from all state directories."""
        rfcs: List[RFC] = []
        seen_numbers: set = set()
        for subdir in _STATE_DIR_MAP.values():
            dir_path = self._rfc_dir / subdir
            if not dir_path.exists():
                continue
            for fpath in sorted(dir_path.iterdir()):
                if fpath.suffix != ".md":
                    continue
                rfc = self._load_rfc_file(fpath)
                if rfc is not None and rfc.number not in seen_numbers:
                    rfcs.append(rfc)
                    seen_numbers.add(rfc.number)
        return rfcs

    def load_into_engine(self, engine: RFCEngine) -> int:
        """Load all persisted RFCs into an RFCEngine.  Returns count loaded."""
        count = 0
        for rfc in self.load_all_rfcs():
            engine.load_rfc(rfc)
            count += 1
        return count

    def generate_index(self, engine: Optional[RFCEngine] = None) -> dict:
        """
        Generate the ``registry/rfcs.json`` index.

        If *engine* is provided, indexes RFCs from the engine.  Otherwise,
        indexes from the filesystem.
        """
        if engine is not None:
            rfcs = list(engine.all_rfcs().values())
        else:
            rfcs = self.load_all_rfcs()

        entries = []
        max_number = 0
        for rfc in sorted(rfcs, key=lambda r: r.number):
            dir_name = _STATE_DIR_MAP[rfc.state]
            filename = f"rfc/{dir_name}/{_rfc_filename(rfc)}"
            entries.append({
                "number": rfc.number,
                "title": rfc.title,
                "filename": filename,
                "status": rfc.state.value,
                "author": rfc.author,
                "date": rfc.created_at.strftime("%Y-%m-%d"),
            })
            if rfc.number > max_number:
                max_number = rfc.number

        index = {
            "rfcs": entries,
            "next_number": max_number + 1 if entries else 1,
        }

        self._index_path.write_text(
            json.dumps(index, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self._git_commit("registry: update rfcs.json index")
        return index

    def remove_rfc_from_state_dir(self, rfc: RFC) -> None:
        """
        Remove the RFC file from its current state directory.

        Call this before ``save_rfc`` when an RFC changes state, so the old
        file is cleaned up.
        """
        old_dir = _STATE_DIR_MAP[rfc.state]
        old_path = self._rfc_dir / old_dir / _rfc_filename(rfc)
        if old_path.exists():
            old_path.unlink()

    def move_rfc(self, rfc: RFC, new_state: RFCState) -> Path:
        """
        Move an RFC to a new state: remove from old directory, write to new.
        Updates the RFC's state in-place and returns the new path.
        """
        self.remove_rfc_from_state_dir(rfc)
        rfc.state = new_state
        return self.save_rfc(rfc)

    # ---- internals --------------------------------------------------------

    def _load_rfc_file(self, filepath: Path) -> Optional[RFC]:
        """Parse a single markdown file into an RFC."""
        try:
            text = filepath.read_text(encoding="utf-8")
        except Exception:
            return None
        data = _parse_rfc_markdown(text)
        if "number" not in data:
            return None
        return RFC.from_dict(data)

    def _git_commit(self, message: str) -> None:
        """Stage and commit if auto_commit is enabled."""
        if not self._auto_commit:
            return
        os.system(f"cd {self._root} && git add -A && git commit -m '{message}'")

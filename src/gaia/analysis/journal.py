"""The improvements journal: an append-only audit log of what the loop changed.

Every artifact the autonomous self-improve loop applies (a skill written, a soul created or
refined, a memory saved) is appended here as one JSON line, so the change is auditable,
de-dupeable, and reversible (``gaia improvements`` / ``/improvements``). Stdlib only; one
file under ``~/.gaia`` (``improvements.jsonl``), atomic-enough for a single daemon writer.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from gaia import constants


@dataclass
class Improvement:
    """One applied change: a skill/soul/memory the loop created or refined."""

    type: str  # "skill" | "soul" | "memory"
    target: str  # the id/key/user the change applies to
    action: str  # "created" | "refined" | "added"
    summary: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts: float = field(default_factory=time.time)
    reverted: bool = False

    def line(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "ts": self.ts,
                "type": self.type,
                "target": self.target,
                "action": self.action,
                "summary": self.summary,
                "reverted": self.reverted,
            }
        )


class ImprovementJournal:
    """Append-only log of applied improvements (one JSON object per line)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else constants.IMPROVEMENTS_FILE

    def record(self, improvement: Improvement) -> Improvement:
        """Append ``improvement`` and return it."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a") as fh:
            fh.write(improvement.line() + "\n")
        return improvement

    def entries(self) -> list[Improvement]:
        """Every recorded improvement, in file order (empty when the file is missing)."""
        if not self._path.exists():
            return []
        out: list[Improvement] = []
        for raw in self._path.read_text().splitlines():
            if not raw.strip():
                continue
            try:
                d = json.loads(raw)
            except json.JSONDecodeError:
                continue
            out.append(
                Improvement(
                    id=d.get("id", ""),
                    ts=d.get("ts", 0.0),
                    type=d.get("type", ""),
                    target=d.get("target", ""),
                    action=d.get("action", ""),
                    summary=d.get("summary", ""),
                    reverted=d.get("reverted", False),
                )
            )
        return out

    def applied_targets(self, type_: str) -> set[str]:
        """Targets of ``type_`` already applied (and not reverted) — for de-duping proposals."""
        return {e.target for e in self.entries() if e.type == type_ and not e.reverted}

    def get(self, improvement_id: str) -> Improvement | None:
        """The entry with ``improvement_id``, or ``None``."""
        return next((e for e in self.entries() if e.id == improvement_id), None)

    def mark_reverted(self, improvement_id: str) -> bool:
        """Rewrite the log marking ``improvement_id`` reverted; True if found."""
        entries = self.entries()
        found = False
        for e in entries:
            if e.id == improvement_id and not e.reverted:
                e.reverted = True
                found = True
        if found:
            self._path.write_text("".join(e.line() + "\n" for e in entries))
        return found

"""Structural diff parsing for human-readable change summaries."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DiffSummary:
    files_changed: tuple[str, ...]
    additions: int
    deletions: int
    summary: str


class SemanticDiff:
    """Parses raw git diffs into structured summaries.

    This is purely structural parsing (file names, line counts).
    LLM-powered intent-level summarization can be layered on top later.
    """

    def summarize_diff(self, raw_diff: str) -> DiffSummary:
        """Parse a git diff string into a DiffSummary."""
        if not raw_diff.strip():
            return DiffSummary(
                files_changed=(),
                additions=0,
                deletions=0,
                summary="No changes",
            )

        files = self._extract_files(raw_diff)
        additions = raw_diff.count("\n+") - raw_diff.count("\n+++")
        deletions = raw_diff.count("\n-") - raw_diff.count("\n---")

        summary_parts: list[str] = []
        if additions:
            summary_parts.append(f"{additions} addition(s)")
        if deletions:
            summary_parts.append(f"{deletions} deletion(s)")
        summary_parts.append(f"across {len(files)} file(s)")

        return DiffSummary(
            files_changed=tuple(files),
            additions=max(0, additions),
            deletions=max(0, deletions),
            summary=", ".join(summary_parts),
        )

    def _extract_files(self, raw_diff: str) -> list[str]:
        """Extract file paths from diff headers."""
        pattern = r"^diff --git a/(.+?) b/(.+?)$"
        matches = re.findall(pattern, raw_diff, re.MULTILINE)
        # Use the 'b' path (destination) for each file
        return [m[1] for m in matches]

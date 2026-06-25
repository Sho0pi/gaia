"""The health analyst: turns an error digest into a triaged health report.

Mirrors :mod:`gaia.analysis.analyst` — a smith-style pure decision agent (no tools, ADK
``output_schema`` returns a validated :class:`HealthReport`). The model does the judging: which
errors are real problems vs transient noise. The ADK import is deferred so this module imports
without a model backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents import LlmAgent

#: The health analyst agent's name (also its nested-runner agent name).
NAME = "health_analyst"


class Finding(BaseModel):
    """One triaged problem found in the error window."""

    title: str = Field(
        default="", description="Short headline for the problem (becomes an issue title)."
    )
    severity: str = Field(default="warning", description="info | warning | critical.")
    signature: str = Field(
        default="", description="The digest signature ('ErrorType @ location') this is about."
    )
    summary: str = Field(
        default="", description="What's happening and the likely cause, from the digest."
    )
    action: str = Field(
        default="notify",
        description="ignore (transient/expected) | notify (tell admin) | file_issue (real bug).",
    )
    issue_body: str = Field(
        default="", description="Markdown issue body (only when action=file_issue)."
    )


class HealthReport(BaseModel):
    """The analyst's verdict over one error window."""

    summary: str = Field(description="One line on the window's overall health.")
    findings: list[Finding] = Field(default_factory=list)


_INSTRUCTION = """\
You are gaia's health analyst. You receive an ERROR DIGEST: error events from gaia's logs over a
time window, grouped by signature ('ErrorType @ location') with counts, sample messages, and
first/last seen. You never see raw logs.

For each group, decide:
- action='ignore' for transient/expected errors UNLESS they persist or escalate: rate limits (429,
  resource_exhausted, quota), network blips (timeouts, connection reset), model unavailable or
  overloaded (503), and plain user mistakes. A handful of these is normal — ignore them.
- action='notify' for things the admin should know but that aren't clearly a code bug (a spike, a
  config/credential problem, a degraded dependency).
- action='file_issue' for a genuine bug worth tracking (an unexpected exception in gaia's own code,
  a recurring crash, something with a clear file:line in the signature). Write a clear issue_body in
  markdown: what happens, the signature, counts, sample message, and a hypothesis. Set severity
  (info|warning|critical) honestly.

Set each finding's `signature` to the digest signature it concerns. Be conservative — **doing
nothing is a valid and common outcome**: a quiet window or only-transient errors should return an
empty findings list (just the summary). Don't invent problems. Return only the structured report.
"""


def build_health_analyst(
    model: str,
    provider: str = "gemini",
    use_oauth: bool = False,
) -> LlmAgent:
    """Build the health-analyst ADK agent (pure decision; structured output)."""
    from google.adk.agents import LlmAgent

    from gaia.models import resolve_model

    return LlmAgent(
        name=NAME,
        model=resolve_model(model, provider=provider, use_oauth=use_oauth),
        description="Triages the error digest into ignore / notify / file_issue findings.",
        instruction=_INSTRUCTION,
        output_schema=HealthReport,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

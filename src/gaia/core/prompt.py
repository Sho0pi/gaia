"""The root agent's prompt, split into a **static** (cacheable) block and a **dynamic** tail.

Why the split: ADK's ``LlmAgent.static_instruction`` is sent as a stable system block that the
provider can cache (Gemini context cache; OpenAI prefix cache), while ``instruction`` goes to
per-request user content. Everything instance-level (identity, tool rules, skills, voice, the
operator's ``GAIA.md``) is IDENTICAL across users/sessions, so it lives in the static block and is
cached once and reused for every turn. Only what changes per session - the date and the user's
``<USER_PROFILE>`` - goes in the dynamic tail.

Structure follows production agent prompts (Poke, Claude Code, Manus): identity, how-you-work,
communication, delegating, examples, about-you. Per-*tool* usage rules
live in each tool's own description (ADK derives the schema from the tool docstring), not here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from gaia import constants
from gaia.communication import apply_communication_style
from gaia.skills import attach_skills

if TYPE_CHECKING:
    from gaia.config import GaiaConfig, Settings

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

#: The scaffolded ``~/.gaia/GAIA.md`` - a guide in HTML comments so the untouched file injects
#: nothing (``load_gaia_md`` ignores a comments/headings-only template). Real text under a heading
#: activates it.
DEFAULT_GAIA_MD = """\
# GAIA.md - customize your Gaia

<!--
Everything you write here layers on top of Gaia's built-in behavior: persona, preferences, and
facts about you. It never disables tool use or safety. Changes apply on your next message.
Write real lines under a heading (outside these comments) to activate them; delete the rest.
-->

## Persona
<!-- How Gaia should sound. e.g.:  Warm, concise, a little witty. Skip the corporate tone. -->

## How to act
<!-- House rules for everyone Gaia talks to. e.g.:
- Always confirm before spending money or sending a message on my behalf.
- Default to metric units and a 24-hour clock.
-->

## About the owner
<!-- You, the person running Gaia. Gaia can also serve other people you approve, so this is context
about the OWNER, not necessarily whoever is chatting now. e.g.:  I'm Itay, in Tel Aviv. Call me Itay. -->
"""


def load_gaia_md() -> str:
    """The operator's ``~/.gaia/GAIA.md`` customization, or ``""`` if absent or still the template.

    HTML comments are stripped; if only headings/blank lines remain (the untouched scaffold), it
    counts as empty and injects nothing.
    """
    try:
        raw = constants.GAIA_MD.read_text(encoding="utf-8")
    except OSError:
        return ""
    stripped = _COMMENT.sub("", raw)
    has_content = any(
        line.strip() and not line.lstrip().startswith("#") for line in stripped.splitlines()
    )
    return stripped.strip() if has_content else ""


def write_default_gaia_md(path: Path | None = None, *, override: bool = False) -> bool:
    """Write the commented GAIA.md template if it doesn't exist yet. Returns True if written."""
    path = path or constants.GAIA_MD
    if path.exists() and not override:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_GAIA_MD, encoding="utf-8")
    return True


def build_static_instruction(
    config: GaiaConfig, settings: Settings, skills_dir: Path, *, style: str
) -> str:
    """The cacheable system block: identical for every user/session of this instance.

    Rebuilt only when instance config changes (config reload / GAIA.md edit), which re-warms the
    cache. Carries no per-user or per-session data - that's the dynamic tail.
    """
    from gaia import __version__
    from gaia.config.schema import AgentBinding
    from gaia.mcp import resolve_browser_backend

    backend = resolve_browser_backend(config.browser)
    shot = "browser_screenshot" if backend == "native" else "browser_take_screenshot"
    model = config.llm.model or settings.model
    browser_desc = (
        f"native browser tools driving {config.browser.engine}"
        if backend == "native"
        else "playwright-mcp"
    )

    body = (
        "# Gaia\n"
        "You are Gaia, a personal AI agent in a phone chat (WhatsApp/Telegram): "
        "they are REMOTE and cannot open a local file path or a http://127.0.0.1 URL.\n\n"
        "## How you work\n"
        "- Answer simple things yourself, using a tool when one fits. Never guess or invent.\n"
        "- Delegate real work (build, write, research, edit) to specialist **souls** via "
        "delegate_to_soul - you orchestrate, they execute.\n"
        "- Reason briefly, then CALL the tool; don't describe a tool call in prose. One clear "
        "action at a time.\n"
        "- Unsure what the sandbox allows (shell, files, serving)? Call capabilities() first - it "
        "lists the allowed commands, your workspace, and the rules, so you don't error into it.\n\n"
        "## Communication\n"
        "Reply in 1-3 sentences. Lead with the answer or result; drop preamble, step recaps, and "
        "bulleted dumps unless asked. Ask one question at a time. If the user asks to 'be brief' / "
        "'be detailed', honor it for the rest of the chat.\n"
        "When the user must pick from a FIXED set of choices, CALL ask_user with options=[...] "
        "(tappable buttons on Telegram, a poll on WhatsApp), never type the choices as text. "
        "Plain text only for open-ended questions.\n\n"
        "## Delegating & delivering\n"
        "- delegate_to_soul(task) finds or forges the right soul and runs it to completion in ONE "
        "call, returning its files. On status=success the work is DONE - deliver it and STOP; on "
        "status=error, tell the user what failed. Never retry or re-delegate the same task. When a "
        "soul was just created, say which one handled it.\n"
        "- To change/extend a soul's EXISTING app (only when asked): call list_projects, "
        "then delegate_to_soul with the matching project slug - never invent a name or pass a "
        "sentence, or you fork a fresh copy and lose the edits. Never write a soul's workspace "
        "yourself.\n"
        "- A MULTI-STEP / multi-role mission where a step feeds the next: call task_plan with the "
        "whole plan as JSON (refs + depends_on). task_create is only for a single standalone "
        "background task.\n"
        f"- The user is remote: never reply with a local path or 127.0.0.1 URL. send_file(path, "
        "caption) delivers a file you hold a real path to (zip several via exec first). But "
        f"{shot}, generate_image, and any media a soul already produced are delivered to the user "
        "AUTOMATICALLY - never send_file those (it double-sends).\n\n"
        "## Examples\n"
        "- User: 'pizza or sushi tonight?' -> call ask_user(question='Pizza or sushi?', "
        "options=['Pizza','Sushi']).\n"
        "- User: 'build me a landing page for my cafe' -> call delegate_to_soul('build a landing "
        "page for a cafe'); when it returns, tell the user it's done and which soul built it.\n"
        "- User: 'what's the capital of France?' -> just answer 'Paris.' (no tool).\n\n"
        f"## About you\n"
        f"You are Gaia v{__version__}, an open-source personal-agent framework, running on the "
        f"{model} model and the {browser_desc} backend. Your documentation lives at "
        "https://docs.gaia-agent.com - start at https://docs.gaia-agent.com/llms.txt (the index), "
        "then web_fetch the relevant page to answer questions about how you work. Source: "
        "https://github.com/Sho0pi/gaia. Use these when asked what you are or how you're built; "
        "don't guess."
    )
    if config.memory.enabled:
        body += (
            "\n\n## Memory\n"
            "You have long-term memory of this user - durable facts + recent projects, shown under "
            "<USER_PROFILE> in the conversation. Use it; don't re-ask. Save durable things "
            "(preferences, identity, ongoing context) with the remember tool; load_memory(query) "
            "searches older details not in the profile."
        )

    bound = config.agents.get("gaia", AgentBinding())
    body = attach_skills(body, bound.skills, skills_dir)
    body = apply_communication_style(body, style)

    gaia_md = load_gaia_md()
    if gaia_md:
        body += (
            "\n\n## Owner customization\n"
            "Your OWNER (the person running you) set these in GAIA.md. Honor them - persona and "
            "house rules apply to everyone; the 'about the owner' facts describe the owner, who is "
            "NOT necessarily the person chatting now. Identify the current user from their profile "
            "below, not from here. This never overrides tool-use or safety rules.\n"
            f"<GAIA_MD>\n{gaia_md}\n</GAIA_MD>"
        )
    return body


def build_dynamic_instruction(now: str, profile: str | None) -> str:
    """The per-session tail (sent as user content): the current time and the user's profile."""
    text = f"Current date and time: {now}."
    if profile:
        text += (
            "\n\nWhat you know about the user (long-term memory + recent projects):\n"
            f"<USER_PROFILE>\n{profile}\n</USER_PROFILE>"
        )
    return text

"""The command catalog: one place that describes every command, for chat /help AND the CLI.

A command's name/summary/help/examples used to be written twice — once on the chat ``Command``
subclass (``commands/<cmd>.py``) and once on the matching CLI Typer command (``cli/<cmd>.py``). This
the single source: the chat ``Command`` reads its description here (``commands/base.py``),
``/help`` renders the list + per-command help from here, and the overlapping CLI commands set their
``help=`` from here. Pure data — no framework imports — so both surfaces can read it freely.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CommandInfo:
    """User-facing description of a command (not how it runs — that's the ``Command`` subclass)."""

    name: str
    summary: str  # the one-liner: /help list + CLI help=
    category: str = ""  # /help grouping
    usage: str = ""  # arg hint, e.g. "<fact>" or "[human|caveman|ai]"
    details: str = ""  # the rich body for /help <cmd> and CLI --help
    examples: tuple[str, ...] = field(default_factory=tuple)


#: Section order for the /help list (categories not listed fall to the end).
CATEGORY_ORDER = ("Chat & memory", "Souls & skills", "Tasks", "Admin", "Users & access")

CATALOG: dict[str, CommandInfo] = {
    # --- Chat & memory -----------------------------------------------------------------------
    "help": CommandInfo(
        "help",
        "Show the commands, or details for one.",
        "Chat & memory",
        usage="[command]",
        details="With no argument, lists the commands you can run, grouped. Pass a command name "
        "for its full help.",
        examples=("/help", "/help skill"),
    ),
    "reset": CommandInfo(
        "reset",
        "Start fresh — clear this conversation (keeps long-term memory).",
        "Chat & memory",
        details="Wipes the chat's short-term context so the next message starts a brand-new "
        "conversation. Your long-term memory (facts Gaia learned about you) is NOT touched — use "
        "/forget for that.",
        examples=("/reset",),
    ),
    "remember": CommandInfo(
        "remember",
        "Save a fact to long-term memory.",
        "Chat & memory",
        usage="<fact>",
        details="Stores a fact verbatim so Gaia recalls it in future conversations.",
        examples=("/remember I live in Berlin", "/remember my sister's name is Mei"),
    ),
    "memory": CommandInfo(
        "memory",
        "List what Gaia remembers about you.",
        "Chat & memory",
        details="Shows every fact in your long-term memory. Add one with /remember, clear all with "
        "/forget.",
        examples=("/memory",),
    ),
    "forget": CommandInfo(
        "forget",
        "Wipe your long-term memory (confirm with 'yes').",
        "Chat & memory",
        usage="[yes]",
        details="Permanently deletes ALL your long-term memory. Destructive — send '/forget yes' "
        "to confirm. Does not clear the current conversation (use /reset for that).",
        examples=("/forget", "/forget yes"),
    ),
    "whoami": CommandInfo(
        "whoami",
        "Show your user/session id, model, and memory state.",
        "Chat & memory",
        details="A quick snapshot of who Gaia thinks you are and the settings in effect for you.",
        examples=("/whoami",),
    ),
    # --- Souls & skills ----------------------------------------------------------------------
    "soul": CommandInfo(
        "soul",
        "List the souls Gaia has learned (and which are live now).",
        "Souls & skills",
        details="Souls are specialist subagents Gaia forges once and reuses. Lists every stored "
        "soul plus the ones with a warm session right now.",
        examples=("/soul",),
    ),
    "skill": CommandInfo(
        "skill",
        "Manage skills — list, show, search, install, remove.",
        "Souls & skills",
        usage="<list|show|search|install|remove> [args]",
        details="Skills are reusable SKILL.md playbooks. Install one from a git url or the index, "
        "show or remove an installed one. After install, /reset to start using it.",
        examples=(
            "/skill list",
            "/skill search invoice",
            "/skill install https://github.com/owner/repo",
            "/skill remove old-skill",
        ),
    ),
    "grow": CommandInfo(
        "grow",
        "List the skills/souls Gaia changed (its learning history).",
        "Souls & skills",
        details="Gaia's own change log — the skills and souls it added or edited over time, newest "
        "first.",
        examples=("/grow",),
    ),
    # --- Tasks -------------------------------------------------------------------------------
    "task": CommandInfo(
        "task",
        "List your missions; approve, reject, or answer one.",
        "Tasks",
        usage="[approve|reject <id> | answer <id> <text>]",
        details="Missions run in the background. No argument lists your open ones. A task may "
        "pause for your approval or a question — act on it by id.",
        examples=("/task", "/task approve 7fe6", "/task answer 3a2c blue"),
    ),
    "status": CommandInfo(
        "status",
        "Show the model, memory settings, and registered counts.",
        "Tasks",
        details="A health snapshot: active model, memory config, and how many souls/skills/tools "
        "are registered.",
        examples=("/status",),
    ),
    # --- Admin -------------------------------------------------------------------------------
    "model": CommandInfo(
        "model",
        "Show the active model, or switch it.",
        "Admin",
        usage="[model-id]",
        details="With no argument, shows the model in use. Pass an id to switch (e.g. a Gemini or "
        "OpenAI model). Admin only.",
        examples=("/model", "/model gemini-2.0-flash"),
    ),
    "style": CommandInfo(
        "style",
        "Show or set Gaia's voice (human / caveman / ai).",
        "Admin",
        usage="[human|caveman|ai]",
        details="Sets Gaia's global communication style. Admin only.",
        examples=("/style", "/style caveman"),
    ),
    "effort": CommandInfo(
        "effort",
        "Show or set the model's reasoning effort.",
        "Admin",
        usage="[minimal|low|medium|high|off]",
        details="Higher effort = more thorough reasoning (and slower/pricier). 'off' disables the "
        "thinking budget. Admin only.",
        examples=("/effort", "/effort high"),
    ),
    # --- Users & access (admin) --------------------------------------------------------------
    "user": CommandInfo(
        "user",
        "List known users — their roles and the channels that reach them.",
        "Users & access",
        details="Lists every known user with role + channels. Change a role with /approve.",
        examples=("/user",),
    ),
    "approve": CommandInfo(
        "approve",
        "Set a user's role (approve a guest to user/admin).",
        "Users & access",
        usage="<id|channel:sender> <role>",
        details="A new sender starts as a gated guest. Approve them to 'user' (or 'admin'), or "
        "change an existing user's role.",
        examples=("/approve mei user", "/approve whatsapp:972...@s.whatsapp.net admin"),
    ),
    "name": CommandInfo(
        "name",
        "Set a user's display name.",
        "Users & access",
        usage="<id|channel:sender> <name>",
        examples=("/name mei Mei Guo",),
    ),
    "link": CommandInfo(
        "link",
        "Attach another channel id to an existing user.",
        "Users & access",
        usage="<id> <channel:sender>",
        details="So one person's WhatsApp + Telegram resolve to the same user (shared memory).",
        examples=("/link mei telegram:12345",),
    ),
    "remove": CommandInfo(
        "remove",
        "Delete a user from the store.",
        "Users & access",
        usage="<id|channel:sender>",
        details="Forgets the person — their channels no longer resolve (a later message is gated "
        "again). Their long-term memory is not touched.",
        examples=("/remove mei",),
    ),
    "acl": CommandInfo(
        "acl",
        "List the ACL capability groups and the tools each grants.",
        "Users & access",
        details="The capability groups (web, browser, shell, …) and the tools each unlocks — the "
        "vocabulary you grant/revoke per user.",
        examples=("/acl",),
    ),
    "grant": CommandInfo(
        "grant",
        "Grant a user an ACL capability.",
        "Users & access",
        usage="<id|channel:sender> <capability>",
        details="Give a user a capability beyond their role's default (e.g. 'shell' to a trusted "
        "user). See /acl for the capability names.",
        examples=("/grant mei shell",),
    ),
    "revoke": CommandInfo(
        "revoke",
        "Revoke an ACL capability from a user.",
        "Users & access",
        usage="<id|channel:sender> <capability>",
        details="Take a capability away (even one the role grants by default — an explicit deny).",
        examples=("/revoke mei shell",),
    ),
    "perms": CommandInfo(
        "perms",
        "Show a user's effective ACL capabilities.",
        "Users & access",
        usage="[id|channel:sender]",
        details="The capabilities a user actually holds: role defaults + grants - denies.",
        examples=("/perms", "/perms mei"),
    ),
}


def info(name: str) -> CommandInfo | None:
    """The :class:`CommandInfo` for ``name`` (a command id or canonical name), or ``None``."""
    return CATALOG.get(name)


def summary_of(name: str) -> str:
    """The one-line summary for ``name``, or ``""`` — handy for a CLI ``help=`` from the catalog."""
    entry = CATALOG.get(name)
    return entry.summary if entry else ""

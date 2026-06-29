"""Bridge a plain-text message to Gaia's ADK root agent and back to text.

Connectors speak :data:`~gaia.connectors.base.Handler`; ADK speaks ``Runner``
events over a session. :class:`GaiaHandler` is the thin glue between them. The ADK
imports are deferred so importing gaia stays cheap and the connectors remain
unit-testable without a model backend.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gaia import constants
from gaia.connectors.base import Inbound, Question, Send, inbound_attachments
from gaia.core.elicit import (
    ASK_USER_TOOL,
    DELEGATE_TOOL,
    Pending,
    SoulPending,
    resolve_answer,
    soul_elicitation_sink,
)
from gaia.logs import log_event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia

#: Debounce window for coalescing a rapid burst of messages (double-texting) into one turn. The
#: cost is ~this much added latency to every normal message; small on purpose.
_COALESCE_SECONDS = 1.0


def _merge_inbounds(batch: list[Inbound]) -> Inbound:
    """Merge a coalesced burst of messages into one turn: joined text + concatenated media."""
    if len(batch) == 1:
        return batch[0]
    text = "\n".join(i.text for i in batch if i.text.strip())
    media = tuple(m for i in batch for m in i.media)
    return Inbound(text=text, media=media, is_group=any(i.is_group for i in batch))


def _friendly_error(exc: Exception) -> str:
    """A short, user-facing message for a failed turn (rate limit / outage / network / other)."""
    text = str(exc)
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return "I'm being rate-limited right now (model quota). Please try again in a minute."
    if "503" in text or "UNAVAILABLE" in text or "overloaded" in text.lower():
        return "The model is busy at the moment. Please try again shortly."
    # A dropped connection to the model (httpx/httpcore TransportError) — usually a transient
    # network blip; the backend already retried once before this surfaced.
    if type(exc).__module__.split(".")[0] in ("httpx", "httpcore"):
        return "I had a network hiccup reaching the model. Please try again."
    return "Sorry — something went wrong handling that. Please try again."


class GaiaHandler:
    """Runs inbound text through Gaia's ADK root agent and returns the reply text.

    The ADK ``Runner`` and its session are expensive to build and hold the running
    conversation, so they're created once on the first message and kept on the
    instance (``self._runner``); later messages reuse them, which is what gives the
    bot memory within a process. One ``GaiaHandler`` == one conversation.
    """

    def __init__(
        self,
        gaia: Gaia,
        *,
        user_id: str = "gaia-user",
        session_id: str = "gaia-session",
        role: str = "admin",
    ) -> None:
        self._gaia = gaia
        self._user_id = user_id
        self._session_id = session_id
        # The caller's role; commands gate on it (e.g. admin-only /approve). Defaults to
        # admin so single-user / cron / test callers that don't resolve a user are trusted.
        self._role = role
        self._runner: Any | None = None
        # The config the runner was built against; when gaia.yaml changes, ConfigSupplier hands back
        # a new object and we rebuild (None until the first build, so injected-runner tests skip).
        # The session is durable + shared (gaia.session_service), so the conversation survives both
        # hot-reloads and process restarts (#76).
        self._runner_config: Any | None = None
        # Idle consolidation: the conversation lives in the durable session; when it goes idle this
        # timer fires, distils the whole session into long-term memory, and clears it (human-like).
        # Re-armed on every turn; injected-runner tests skip it.
        self._idle_task: asyncio.Task[None] | None = None
        # A question the model asked via ``ask_user`` that this conversation is paused on;
        # the user's next message is its answer, fed back to resume the run (see _resume).
        self._pending: Pending | None = None
        # Serialize everything that touches the durable session — turns, commands, idle
        # consolidation. Concurrent inbound messages would otherwise run two run_async on the same
        # session and trip ADK's optimistic-concurrency check (#315). One daemon = one loop, so a
        # lock is enough. ``_inbox`` coalesces a rapid burst (double-texting) into one turn.
        self._lock = asyncio.Lock()
        self._inbox: list[Inbound] = []
        self._inbox_send: Send | None = None

    async def _profile_block(self) -> str | None:
        """The user's distilled profile to bake into the prompt, or None.

        One LLM call per runner build (session start / config reload) — see
        :func:`gaia.memory.profile.distill_profile`. Gated on ``memory.preload``; the
        distiller itself returns None (no model call) when memory is off or the user has
        nothing stored, so a fresh store never triggers one.
        """
        if not self._gaia.config.memory.preload:
            return None
        from gaia.memory.profile import distill_profile

        return await distill_profile(self._gaia, self._user_id)

    def _build_runner(self, profile: str | None) -> Any:
        """Build a Runner over the (reused) session service against the live config.

        Re-reads ``build_root_agent`` (model/instruction/profile) and ``memory_service``
        each call, so a rebuild picks up every gaia.yaml change. The session service is
        shared, so the conversation continues across rebuilds.
        """
        from google.adk.runners import Runner

        from gaia.core.plugins import SessionWindowPlugin, ToolLoggingPlugin, ToolPermissionPlugin

        window = self._gaia.config.sessions.window_turns
        return Runner(
            app_name=constants.APP_NAME,
            agent=self._gaia.build_root_agent(self, profile=profile),
            session_service=self._gaia.session_service,
            memory_service=self._gaia.memory_service,
            plugins=[
                ToolPermissionPlugin(self._gaia),
                ToolLoggingPlugin(),
                SessionWindowPlugin(window),  # cap replayed turns; full session stays on disk
            ],
        )

    async def _ensure_runner(self) -> Any:
        if self._runner is None:
            # Durable shared session: resume by the stable id, create only if it's new. Survives
            # process restarts (#76); the window plugin bounds what's replayed.
            svc = self._gaia.session_service
            existing = await svc.get_session(
                app_name=constants.APP_NAME, user_id=self._user_id, session_id=self._session_id
            )
            if existing is None:
                await svc.create_session(
                    app_name=constants.APP_NAME, user_id=self._user_id, session_id=self._session_id
                )
            self._runner_config = self._gaia.config
            self._runner = self._build_runner(await self._profile_block())
        elif self._runner_config is not None and self._gaia.config is not self._runner_config:
            # gaia.yaml changed on disk (ConfigSupplier returns a new object): rebuild the
            # agent so the live conversation picks up the new model/instruction/memory. The
            # shared session service keeps this session's history intact.
            log_event("config_reloaded", user=self._user_id, session=self._session_id)
            self._runner_config = self._gaia.config
            self._runner = self._build_runner(await self._profile_block())
        return self._runner

    async def __call__(self, inbound: Inbound, send: Send) -> None:
        log_event(
            "message_in",
            user=self._user_id,
            session=self._session_id,
            chars=len(inbound.text),
            media=len(inbound.media) or None,
        )

        # Commands + ask_user answers are discrete (never coalesced) but still serialized under the
        # lock so they can't race a turn / the idle consolidation on the durable session.
        if inbound.text.strip().startswith("/") or self._pending is not None:
            async with self._lock:
                # A slash command is handled out-of-band (never reaches the model/memory). Checked
                # before resume so /reset (which clears _pending) still escapes a pending question.
                if await self._maybe_run_command(inbound.text, send):
                    return
                if self._pending is not None:
                    await self._resume(inbound, send)
                    return
            # Wasn't a real command and nothing pending → fall through as a normal message.

        # Normal message: coalesce a rapid burst into one turn. Append BEFORE taking the lock so a
        # sibling arriving during the debounce window lands in the same batch; the lock then
        # serializes turns (concurrent run_async on one durable session is what crashed, #315).
        self._inbox.append(inbound)
        self._inbox_send = send
        async with self._lock:
            if not self._inbox:  # a prior holder already drained my message into its turn
                return
            await asyncio.sleep(_COALESCE_SECONDS)  # let a rapid burst land (siblings append/block)
            batch, self._inbox = self._inbox, []
            reply_to = self._inbox_send if self._inbox_send is not None else send
            await self._run_turn(_merge_inbounds(batch), reply_to)

    async def _run_turn(self, inbound: Inbound, send: Send) -> None:
        """Run one model turn for ``inbound`` (text + attachments). Caller holds ``self._lock``."""
        from google.genai import types

        # Build the model turn: the text part (if any) plus an image part per attachment, so
        # the model sees the picture this turn and it stays in the session for follow-ups.
        parts: list[Any] = [types.Part(text=inbound.text)] if inbound.text else []
        attached: list[Path] = []
        for item in inbound.media:
            try:
                data = item.path.read_bytes()
            except OSError:
                logging.getLogger(constants.LOGGER_NAME).warning(
                    "dropped inbound attachment (unreadable): %s", item.path
                )
                continue
            parts.append(types.Part.from_bytes(data=data, mime_type=item.mime))
            attached.append(item.path)
        # Stash the files for this turn so delegate_to_soul can copy them into the chosen
        # soul's workspace (where it can embed them). The model sees the image itself via the
        # part above — no synthetic "here's the path" message needed.
        inbound_attachments.set(tuple(attached))
        if not parts:  # nothing to send (empty text, no readable media)
            return

        # yield_user_message=True so the user's own turn is in the event stream we buffer —
        # otherwise auto-ingest only ever sees Gaia's replies and mem0 extracts facts from
        # the wrong half of the conversation.
        content = types.Content(role="user", parts=parts)
        await self._drive(content, send, secret=False, yield_user_message=True)

    async def _resume(self, inbound: Inbound, send: Send) -> None:
        """Resume a paused run with the user's reply.

        P1 (``pending.soul is None``): feed the answer as the root ``ask_user`` tool result —
        ADK finds the original call by id in the session events and continues the same
        invocation. P2: route the answer into the nested soul first (see :meth:`_resume_soul`).
        ``yield_user_message=False`` keeps the synthetic function-response out of the
        memory-ingest stream; a secret answer also skips buffering entirely (see _drive).
        """
        from google.genai import types

        pending, self._pending = self._pending, None
        assert pending is not None  # guarded by the caller
        answer = resolve_answer(pending, inbound.text)
        soul_key = pending.soul.soul_key if pending.soul else None
        log_event(
            "elicit_answered", user=self._user_id, soul=soul_key, secret=pending.secret or None
        )

        if pending.soul is not None:
            await self._resume_soul(pending, answer, send)
            return

        content = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=pending.fc_id, name=ASK_USER_TOOL, response={"answer": answer}
                    )
                )
            ],
        )
        await self._drive(content, send, secret=pending.secret, yield_user_message=False)

    async def _resume_soul(self, pending: Pending, answer: str, send: Send) -> None:
        """Feed the answer to the paused soul; finish or re-pause.

        If the soul asks a further question it re-pauses (the root stays paused on the same
        ``delegate_to_soul`` call); when the soul finishes, its result dict resumes that call so
        the root model continues as if delegate had just returned.
        """
        from google.genai import types

        from gaia.souls.run import resume_soul, soul_result

        assert pending.soul is not None  # guarded by the caller
        try:
            run = await resume_soul(self._gaia, pending.soul, answer)
        except Exception as exc:
            # log_event(exc=) writes the traceback to system.log AND the structured event.
            log_event("turn_error", user=self._user_id, exc=exc)
            self._gaia.soul_sessions.unpin(pending.soul.warm_key)
            await send(_friendly_error(exc))
            return

        if run.pending is not None:  # the soul asked something else — keep the root paused
            await self._surface_soul(pending.fc_id, run.pending, send)
            return

        # Soul finished: unpin its session and resume the ROOT delegate call with the result.
        self._gaia.soul_sessions.unpin(pending.soul.warm_key)
        # Deliver the soul's media here — it rides in soul_result as tool INPUT below, so the
        # screenshot bridge (which scans tool responses) can't see it. (#268 fast-follow)
        await self._emit_media(run.media, send)
        content = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=pending.fc_id, name=DELEGATE_TOOL, response=soul_result(run)
                    )
                )
            ],
        )
        await self._drive(content, send, secret=pending.secret, yield_user_message=False)

    async def _surface_soul(self, root_fc_id: str, soul: SoulPending, send: Send) -> None:
        """Record + send a delegated soul's question (the root stays paused on ``root_fc_id``)."""
        self._pending = Pending(
            fc_id=root_fc_id, options=soul.options, secret=soul.secret, soul=soul
        )
        text = f"*{soul.soul_name}* asks: {soul.question}" if soul.soul_name else soul.question
        log_event(
            "elicit_asked", user=self._user_id, soul=soul.soul_key, secret=soul.secret or None
        )
        await send(Question(text=text, options=soul.options, secret=soul.secret))

    async def _drive(
        self, content: Any, send: Send, *, secret: bool, yield_user_message: bool
    ) -> None:
        """Run one turn to completion or to an ask_user pause, then reply/buffer.

        Shared by a fresh turn and a resume. ``secret`` true means this turn carries a
        sensitive answer, so its events are never buffered to long-term memory.
        """
        runner = await self._ensure_runner()
        turn_events: list[Any] = []
        texts: list[str] = []
        ask_call: Any | None = None
        preface_done = False  # streamed the model's text before a long delegate ran (once)
        responded_ids: set[str] = set()  # long-running call ids that got a response (= completed)
        # A paused delegate_to_soul appends its SoulPending here (the tool can't return it — it
        # returns None to pause). A fresh list per turn; read after the loop.
        sink: list[SoulPending] = []
        token = soul_elicitation_sink.set(sink)
        try:
            async for event in runner.run_async(
                user_id=self._user_id,
                session_id=self._session_id,
                new_message=content,
                yield_user_message=yield_user_message,
            ):
                turn_events.append(event)
                get_responses = getattr(event, "get_function_responses", None)
                if get_responses is not None:
                    for resp in get_responses() or []:
                        rid = getattr(resp, "id", None)
                        if rid:
                            responded_ids.add(rid)  # this long-running call completed, not paused
                call = self._paused_call(event)
                # Stream the preface before a (long-running) delegate runs: send what the model
                # said leading up to the call so the user isn't left in silence while the soul
                # works. Consumes that text (cleared) so the final reply doesn't resend it.
                if not preface_done and self._calls_delegate(event):
                    preface = [*texts, *self._event_texts(event)]
                    if any(t.strip() for t in preface):
                        await self._emit_texts(preface, send)
                    texts = []
                    preface_done = True
                    if call is not None:  # a delegate that paused: still record the pause
                        ask_call = call
                    continue
                if call is not None:
                    # The run paused on ask_user. Record it but DON'T break the loop: a
                    # long-running tool emits no function-response, so run_async ends on its
                    # own right after this event. Letting it finish lets ADK close the run
                    # cleanly — breaking early aclose()s a live span ("Root node cancelled" /
                    # "Failed to detach context"). Act on the pause after the loop.
                    ask_call = call
                    texts.extend(self._event_texts(event))
                    continue
                # Collect the final answer's text parts; they're emitted after the loop so
                # a screenshot taken this turn can ride as its caption (one message). Skip
                # the echoed user event (role "user") — it's only here for auto-ingest.
                if (
                    event.is_final_response()
                    and event.content
                    and event.content.parts
                    and getattr(event.content, "role", None) != "user"
                ):
                    texts.extend(self._event_texts(event))
        except Exception as exc:
            # A model error (rate limit, outage) or tool fault must not surface as a raw
            # traceback to the user. log_event(exc=) records the traceback (system.log) + the
            # structured event; we send a short apology and end the turn.
            log_event("turn_error", user=self._user_id, exc=exc)
            await send(_friendly_error(exc))
            return
        finally:
            soul_elicitation_sink.reset(token)

        # ADK flags long_running_tool_ids on a call even when the tool COMPLETES in the same turn,
        # so a finished delegate looks paused. It's only truly paused if it got no response this
        # turn — otherwise fall through to the normal reply (which delivers its result + media).
        if ask_call is not None and ask_call.id in responded_ids:
            ask_call = None

        if ask_call is not None:
            # Paused: stream any preface text, then surface the question.
            await self._emit_texts(texts, send)
            if ask_call.name == DELEGATE_TOOL:
                soul = sink[-1] if sink else None
                if soul is None:  # shouldn't happen — fail safe rather than hang the conversation
                    logging.getLogger(constants.LOGGER_NAME).warning(
                        "delegate paused with no elicitation"
                    )
                    await send(
                        "(The delegated task is waiting on something, but I lost the question.)"
                    )
                else:
                    await self._surface_soul(ask_call.id, soul, send)
            else:
                await self._begin_elicitation(ask_call, send)
        else:
            await self._emit_reply(turn_events, texts, send)
        # The turn is already in the durable session; just (re)arm the idle timer so the
        # conversation consolidates into long-term memory once it goes quiet.
        if not secret:
            self._arm_idle_consolidate()

    def _paused_call(self, event: Any) -> Any | None:
        """The long-running call in ``event`` that paused the run for the user, else None.

        Either a direct ``ask_user`` (P1) or a ``delegate_to_soul`` whose nested soul paused on
        ask_user (P2); both surface as a long-running call flagged on
        ``event.long_running_tool_ids``. Other long-running tools never look like a pause.
        """
        ids = getattr(event, "long_running_tool_ids", None)
        if not ids or not (event.content and event.content.parts):
            return None
        for part in event.content.parts:
            call = getattr(part, "function_call", None)
            if call is not None and call.id in ids and call.name in (ASK_USER_TOOL, DELEGATE_TOOL):
                return call
        return None

    @staticmethod
    def _calls_delegate(event: Any) -> bool:
        """True if ``event`` carries a ``delegate_to_soul`` function-call (about to run a soul)."""
        if not (event.content and event.content.parts):
            return False
        return any(
            getattr(part, "function_call", None) is not None
            and part.function_call.name == DELEGATE_TOOL
            for part in event.content.parts
        )

    @staticmethod
    def _event_texts(event: Any) -> list[str]:
        """The non-empty text parts of an event's content (model speech), in order."""
        if not (event.content and event.content.parts):
            return []
        return [part.text for part in event.content.parts if part.text]

    async def _emit_media(self, paths: list[str], send: Send) -> None:
        """Send each soul-produced media path to the user once.

        Used by the resume path: a soul that finished after an ask_user pause returns its media in
        ``soul_result``, which is fed back as tool *input* — so the screenshot bridge
        (:func:`media_for_outputs`, which scans emitted tool *responses*) never sees it.
        """
        from pathlib import Path

        from gaia.connectors.base import Media, media_kind

        for raw in paths:
            path = Path(raw)
            kind = media_kind(path)
            log_event("media_out", user=self._user_id, tool=kind, chars=0)
            await send(Media(path, kind=kind))

    async def _emit_texts(self, texts: list[str], send: Send) -> None:
        """Send each non-empty text as its own reply (used for an ask_user preface)."""
        for text in texts:
            if text.strip():
                log_event("message_out", user=self._user_id, chars=len(text))
                await send(text)

    async def _begin_elicitation(self, call: Any, send: Send) -> None:
        """Record the pending question from a root ``ask_user`` call (P1) and surface it.

        (A delegated soul's question — P2 — is surfaced by :meth:`_surface_soul` from the
        per-turn sink in :meth:`_drive`, since the question lives in the soul, not this call.)
        """
        args = call.args or {}
        options = tuple(args.get("options") or ())
        secret = bool(args.get("secret", False))
        self._pending = Pending(fc_id=call.id, options=options, secret=secret)
        log_event(
            "elicit_asked", user=self._user_id, options=len(options) or None, secret=secret or None
        )
        await send(Question(text=str(args.get("question", "")), options=options, secret=secret))

    async def _emit_reply(self, events: list[Any], texts: list[str], send: Send) -> None:
        """Send the turn's reply: an image (with the text as its caption) when a screenshot
        was taken, otherwise the text parts.

        Connectors that support media (WhatsApp) render the image with the caption as one
        message; text-only connectors degrade the Media to its caption (see ``as_text``),
        so either way the user gets the words attached to the picture, not a bare path.
        """
        from gaia.connectors.base import Media
        from gaia.core.screenshots import media_for_outputs

        media = media_for_outputs(events)
        if media:
            # The reply text rides as the caption of the first attachment (one combined
            # message); each file keeps its own caption otherwise (a send_file carries the
            # model's words, screenshots their "screenshot" label).
            caption = "\n".join(t.strip() for t in texts if t.strip())
            for i, item in enumerate(media):
                cap = (caption if i == 0 else "") or item.caption
                log_event("media_out", user=self._user_id, tool=item.kind, chars=len(cap))
                await send(Media(item.path, caption=cap, kind=item.kind))
            return

        # No media: stream each non-empty text part as its own reply (one inbound can fan out
        # to many).
        sent = False
        for text in texts:
            if not text.strip():
                continue
            log_event("message_out", user=self._user_id, chars=len(text))
            await send(text)
            sent = True
        if not sent:
            # The turn ran but produced no text and no media — e.g. a reasoning model that put
            # everything in its (hidden) thoughts and emitted no message. Never ghost the user:
            # log it for visibility and send a short acknowledgement.
            log_event("turn_empty", user=self._user_id, session=self._session_id)
            await send("(Done — I didn't have anything to add there.)")

    async def reset_session(self) -> None:
        """Delete the durable ADK session so the next message starts fresh (used by ``/reset``).

        ``/reset`` calls ``flush()`` first (consolidate to long-term), then this. Long-term memory
        is untouched — only this conversation's thread is cleared.
        """
        self._cancel_idle()
        await self._clear_session()  # delete the durable session + drop the runner
        self._clear_elicitation()  # unpin a paused soul's warm session before wiping the session
        self._pending = None  # drop any unanswered question; /reset starts clean

    def _clear_elicitation(self) -> None:
        """If a delegated soul is paused awaiting an answer, unpin its warm session.

        Defensive ``getattr`` so the lightweight fakes in unit tests (a bare gaia namespace)
        don't need a ``soul_sessions`` attribute.
        """
        soul = self._pending.soul if self._pending else None
        sessions = getattr(self._gaia, "soul_sessions", None)
        if soul is not None and sessions is not None:
            sessions.unpin(soul.warm_key)

    async def _maybe_run_command(self, text: str, send: Send) -> bool:
        """If ``text`` is a slash command, run it and reply; return whether it was one."""
        from gaia.commands import CommandContext, authorize, default_registry, parse

        parsed = parse(text)
        if parsed is None:
            return False
        name, args = parsed

        registry = default_registry(self._gaia.config)
        command = registry.get(name)
        if command is None:
            log_event("command_used", command=name, status="unknown")
            await send(f"Unknown command '/{name}'. Try /help.")
            return True

        ctx = CommandContext(
            args=args,
            gaia=self._gaia,
            handler=self,
            registry=registry,
            user_id=self._user_id,
            session_id=self._session_id,
            role=self._role,
        )
        if refusal := authorize(command, ctx):  # one ACL gate for the human path
            log_event("command_used", command=command.name, status="denied")
            await send(refusal)
            return True
        reply = await command.run(ctx)
        log_event("command_used", command=command.name, status="ok")
        await send(reply)
        return True

    def _arm_idle_consolidate(self) -> None:
        """(Re)start the idle timer; when it fires the conversation is digested into memory."""
        if self._gaia.memory_service is None or not self._gaia.config.memory.auto_ingest:
            return
        self._cancel_idle()
        delay = self._gaia.config.sessions.idle_consolidate_minutes * 60.0
        self._idle_task = asyncio.create_task(self._idle_consolidate(delay))

    async def _idle_consolidate(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return  # a new turn / reset refreshed or cancelled the timer
        # Under the lock so consolidation never races a turn on the same durable session (#315).
        async with self._lock:
            await self.flush()  # consolidate the whole conversation…
            await self._clear_session()  # …then start fresh (human-like)

    def _cancel_idle(self) -> None:
        if self._idle_task is not None and not self._idle_task.done():
            self._idle_task.cancel()

    async def flush(self) -> None:
        """Distil the whole durable session into long-term memory (the conversation's facts).

        Best-effort: the replies are already sent, so a mem0 hiccup is logged and swallowed. No-op
        when memory is off / disabled or the session has no turns. mem0's ``infer`` extracts only
        the important facts (with full conversation context) and dedups against the existing store.
        """
        service = self._gaia.memory_service
        if service is None or not self._gaia.config.memory.auto_ingest:
            return
        session = await self._gaia.session_service.get_session(
            app_name=constants.APP_NAME, user_id=self._user_id, session_id=self._session_id
        )
        if session is None or not session.events:
            return
        try:
            await service.add_session_to_memory(session)  # off-loop; infer=True extraction
        except Exception:
            logging.getLogger(constants.LOGGER_NAME).warning("memory consolidation failed")

    async def _clear_session(self) -> None:
        """Delete the durable session + drop the runner so the next turn starts fresh."""
        try:
            await self._gaia.session_service.delete_session(
                app_name=constants.APP_NAME, user_id=self._user_id, session_id=self._session_id
            )
        except Exception:
            logging.getLogger(constants.LOGGER_NAME).debug("session delete failed", exc_info=True)
        self._runner = None
        self._runner_config = None


def build_handler(
    gaia: Gaia,
    *,
    user_id: str = "gaia-user",
    session_id: str = "gaia-session",
    role: str = "admin",
) -> GaiaHandler:
    """Return a :class:`GaiaHandler` that runs ``text`` through Gaia as ``user_id``."""
    return GaiaHandler(gaia, user_id=user_id, session_id=session_id, role=role)

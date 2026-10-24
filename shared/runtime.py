"""Small uAgents runtime helpers shared by both agents."""

import logging
from uuid import UUID

from uagents import Context, Model
from uagents_core.contrib.protocols.chat import ChatMessage

try:  # needs the uagents[llm] extra (litellm)
    from uagents.experimental.chat_agent.protocol import build_llm_message_history
except ImportError:
    def build_llm_message_history(ctx: Context) -> list[dict[str, str]]:
        """Same logic as uagents.experimental.chat_agent.protocol.build_llm_message_history."""
        history: list[dict[str, str]] = []
        for entry in ctx.session_history() or []:
            if not entry.payload:
                continue
            try:
                hist_msg = ChatMessage.parse_raw(entry.payload)
            except Exception:
                continue
            text = hist_msg.text().strip()
            if text:
                role = "assistant" if entry.sender == ctx.agent.address else "user"
                history.append({"role": role, "content": text})
        return history


def make_send(ctx: Context):
    """Send helper for the brains: send(dest, msg, session=None).

    When `session` is given (the chat session a plan was requested in), the
    message goes out in that session so ASI:One shows it in the same chat even
    when it is sent later from the interval handler. This sets a private
    attribute of the context; if the runtime changes, it falls back to the
    context's own session.
    """
    async def send(dest: str, msg: Model, session: str | None = None) -> None:
        prev = getattr(ctx, "_session", None)
        swapped = False
        if session and prev is not None and str(prev) != str(session):
            try:
                ctx._session = UUID(str(session))
                swapped = True
            except (ValueError, AttributeError):
                pass
        try:
            await ctx.send(dest, msg)
        finally:
            if swapped:
                ctx._session = prev
    return send


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

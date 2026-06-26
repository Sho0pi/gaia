"""Sign in with ChatGPT — Codex-style OAuth + the Responses model backend.

``gaia model`` (ChatGPT sign-in) runs the device-code :func:`login`; then
``llm: { provider: openai, use_oauth: true }`` routes Gaia + souls through :class:`ChatGptOAuthLlm`,
which calls the ChatGPT subscription's Responses backend with the stored credentials.
"""

from gaia.providers.openai.device_auth import login, refresh
from gaia.providers.openai.responses_llm import ChatGptOAuthLlm
from gaia.providers.openai.store import Credentials, load_credentials

__all__ = ["ChatGptOAuthLlm", "Credentials", "load_credentials", "login", "refresh"]

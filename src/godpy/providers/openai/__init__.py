"""Sign in with ChatGPT — Codex-style OAuth + the Responses model backend.

``godpy llm auth openai`` runs the device-code :func:`login`; then
``llm: { provider: openai, use_oauth: true }`` routes God + souls through :class:`ChatGptOAuthLlm`,
which calls the ChatGPT subscription's Responses backend with the stored credentials.
"""

from godpy.providers.openai.device_auth import login, refresh
from godpy.providers.openai.responses_llm import ChatGptOAuthLlm
from godpy.providers.openai.store import Credentials, load_credentials

__all__ = ["ChatGptOAuthLlm", "Credentials", "load_credentials", "login", "refresh"]

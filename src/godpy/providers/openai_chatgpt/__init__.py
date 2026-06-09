"""Sign in with ChatGPT — Codex-style OAuth + the Responses model backend.

``python main.py auth openai-chatgpt`` runs the device-code :func:`login`; then
``llm: { provider: openai-chatgpt }`` routes God + souls through :class:`ChatGptOAuthLlm`,
which calls the ChatGPT subscription's Responses backend with the stored credentials.
"""

from godpy.providers.openai_chatgpt.device_auth import login, refresh
from godpy.providers.openai_chatgpt.responses_llm import ChatGptOAuthLlm
from godpy.providers.openai_chatgpt.store import Credentials, load_credentials

__all__ = ["ChatGptOAuthLlm", "Credentials", "load_credentials", "login", "refresh"]

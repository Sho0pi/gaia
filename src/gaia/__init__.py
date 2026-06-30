"""gaia — Gaia, an AI agent that spawns and reuses task-specific subagents."""

__version__ = "0.1.0a1"


def version() -> str:
    """The installed package version, or the source ``__version__`` when run from a tree."""
    import importlib.metadata

    try:
        return importlib.metadata.version("gaia")
    except importlib.metadata.PackageNotFoundError:  # running from an uninstalled checkout
        return __version__

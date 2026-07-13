from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .claude import ClaudeCodeSession
from .session import SessionAdapter


@dataclass(frozen=True)
class ProviderConfig:
    command: str
    model: str | None


ProviderFactory = Callable[[ProviderConfig], SessionAdapter]

# Provider-specific defaults belong here, rather than in an adapter that may be
# constructed by other callers.
DEFAULT_PROVIDER_MODELS = {"claude": "fable"}


def _claude_adapter(config: ProviderConfig) -> SessionAdapter:
    if not config.model:
        raise ValueError("claude provider requires a model")
    return ClaudeCodeSession(command=config.command, model=config.model)


DEFAULT_PROVIDER_FACTORIES: Mapping[str, ProviderFactory] = {"claude": _claude_adapter}


def build_session_adapter(
    provider: str,
    command: str,
    model: str | None,
    provider_factories: Mapping[str, ProviderFactory] | None = None,
) -> SessionAdapter:
    """Build a selected provider adapter; callers may supply additional providers."""
    factories = dict(DEFAULT_PROVIDER_FACTORIES)
    if provider_factories:
        factories.update(provider_factories)
    try:
        factory = factories[provider]
    except KeyError as error:
        raise ValueError(f"unknown provider: {provider}") from error
    return factory(ProviderConfig(command=command, model=model or DEFAULT_PROVIDER_MODELS.get(provider)))

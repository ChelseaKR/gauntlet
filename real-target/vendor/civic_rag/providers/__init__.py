"""Provider factory. Resolves config → concrete embedding/generation providers,
keeping optional backends (Bedrock, OpenAI-compatible, Anthropic) behind lazy
imports so the offline default stack has zero heavy dependencies.
"""

from __future__ import annotations

from civic_rag.config import Config
from civic_rag.providers.base import EmbeddingProvider, GenerationProvider
from civic_rag.providers.deterministic import ExtractiveGenerator, HashingEmbedding

__all__ = [
    "EmbeddingProvider",
    "GenerationProvider",
    "build_embedding",
    "build_generator",
]


def _build_base_embedding(config: Config) -> EmbeddingProvider:
    provider = config.retrieval.embedding_provider
    if provider == "deterministic":
        return HashingEmbedding(dim=config.retrieval.embedding_dim)
    if provider == "bedrock":  # pragma: no cover - requires AWS credentials
        from civic_rag.providers.bedrock import TitanEmbedding

        return TitanEmbedding(config)
    if provider == "local":  # pragma: no cover - requires the 'local' extra + a pinned model
        from civic_rag.providers.local_onnx import LocalOnnxEmbedding

        return LocalOnnxEmbedding(config)
    raise ValueError(f"unknown embedding provider: {provider!r}")


def build_embedding(config: Config) -> EmbeddingProvider:
    base = _build_base_embedding(config)
    size = config.retrieval.embedding_cache_size
    if size > 0:
        from civic_rag.providers.caching import CachingEmbedding

        return CachingEmbedding(base, size)
    return base


def build_generator(config: Config) -> GenerationProvider:
    provider = config.generation.provider
    if provider == "deterministic":
        return ExtractiveGenerator(
            relevance_floor=config.generation.relevance_floor,
            relevance_floor_by_lang=config.generation.relevance_floor_by_lang,
        )
    if provider == "bedrock":  # pragma: no cover - requires AWS credentials
        from civic_rag.providers.bedrock import BedrockGenerator

        return BedrockGenerator(config)
    if provider == "openai":  # pragma: no cover - requires network + API key
        from civic_rag.providers.openai_compat import OpenAICompatGenerator

        return OpenAICompatGenerator(config)
    if provider == "anthropic":  # pragma: no cover - requires network + API key
        from civic_rag.providers.anthropic_native import AnthropicGenerator

        return AnthropicGenerator(config)
    raise ValueError(f"unknown generation provider: {provider!r}")

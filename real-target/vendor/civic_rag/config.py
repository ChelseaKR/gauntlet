"""Config-over-code. Adopters change ``config/civic-rag.yaml``, not the engine.

Every knob the pipeline reads lives here, validated by pydantic with ``extra =
"forbid"`` so a typo in the YAML fails loudly at load time rather than silently
doing the wrong thing.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CorpusSource(_Model):
    """One named corpus in a multi-corpus deployment.

    ``name`` identifies the corpus (used for its index file and routing); ``path``
    and ``glob`` locate its documents exactly like the single-corpus fields. The
    routing signals — ``description`` and ``keywords`` — are matched against a query
    to decide which corpora to search (see :mod:`civic_rag.routing`). Neither is sent
    to the generator or logged; they only steer retrieval.
    """

    name: str = Field(min_length=1)
    path: str
    glob: str = "**/*.md"
    default_language: str = "en"
    # Free-text summary of what this corpus covers; its words are routing signals.
    description: str = ""
    # Explicit routing terms (program names, synonyms) that should steer a query here.
    keywords: list[str] = Field(default_factory=list)


class RoutingConfig(_Model):
    """How a query is routed across the configured ``corpora``.

    ``route`` (default) scores each corpus by lexical overlap of the query with its
    name/description/keywords and searches only the matching corpora, falling back to
    *all* corpora when nothing matches (so routing never causes a needless refusal).
    ``all`` always searches every corpus and merges. ``max_corpora`` caps how many
    corpora a single query fans out to (0 = no cap).
    """

    strategy: str = "route"
    max_corpora: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check_strategy(self) -> RoutingConfig:
        if self.strategy not in ("route", "all"):
            raise ValueError(f"routing.strategy must be 'route' or 'all', got {self.strategy!r}")
        return self


class CorpusConfig(_Model):
    path: str = "examples/corpus"
    glob: str = "**/*.md"
    default_language: str = "en"
    # Pluggable ingestion connector — the seam between a *source* of public-sector
    # content and the engine's Document model. "filesystem" (default) loads files
    # under ``path`` matching ``glob`` (markdown/text/HTML/PDF). "agency_faq" ingests
    # structured JSONL FAQ records (one citable Q&A per line) — the worked reference in
    # ``civic_rag/connectors/agency_faq.py`` and ``examples/agency-faq/``. Adopters add
    # their own by registering a Connector in ``civic_rag/connectors/`` (see CONNECTORS.md).
    connector: str = "filesystem"
    # Generic bounded JSON API reference connector (FND-06). The endpoint must return
    # either a JSON array of FAQ-like records or {"items": [...]}; each record carries
    # id/question/answer and optional language/category/source_url. Credentials are read
    # from a JSON object in ``http_headers_env`` and never stored here.
    http_url: str = ""
    http_headers_env: str = "CIVIC_RAG_CONNECTOR_HEADERS"
    http_timeout_seconds: float = Field(default=15.0, gt=0.0, le=120.0)
    http_max_bytes: int = Field(default=5_000_000, ge=1, le=100_000_000)
    http_allow_insecure: bool = False
    # Multi-corpus routing (Could-have): when non-empty, the kit indexes each listed
    # corpus separately and routes each query to the relevant one(s) via ``routing``.
    # Empty (default) keeps the single-corpus behavior driven by ``path``/``glob`` above.
    corpora: list[CorpusSource] = Field(default_factory=list)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)

    @model_validator(mode="after")
    def _check_corpora(self) -> CorpusConfig:
        names = [c.name for c in self.corpora]
        if len(names) != len(set(names)):
            raise ValueError("corpus.corpora names must be unique")
        return self


class ChunkConfig(_Model):
    # Token-ish budget per chunk and overlap, measured in whitespace words.
    max_words: int = Field(default=120, ge=20, le=1000)
    overlap_words: int = Field(default=24, ge=0, le=200)

    @model_validator(mode="after")
    def _check_overlap(self) -> ChunkConfig:
        if self.overlap_words >= self.max_words:
            raise ValueError("chunk.overlap_words must be less than chunk.max_words")
        return self


class RetrievalConfig(_Model):
    top_k: int = Field(default=4, ge=1, le=50)
    # Below this cosine score the engine has no grounding and must refuse.
    min_score: float = Field(default=0.12, ge=0.0, le=1.0)
    embedding_dim: int = Field(default=512, ge=64, le=4096)
    # Bounded LRU cache over embeddings, keyed on exact text. Saves recompute when the
    # same query (or chunk) is embedded repeatedly — a real win for high-traffic FAQs and
    # for re-ingesting an unchanged corpus. Deterministic: a cached vector is identical to
    # a freshly computed one. 0 (default) disables it; the value is the max entries kept.
    embedding_cache_size: int = Field(default=0, ge=0, le=1_000_000)
    # "deterministic" = offline hashing baseline (default); "bedrock" = Titan via Bedrock;
    # "local" = a pinned local ONNX sentence-embedding model (offline + deterministic +
    # *semantic*; needs the 'local' extra and a downloaded, sha256-pinned model — see below).
    embedding_provider: str = "deterministic"
    bedrock_embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    bedrock_region: str = "us-east-1"
    # Local ONNX embedding tier (FIX-06). Closes the paraphrase-recall ceiling of the
    # hashing baseline while staying offline and byte-reproducible: fixed weights, no
    # network, no AWS. Weights are NOT bundled (they conflict with clone-and-run) — fetch a
    # MiniLM-class model once and pin it by sha256. All four settings below are required
    # when embedding_provider == "local".
    local_model_path: str | None = None  # path to the exported .onnx model file
    local_tokenizer_path: str | None = None  # path to the matching HF tokenizer.json
    # sha256 of the .onnx file, verified at load so a swapped/corrupted model fails loudly
    # instead of silently degrading retrieval. Get it with `sha256sum model.onnx`.
    local_model_sha256: str | None = None
    # Output width of the pinned model (MiniLM-class == 384). Must match the model.
    local_embedding_dim: int = Field(default=384, ge=8, le=4096)
    # Token budget per text; longer inputs are truncated before embedding.
    local_max_seq_length: int = Field(default=256, ge=8, le=8192)
    # Hybrid retrieval: fuse the vector ranking with a BM25 lexical ranking
    # (Reciprocal Rank Fusion) so a chunk strong in *either* signal can surface.
    # Off by default; applies to stores that can enumerate their chunks (the
    # in-memory default) — pgvector falls back to vector-only.
    hybrid: bool = False
    bm25_k1: float = Field(default=1.5, ge=0.0)
    bm25_b: float = Field(default=0.75, ge=0.0, le=1.0)
    rrf_k: int = Field(default=60, ge=1)
    # SUPERSEDED (EXP-03): the raw-cosine deferral cutoff is replaced by the calibrated
    # confidence tiers below — ``low_confidence`` now follows ``confidence_tier == "low"``
    # from the calibration table, and this knob drives nothing. It stays accepted so
    # existing configs keep loading; ``config-check`` flags it when set. (R36)
    defer_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
    # Civic glossary: when a key term appears in the query, its aliases are appended
    # to the *retrieval* query only (the generation prompt and logs keep the
    # original), so colloquial phrasing ("food stamps") reaches chunks written in
    # official terms ("SNAP"). Directional (term → aliases); empty = no expansion. (E18)
    synonyms: dict[str, list[str]] = Field(default_factory=dict)
    # Near-duplicate collapse: drop a retrieved chunk whose token-set Jaccard overlap
    # with an already-kept (higher-ranked) chunk is at or above this, so repeated
    # boilerplate doesn't crowd out the top_k budget or feed the generator redundant
    # context. The best-scoring member is kept, so grounding is unaffected. 0 = off. (E20)
    dedup_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    # Optional path to a JSON calibration table for confidence scoring (EXP-03). Defaults to
    # the packaged table in civic_rag/data/confidence-calibration.json. Set only if you have
    # a custom-fitted calibration from scripts/fit_confidence_calibration.py.
    confidence_calibration: str | None = None


class GenerationConfig(_Model):
    # "deterministic" = offline extractive (default); "bedrock" = Claude via Bedrock;
    # "openai" = any OpenAI-compatible chat endpoint (see the openai_* knobs below).
    provider: str = "deterministic"
    max_sentences: int = Field(default=4, ge=1, le=20)
    # Immutable Bedrock Runtime model id. Keep this exact: the pricing shim fails
    # closed on undeclared future/revision ids instead of guessing by family.
    bedrock_model_id: str = "anthropic.claude-haiku-4-5-20251001-v1:0"
    bedrock_region: str = "us-east-1"
    # OpenAI-compatible HTTP generation (provider: "openai"): drives the OpenAI API,
    # Azure OpenAI, or any compatible endpoint (vLLM, Together, Groq, …) — the kit's
    # provider-neutral seam. The API key is read from the env var named below; it is
    # never read from or written to config.
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_api_key_env: str = "OPENAI_API_KEY"
    # Pricing (USD per million tokens) for the pre-call cost gate; override per model.
    openai_input_price_per_mtok: float = Field(default=0.15, ge=0.0)
    openai_output_price_per_mtok: float = Field(default=0.60, ge=0.0)
    # Native Anthropic Messages API (provider: "anthropic"): call Claude directly
    # (or via a VPC proxy) instead of through Bedrock. Key from the env var below.
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    anthropic_model: str = "claude-haiku-4-5"
    anthropic_api_key_env: str = "ANTHROPIC_API_KEY"
    anthropic_version: str = "2023-06-01"
    anthropic_input_price_per_mtok: float = Field(default=1.0, ge=0.0)
    anthropic_output_price_per_mtok: float = Field(default=5.0, ge=0.0)
    # Resilience for the HTTP generation providers (openai / anthropic): retry a
    # transient transport error or 429/5xx response this many extra times, with
    # exponential backoff (retry_backoff_seconds * 2**n). 0 (default) keeps the
    # single-attempt, fail-closed behavior; non-retryable 4xx is never retried.
    max_retries: int = Field(default=0, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.5, ge=0.0, le=30.0)
    # Mask PII (emails, SSNs, phone numbers, long bare digit runs) in the query handed
    # to the generator — for network providers this is what gets transmitted. Retrieval
    # and the (fingerprint-only) logs keep the original query. Off by default. (E23)
    redact_query_pii: bool = False
    # Per-conversation cost ceiling (USD), enforced by the pipeline before generation.
    max_cost_usd: float = Field(default=0.01, ge=0.0)
    # Two load-bearing grounding thresholds, surfaced here so adopters tune by config,
    # not by forking the engine (config-over-code). Higher = stricter grounding, more
    # refusals; lower = more permissive, higher hallucination risk on a real LLM path.
    #   * support_overlap — the citation guard drops any sentence whose *content-word
    #     coverage* of its cited chunk is below this, OR whose negation polarity disagrees
    #     with the chunk sentence it restates (verbatim containment always passes).
    #     Coverage is sentence-anchored (fraction of the sentence's content words backed
    #     by the chunk), so honest paraphrase against a long chunk is not penalized, while
    #     an inserted "not" is always caught. This is the safety gate; raise it for terse
    #     corpora, lower it cautiously for jargon- or ES-heavy text.
    #   * relevance_floor — the offline extractive generator only considers a context
    #     sentence sharing at least this fraction of the query's content words. Affects
    #     answer completeness vs. tightness (no effect on LLM providers).
    support_overlap: float = Field(default=0.66, ge=0.0, le=1.0)
    # Runtime paranoid verification (EXP-16): when True, the pipeline re-checks the
    # citation-guard invariant over every fully-built answer *before returning it*, via
    # civic_rag.citation.verify_answer, and raises CitationInvariantError (failing closed)
    # if any served sentence is not grounded in a retrieved chunk. Off by default because
    # the guard already enforces this; turn it on as a defense-in-depth runtime monitor.
    # See docs/CITATION-GUARD-INVARIANT.md.
    paranoid_verify: bool = False
    relevance_floor: float = Field(default=0.34, ge=0.0, le=1.0)
    # Optional per-language overrides for relevance_floor, keyed by language code (e.g. "es").
    # When an answer's resolved language has an entry here, it is used in place of the base
    # relevance_floor above; otherwise the base value is used. Allows tuning answer tightness
    # by language without forking the engine.
    relevance_floor_by_lang: dict[str, float] = Field(default_factory=dict)

    @staticmethod
    def _localized(base: float, overrides: dict[str, float], language: str | None) -> float:
        if language is not None and language in overrides:
            return overrides[language]
        return base

    def relevance_floor_for(self, language: str | None) -> float:
        """The relevance floor in ``language`` if an override exists, else the base."""
        return self._localized(self.relevance_floor, self.relevance_floor_by_lang, language)


class ServerConfig(_Model):
    # Hard cap on accepted question length; longer requests are rejected with 400.
    max_question_chars: int = Field(default=2000, ge=1, le=100_000)
    # Multi-turn memory: when enabled, a request carrying a `session_id` reuses that
    # session's recent questions to improve retrieval for follow-ups (generation still sees
    # only the current question). Off by default; the store is process-local (see
    # civic_rag/session.py). `session_max_turns` bounds how many prior turns are reused.
    session_memory: bool = False
    session_max_turns: int = Field(default=3, ge=1, le=20)
    # Operator tooling: when enabled, POST /admin/reload atomically rebuilds and reindexes
    # the corpus without redeploying. Off by default; must be proxy-authenticated
    # (see docs/PRODUCTION.md). Rebuilding the index while serving does not interrupt
    # the old pipeline until the new one is ready (atomic reference swap in CPython).
    admin_reload: bool = False
    # Request rate limiting: an optional in-process token-bucket limiter keyed per
    # client IP. Off by default (the closed-VPC topology expects rate limiting at the
    # proxy layer; see docs/THREAT-MODEL.md). Enable when running without a fronting
    # proxy. Applied to /api/chat and /api/chat/stream only (all methods); /health
    # and /api/disclosure are never rate-limited.
    rate_limit_enabled: bool = False
    # Refill rate: requests per minute per client IP (used to refill the token bucket).
    rate_limit_requests_per_minute: int = Field(default=60, ge=1)
    # Burst capacity: max tokens in the bucket at once (allows short traffic spikes
    # within the per-minute budget).
    rate_limit_burst: int = Field(default=10, ge=1)
    # Legacy GET streaming route. The preferred streaming endpoint is
    # ``POST /api/chat/stream`` (question travels in the request body, never the URL).
    # ``GET /api/chat/stream?q=…`` writes the resident's question — which can contain
    # pasted PII such as SSNs — into every fronting proxy/CDN access log, quietly voiding
    # the "no conversation content in logs" posture (see docs/THREAT-MODEL.md). The GET
    # route is retained for one deprecation cycle so existing EventSource clients keep
    # working; set this to ``false`` once your frontend uses the POST stream. When left
    # enabled, configcheck emits an info-level notice (see docs/PRODUCTION.md for proxy
    # log-scrubbing guidance for adopters who cannot upgrade immediately).
    legacy_get_stream: bool = True


class LanguageConfig(_Model):
    # Languages the assistant supports end-to-end; the first is the reference.
    supported: list[str] = Field(default_factory=lambda: ["en", "es"])


class PromptConfig(_Model):
    system: str = (
        "You are a public-sector assistant. Answer only from the provided sources, "
        "cite every claim, and say you don't know when the sources don't cover it."
    )
    refusal: str = (
        "I don't have a source that answers that, so I can't say. Try rephrasing, "
        "or contact the agency directly."
    )
    disclosure: str = (
        "This is an AI assistant. Answers are drawn from official sources shown as "
        "citations; it cannot give legal advice or access your personal records."
    )
    # Optional per-language overrides keyed by language code (e.g. "es"). When an
    # answer's resolved language has an entry here, it is used in place of the base
    # string above; otherwise the base (reference-language) string is used. This covers
    # the network-provider system instruction as well as user-facing safety strings,
    # preventing localized answer paths from silently receiving an English prompt.
    system_by_lang: dict[str, str] = Field(default_factory=dict)
    refusal_by_lang: dict[str, str] = Field(default_factory=dict)
    disclosure_by_lang: dict[str, str] = Field(default_factory=dict)

    @staticmethod
    def _localized(base: str, overrides: dict[str, str], language: str | None) -> str:
        if language is not None and language in overrides:
            return overrides[language]
        return base

    def refusal_for(self, language: str | None) -> str:
        """The refusal text in ``language`` if an override exists, else the base."""
        return self._localized(self.refusal, self.refusal_by_lang, language)

    def system_for(self, language: str | None) -> str:
        """The provider system instruction in ``language`` if an override exists."""
        return self._localized(self.system, self.system_by_lang, language)

    def disclosure_for(self, language: str | None) -> str:
        """The disclosure text in ``language`` if an override exists, else the base."""
        return self._localized(self.disclosure, self.disclosure_by_lang, language)


class StoreConfig(_Model):
    # "memory" (default, offline), "pgvector" (built-in), or a dotted import path to a
    # custom backend — "package.module:Factory" — where Factory is a VectorStore class
    # (or a callable taking this Config). That last form makes the store genuinely
    # pluggable (OpenSearch, Pinecone, ...) without forking the engine (ADR-K4).
    backend: str = "memory"
    index_path: str = "var/index"
    # The pgvector DSN is a secret (it carries the DB password): prefer reading it from
    # this env var at startup — like the provider API keys — rather than committing it to
    # config. The literal `pgvector_dsn` below stays as an explicit fallback for local/dev.
    pgvector_dsn_env: str = "PGVECTOR_DSN"
    pgvector_dsn: str = ""


class AuditConfig(_Model):
    # Opt-in, PII-free audit trail: one JSON line per answered/refused query, separate from
    # the operational logs. Off by default. Records metadata only (timestamp, query
    # fingerprint, language, refusal/low-confidence, citation/sentence counts) — never the
    # query or answer text — so it satisfies records obligations without logging content.
    enabled: bool = False
    path: str = "var/audit/audit.jsonl"
    # Drop audit records older than this many days (checked at startup and on demand).
    # 0 = keep forever (no time-based pruning).
    retention_days: int = Field(default=0, ge=0)


class HandoffConfig(_Model):
    # Human-handoff hook: where a consent-gated handoff record is *dispatched* after it is
    # built (the record content itself is always returned by POST /api/handoff). This is the
    # pluggable integration point for routing a conversation to a human caseworker.
    #   * "none" (default) — no dispatch; the caller pulls the record from the API response.
    #   * "file" — append the record as one JSON line to `file_path` (offline reference hook;
    #     a caseworker tool tails the file). Real destinations (ticket queue, inbox, bus) are
    #     adopter-implemented behind the same HandoffSink protocol (civic_rag/handoff_sink.py).
    # Transport, auth, and retention for a real sink are adopter obligations.
    sink: str = "none"
    file_path: str = "var/handoff/handoff.jsonl"


class ObservabilityConfig(_Model):
    # Detect and log (as a PII-free `injection_attempt` event) queries that look like
    # prompt-injection attempts. Off by default; the citation guard already neutralizes
    # injection structurally — this is operator *visibility*, surfaced in `log-stats`.
    detect_injection: bool = False


class KioskConfig(_Model):
    """Single-device, no-internet kiosk profile (EXP-17): a shared public terminal in a
    library or community center, used by a stream of unrelated residents back-to-back.

    Off by default. Enabling it does not change retrieval/generation — it only turns on
    the front-end idle-reset UX (``GET /api/kiosk-config`` is polled by ``web/dist/kiosk.js``)
    and documents the paired server settings a kiosk deployment must also set (see
    ``config/kiosk.yaml`` and ``docs/KIOSK.md``): ``server.session_memory: false``
    and ``audit.enabled: false``, so no trace of one resident's questions is available to
    the next.
    """

    enabled: bool = False
    # Seconds of no touch/pointer/keyboard input before the session is auto-reset
    # (transcript cleared, page reloaded) for the next resident. WCAG 2.2.1 (Timing
    # Adjustable) requires either an essential-timeout exception or a warning + simple
    # way to extend; a shared-privacy kiosk reset is an essential timeout (2.2.1
    # exception 3: the timeout is essential and extending it would invalidate the
    # activity), but the UI still warns and offers a one-tap "Continue" as a courtesy.
    idle_timeout_seconds: int = Field(default=90, ge=10, le=3600)
    # How long before the idle timeout the warning dialog appears (and how long the
    # resident has to tap "Continue" before the reset fires). Must be < idle_timeout_seconds.
    warning_seconds: int = Field(default=20, ge=5, le=600)

    def validate_timeouts(self) -> None:
        """Raise if warning_seconds does not leave room before idle_timeout_seconds."""
        if self.warning_seconds >= self.idle_timeout_seconds:
            raise ValueError(
                "kiosk.warning_seconds must be less than kiosk.idle_timeout_seconds "
                f"(got warning_seconds={self.warning_seconds}, "
                f"idle_timeout_seconds={self.idle_timeout_seconds})"
            )


class Config(_Model):
    corpus: CorpusConfig = Field(default_factory=CorpusConfig)
    chunk: ChunkConfig = Field(default_factory=ChunkConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    language: LanguageConfig = Field(default_factory=LanguageConfig)
    prompts: PromptConfig = Field(default_factory=PromptConfig)
    store: StoreConfig = Field(default_factory=StoreConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    handoff: HandoffConfig = Field(default_factory=HandoffConfig)
    kiosk: KioskConfig = Field(default_factory=KioskConfig)

    @model_validator(mode="after")
    def _check_kiosk_timeouts(self) -> Config:
        self.kiosk.validate_timeouts()
        return self


def load_config(path: str | Path) -> Config:
    """Load and validate a config YAML. Missing file → an explicit error."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"config not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"config root must be a mapping, got {type(raw).__name__}")
    return Config.model_validate(raw)

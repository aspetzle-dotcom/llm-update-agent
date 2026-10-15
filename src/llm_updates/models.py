from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .util import canonical_url, clean, date

MAX_ENTRIES = 10  # stories per digest; matches the digest_entries position CHECK


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)
    schema_version: Literal["1"] = "1"


class NormalizedItem(Contract):
    source_id: str
    external_id: str
    canonical_key: str
    canonical_url: str
    title: str = Field(min_length=1, max_length=1000)
    kind: Literal["arxiv", "github", "lab", "hf"]
    fetched_at: str
    content_text: str = Field(min_length=1, max_length=250_000)
    content_scope: Literal["abstract-only", "release-notes-only", "article-body"]
    raw_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_at: str | None = None
    updated_at: str | None = None
    publisher_version: str | None = None
    raw_published_at: str | None = None
    raw_updated_at: str | None = None
    authors: list[str] = Field(default_factory=list)
    related_keys: list[str] = Field(default_factory=list)
    withdrawn: bool = False
    correction: bool = False

    @field_validator("title", "content_text")
    @classmethod
    def nonempty_clean(cls, value: str) -> str:
        value = clean(value)
        if not value:
            raise ValueError("empty normalized content")
        return value

    @field_validator("canonical_url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        return canonical_url(value)

    @field_validator("fetched_at", "published_at", "updated_at")
    @classmethod
    def utc_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        result = date(value)
        if result is None:
            raise ValueError("invalid date; use null and preserve raw date")
        return result


class CollectorResult(Contract):
    items: list[NormalizedItem]
    cursor: str | None = None
    complete: bool = True
    warnings: list[str] = Field(default_factory=list)
    retry_after: str | None = None


class NumericContext(Contract):
    number: str
    unit: str
    baseline: str
    conditions: str


class Claim(Contract):
    text: str = Field(min_length=1, max_length=3000)
    kind: Literal["source_fact", "interpretation"]
    evidence_ids: list[str] = Field(max_length=5)
    numeric_context: list[NumericContext] = Field(default_factory=list, max_length=10)


class DraftEntry(Contract):
    story_id: str
    title: str
    what_changed: str
    why_relevant: str
    concept_explanation: str
    claims: list[Claim] = Field(min_length=1, max_length=10)
    uncertainties: list[str] = Field(max_length=5)


class DigestDraft(Contract):
    entries: list[DraftEntry] = Field(max_length=MAX_ENTRIES)


class ToolRequest(Contract):
    name: Literal[
        "list_candidates", "retrieve_passages", "fetch_registered_item", "get_previous_coverage"
    ]
    run_id: str | None = None
    version_ids: list[str] = Field(default_factory=list, max_length=12)
    query: str = Field(default="", max_length=300)
    limit: int = Field(default=3, ge=1, le=12)
    candidate_id: str | None = None
    link_id: str | None = None
    story_id: str | None = None


class AgentAction(Contract):
    tool: ToolRequest | None = None
    final: DigestDraft | None = None

    @model_validator(mode="after")
    def exactly_one(self) -> AgentAction:
        if (self.tool is None) == (self.final is None):
            raise ValueError("return exactly one tool or final")
        return self


class ModelResult(Contract):
    action: AgentAction | None = None
    input_tokens: int = Field(ge=0, default=0)
    output_tokens: int = Field(ge=0, default=0)
    cached_tokens: int = Field(ge=0, default=0)
    reasoning_tokens: int = Field(ge=0, default=0)
    finish_reason: str
    request_id: str | None = None
    retryable: bool = False
    raw_response: dict = Field(default_factory=dict)

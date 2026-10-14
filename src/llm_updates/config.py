from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .models import Contract


class Source(Contract):
    source_id: str = Field(pattern=r"^[a-z0-9_-]+$")
    kind: Literal["arxiv", "github", "lab"]
    name: str
    endpoint: str
    enabled: bool = True
    allowed_paths: dict[str, list[str]]
    category: str | None = None
    repository: str | None = None
    index_selector: str = "main a[href]"
    article_selector: str = "article"
    article_path: str = "/"
    skip_prereleases: bool = True


class Topic(Contract):
    weight: float = Field(ge=0, le=3)
    keywords: list[str]


class Profile(Contract):
    topics: dict[str, Topic]
    excluded_topics: list[str] = Field(default_factory=list)
    depth: str = "Explain LLM systems terms; assume Python and mathematics familiarity."
    reading_minutes: int = Field(default=10, ge=1, le=60)
    concepts_to_revisit: list[str] = Field(default_factory=list)


class Limits(Contract):
    candidates_per_source: int = Field(default=100, ge=1, le=1000)
    lookback_days: int = Field(default=7, ge=1, le=30)
    overlap_hours: int = Field(default=48, ge=0, le=168)
    source_timeout_seconds: int = Field(default=90, ge=1, le=600)
    wall_seconds: int = Field(default=600, ge=1, le=3600)
    response_bytes: int = Field(default=2_000_000, ge=1024, le=10_000_000)
    model_calls: int = Field(default=8, ge=0, le=8)
    tool_calls: int = Field(default=12, ge=0, le=12)
    network_enrichments: int = Field(default=4, ge=0, le=4)
    call_input_tokens: int = Field(default=12000, ge=0, le=60000)
    call_output_tokens: int = Field(default=2000, ge=0, le=12000)
    total_input_tokens: int = Field(default=60000, ge=0)
    total_output_tokens: int = Field(default=12000, ge=0)
    run_usd: float = Field(default=0.5, ge=0)
    month_usd: float = Field(default=5, ge=0)


class Provider(Contract):
    provider: Literal["openai"] = "openai"
    model: str = ""
    currency: Literal["USD"] = "USD"
    checked_at: str = ""
    context_window: int = Field(default=0, ge=0)
    input_per_million: float | None = Field(default=None, ge=0)
    cached_input_per_million: float | None = Field(default=None, ge=0)
    output_per_million: float | None = Field(default=None, ge=0)


class Config(Contract):
    source_set_version: str = "2026-10-14.1"
    database: str = "data/updates.sqlite3"
    output_dir: str = "data/digests"
    user_agent: str = "personal-llm-updates/0.1 (local research; manual collection)"
    minimum_relevance: float = Field(default=0.15, ge=0, le=1)
    recency_half_life_days: float = Field(default=7, gt=0)
    source_family_cap: int = Field(default=3, ge=1, le=5)
    limits: Limits = Field(default_factory=Limits)
    provider: Provider = Field(default_factory=Provider)
    profile: Profile
    sources: list[Source]

    @model_validator(mode="after")
    def unique_sources(self) -> Config:
        if len({s.source_id for s in self.sources}) != len(self.sources):
            raise ValueError("duplicate source ID")
        if len({s.endpoint for s in self.sources}) != len(self.sources):
            raise ValueError("duplicate source endpoint")
        return self


def load(path: Path, profile: Path | None = None) -> Config:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if profile:
        data["profile"] = tomllib.loads(profile.read_text(encoding="utf-8"))
    result = Config.model_validate(data)
    result.database = str((path.parent / result.database).resolve())
    result.output_dir = str((path.parent / result.output_dir).resolve())
    return result

from __future__ import annotations

import os
from typing import Protocol

from pydantic import ValidationError

from .models import AgentAction, ModelResult


class ProviderAdapter(Protocol):
    def generate(self, request: dict, timeout: float) -> ModelResult: ...


def strict_schema():
    def convert(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                convert(value)
        elif isinstance(node, list):
            for value in node:
                convert(value)

    schema = AgentAction.model_json_schema()
    convert(schema)
    return schema


class OpenAIAdapter:
    def __init__(self):
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is not set")
        import httpx
        from openai import OpenAI

        self.client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url="https://api.openai.com/v1",
            max_retries=0,
            http_client=httpx.Client(trust_env=False),
        )

    def generate(self, request: dict, timeout: float) -> ModelResult:
        response = self.client.responses.create(**request, timeout=timeout)
        usage = response.usage
        raw = response.model_dump(mode="json")
        action = None
        reason = response.status
        if any(
            c.type == "refusal"
            for output in response.output
            if output.type == "message"
            for c in output.content
        ):
            reason = "refusal"
        elif response.status == "completed":
            try:
                action = AgentAction.model_validate_json(response.output_text)
            except (ValidationError, ValueError):
                reason = "invalid_json"
        if not usage:
            raise ValueError("provider returned no usage; retain reservation")
        return ModelResult(
            action=action,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=getattr(usage.input_tokens_details, "cached_tokens", 0) or 0,
            reasoning_tokens=getattr(usage.output_tokens_details, "reasoning_tokens", 0) or 0,
            finish_reason=reason,
            request_id=getattr(response, "_request_id", None),
            raw_response=raw,
        )

"""The one module allowed to import an LLM vendor SDK.

Every other module that wants a model call goes through `LLMClient` instead of
talking to a provider directly. Swapping providers means writing one new class
with the same `complete_json` method and changing what `build_client` returns --
no changes anywhere else.
"""

from typing import Protocol, TypeVar

from pydantic import BaseModel

from artemis.settings import Settings

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMError(Exception):
    """Any provider failure: transport, auth, quota, or output that won't validate."""


class LLMClient(Protocol):
    def complete_json(self, prompt: str, schema: type[SchemaT]) -> SchemaT:
        """Return the model's response parsed and validated against `schema`.

        Raises LLMError on any failure, including a response that does not
        validate -- callers never see provider-specific exceptions.
        """
        ...


class GeminiClient:
    def __init__(self, api_key: str, model: str, timeout: float):
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def complete_json(self, prompt: str, schema: type[SchemaT]) -> SchemaT:
        try:
            from google import genai
            from google.genai import types
        except ImportError as error:
            raise LLMError("google-genai is not installed") from error

        try:
            client = genai.Client(
                api_key=self._api_key,
                http_options=types.HttpOptions(timeout=int(self._timeout * 1000)),
            )
            response = client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            )
        except Exception as error:
            raise LLMError(f"Gemini request failed: {error}") from error

        parsed = response.parsed
        if parsed is None:
            raise LLMError("Gemini response did not match the requested schema")
        return parsed


def build_client(settings: Settings) -> LLMClient | None:
    """A client when an API key is configured, else None (LLM features off)."""

    if settings.llm_api_key is None:
        return None
    return GeminiClient(
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout=settings.llm_timeout_seconds,
    )

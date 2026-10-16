"""The model the agent answers with: an Azure OpenAI deployment made from settings or the environment (or any
LangChain chat model passed in), its name, what its tokens cost, and what a failed call to it means. Another
provider (a model on Amazon Bedrock, OpenAI itself) gets its own maker here, next to azure_chat_model()."""

from __future__ import annotations

import os
from typing import Any, Callable

from .._kit.deps import _require
from .._kit.errors import _Hint

AZURE_API_VERSION = "2024-10-21"  # the Azure OpenAI API version used when neither api_version= nor the environment
# (OPENAI_API_VERSION, AZURE_OPENAI_API_VERSION) names one: a GA version that supports tool calling


def azure_settings(deployment: str | None = None, endpoint: str | None = None,
                   api_version: str | None = None) -> tuple[str | None, str | None, str]:
    """(deployment, endpoint, api_version): the ones given, else AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_ENDPOINT and
    OPENAI_API_VERSION (or AZURE_OPENAI_API_VERSION), else AZURE_API_VERSION for the version."""
    return (deployment or os.environ.get("AZURE_OPENAI_DEPLOYMENT") or None,
            endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT") or None,
            api_version or os.environ.get("OPENAI_API_VERSION") or os.environ.get("AZURE_OPENAI_API_VERSION")
            or AZURE_API_VERSION)


def azure_chat_model(*, endpoint: str | None, deployment: str | None, api_version: str, api_key: str | None = None,
                     token_provider: Callable[[], str] | None = None, max_tokens: int | None = None) -> Any:
    """LangChain's AzureChatOpenAI for a deployment. The key is api_key, else AZURE_OPENAI_API_KEY, or a Microsoft
    Entra ID token_provider instead. _Hint (a note, not a traceback) says which settings are missing."""
    missing = [env for env, value in (("AZURE_OPENAI_ENDPOINT", endpoint),
                                      ("AZURE_OPENAI_DEPLOYMENT", deployment)) if not value]
    if not (api_key or token_provider or os.environ.get("AZURE_OPENAI_API_KEY")):
        missing.append("AZURE_OPENAI_API_KEY")
    if missing:
        raise _Hint(
            f"The agent needs an Azure OpenAI deployment to answer with, and {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} not set. Set them in the notebook's environment "
            "(os.environ['AZURE_OPENAI_ENDPOINT'] = 'https://<resource>.openai.azure.com', and the key and the "
            "deployment's name), or pass them: AgentView(endpoint=..., deployment=..., api_key=...)."
        )
    openai = _require("langchain_openai", "The agent's Azure OpenAI model", package="langchain-openai")
    settings: dict[str, Any] = {"azure_endpoint": endpoint, "azure_deployment": deployment,
                                "api_version": api_version, "max_retries": 3}
    if max_tokens:
        settings["max_tokens"] = max_tokens
    if api_key:
        settings["api_key"] = api_key
    if token_provider:
        settings["azure_ad_token_provider"] = token_provider
    return openai.AzureChatOpenAI(**settings)


def model_name(chat_model: Any, deployment: str | None = None) -> str:
    """The deployment or model that answers: the chat model's own name when there is one, else the Azure
    deployment's."""
    if chat_model is not None:
        for attribute in ("deployment_name", "model_name", "model_id", "model"):
            value = getattr(chat_model, attribute, None)
            if isinstance(value, str) and value:
                return value
        return type(chat_model).__name__
    return deployment or ""


def answer_cost(model: str, input_tokens: int, output_tokens: int,
                model_prices: dict[str, tuple[float, float]] | None) -> float | None:
    """Estimated USD for the tokens: model_prices maps a deployment or model name (the longest that appears in
    `model` wins) to (USD per 1M input tokens, per 1M output tokens). None when no price matches."""
    if not model_prices or not model:
        return None
    wanted = model.lower()
    for key in sorted(model_prices, key=len, reverse=True):
        if key.lower() in wanted:
            price_in, price_out = model_prices[key]
            return input_tokens / 1e6 * price_in + output_tokens / 1e6 * price_out
    return None


def explain_model_error(exc: BaseException) -> str:
    """What a failed model call means and what to do, from the error the OpenAI client raised (by its class name,
    so this needs no openai import)."""
    name = type(exc).__name__
    text = " ".join(str(exc).split())
    if len(text) > 300:
        text = text[:299] + "…"
    status = getattr(exc, "status_code", None)
    if name == "AuthenticationError" or status == 401:
        why = ("Azure OpenAI refused the key: check AZURE_OPENAI_API_KEY (or api_key=) is a key of this resource, "
               "and AZURE_OPENAI_ENDPOINT its endpoint.")
    elif name == "PermissionDeniedError" or status == 403:
        why = ("Azure OpenAI refused access: the key or identity can't use this deployment, or the resource's "
               "network rules block this notebook.")
    elif name == "NotFoundError" or status == 404:
        why = ("No such deployment on this endpoint: AZURE_OPENAI_DEPLOYMENT (or deployment=) is the deployment's "
               "name in Azure AI Foundry, not the model's; an old api_version can also give a 404.")
    elif name == "RateLimitError" or status == 429:
        why = "The deployment's rate limit was hit: wait a minute and ask again, or raise its tokens-per-minute quota."
    elif "content_filter" in text or "content management policy" in text.lower():
        why = ("Azure's content filter blocked the prompt or the answer. Log lines can set it off: ask about a "
               "narrower window, or adjust the deployment's content filter.")
    elif name == "BadRequestError" or status == 400:
        why = ("The deployment rejected the request: it may not support tool calling, or api_version may be too "
               "old for it.")
    elif name in ("APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout"):
        why = "Couldn't reach the endpoint from this notebook: check AZURE_OPENAI_ENDPOINT and the network."
    elif name == "GraphRecursionError":
        why = "The agent took more steps than its budget allows."
    else:
        why = ""
    return f"{name}: {text}" + (f" {why}" if why else "")

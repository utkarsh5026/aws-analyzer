"""
The model the agent answers with, from whichever provider the notebook can use: a model on Amazon Bedrock (called
with the notebook's own AWS credentials, so nothing leaves AWS), OpenAI or any server that speaks its API (a LiteLLM
or vLLM gateway, OpenRouter), an Azure OpenAI deployment, any other provider LangChain's init_chat_model knows
('anthropic:claude-sonnet-4-5', 'google_genai:gemini-2.5-pro', 'ollama:llama3.1'), or a LangChain chat model passed
in. pick_provider() works out which from the settings and the environment; the makers here build it; model_name(),
model_rates(), answer_cost(), model_place() and explain_model_error() name it, price its tokens, say where what it's
sent goes and what a failed call to it means. A provider that needs handling of its own (a key, a client, its
errors) gets an entry in PROVIDERS and a maker here; any other goes through init_chat_model as it is.

PROVIDERS describes the providers reports name by title and that have a default model or a key of their own.
MODEL_ENV (AWS_ANALYZER_AGENT_MODEL) names the model for every notebook that doesn't pass one ('bedrock:sonnet',
'openai:gpt-4.1'). OPENAI_MODEL is what OpenAI answers with when no model is named, and Bedrock's is _bedrock's
DEFAULT_MODEL (Claude Haiku 4.5), found in the region's model list. AZURE_API_VERSION is the Azure OpenAI API version
used when neither api_version= nor the environment (OPENAI_API_VERSION, AZURE_OPENAI_API_VERSION) names one: a GA
version that supports tool calling.
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, NamedTuple

from botocore.exceptions import ClientError

from .._bedrock.models import ModelInfo
from .._bedrock.prices import DEFAULT_MODEL, MODEL_PRICES, _MODEL_ALIASES, _family_match, model_price, short_model
from .._kit.deps import _require
from .._kit.errors import _error_code, _Hint

AZURE_API_VERSION = "2024-10-21"
OPENAI_MODEL = "gpt-4.1"
MODEL_ENV = "AWS_ANALYZER_AGENT_MODEL"
_RETRIES = 3
_ERROR_CHARACTERS = 300
_NO_KEY = "not-needed"
_CLOSE_MODELS = 3


@dataclass(frozen=True)
class Provider:
    """
    A provider the agent can answer with, as reports name it.

        name      what provider= takes, and what model='<name>:...' starts with: 'bedrock'
        title     how reports name it: 'Amazon Bedrock'
        key_env   the environment variable its key is read from ('' when it needs none)
        default   the model it answers with when none is named ('' when one must be)
        example   a model to show in a hint
        place     where what the agent sends goes, for the note on what leaves AWS ({model} is filled in)
    """

    name: str
    title: str
    key_env: str = ""
    default: str = ""
    example: str = ""
    place: str = "{title} ({model}), outside AWS"


PROVIDERS: dict[str, Provider] = {
    "bedrock": Provider("bedrock", "Amazon Bedrock", default=DEFAULT_MODEL, example="sonnet",
                        place="Amazon Bedrock in your own AWS account ({model}), so it stays in AWS"),
    "openai": Provider("openai", "OpenAI", "OPENAI_API_KEY", OPENAI_MODEL, OPENAI_MODEL),
    "azure": Provider("azure", "Azure OpenAI", "AZURE_OPENAI_API_KEY", example="prod-gpt-4o",
                      place="your Azure OpenAI deployment ({model}), outside AWS"),
    "anthropic": Provider("anthropic", "Anthropic", "ANTHROPIC_API_KEY", example="claude-sonnet-4-5"),
    "google_genai": Provider("google_genai", "Google Gemini", "GOOGLE_API_KEY", example="gemini-2.5-pro"),
    "ollama": Provider("ollama", "Ollama", example="llama3.1", place="your Ollama server ({model})"),
}
_CHAT_MODEL = Provider("", "the chat model you passed", place="your chat model ({model})")
_ALIASES = {"azure_openai": "azure", "bedrock_converse": "bedrock"}
_LANGCHAIN_PROVIDERS = frozenset({
    "anthropic", "anthropic_bedrock", "azure_ai", "azure_openai", "baseten", "bedrock", "bedrock_converse",
    "bedrock_mantle_anthropic", "bedrock_mantle_openai", "cohere", "deepseek", "fireworks", "google_anthropic_vertex",
    "google_genai", "google_vertexai", "groq", "huggingface", "ibm", "litellm", "meta", "mistralai", "nvidia",
    "ollama", "openai", "openrouter", "perplexity", "together", "upstage", "xai",
})
_PREFIXES = _LANGCHAIN_PROVIDERS | set(PROVIDERS)
_CHAT_MODEL_CLASSES = {
    "AzureChatOpenAI": "azure",
    "ChatOpenAI": "openai",
    "ChatBedrockConverse": "bedrock",
    "ChatBedrock": "bedrock",
    "ChatAnthropic": "anthropic",
    "ChatGoogleGenerativeAI": "google_genai",
    "ChatOllama": "ollama",
}
_OPENAI_NAMES = ("gpt-", "chatgpt", "o1", "o3", "o4")
_BEDROCK_ID = re.compile(r"^(?:arn:aws[\w-]*:bedrock:|(?:[a-z]{2,6}(?:-gov)?\.)?[a-z0-9]+\.[a-z])")
_BEDROCK_PROFILE = re.compile(r"^(?:arn:|(?:us|eu|apac|ap|ca|jp|au|sa|global|us-gov)\.)")
_BEDROCK_VERSIONED = re.compile(r"(?:-v\d+(?::\d+)?|:\d+)$")


def provider_info(provider: str) -> Provider:
    """
    What reports say about a provider: its PROVIDERS entry, else one named after it ('groq'), or the chat model
    passed in for ''.
    """
    if not provider:
        return _CHAT_MODEL
    return PROVIDERS.get(provider) or Provider(provider, provider)


def provider_names() -> list[str]:
    """
    Every provider the agent can answer with, as provider= takes it: PROVIDERS first, then the other providers
    LangChain's init_chat_model knows, A to Z (without its second names for Azure OpenAI and Bedrock).
    """
    others = sorted(_LANGCHAIN_PROVIDERS - set(PROVIDERS) - set(_ALIASES))
    return [*PROVIDERS, *others]


def provider_of(chat_model: Any) -> str:
    """
    The provider a LangChain chat model is from, by its class ('bedrock' for ChatBedrockConverse); '' when the
    class isn't one of the providers it knows.
    """
    return _CHAT_MODEL_CLASSES.get(type(chat_model).__name__, "")


def _named_provider(name: str) -> str:
    """
    provider= as the agent keeps it: lower case, and LangChain's 'azure_openai' / 'bedrock_converse' as 'azure' /
    'bedrock'.
    """
    name = name.strip().lower()
    return _ALIASES.get(name, name)


def _split_prefix(model: str) -> tuple[str, str]:
    """
    'openai:gpt-4.1' -> ('openai', 'gpt-4.1'); a model whose part before the first colon isn't a provider LangChain
    knows ('us.anthropic.claude-haiku-4-5-20251001-v1:0', 'llama3.1:8b') -> ('', model).
    """
    prefix, colon, rest = model.partition(":")
    if colon and rest and prefix.strip().lower() in _PREFIXES:
        return _named_provider(prefix), rest.strip()
    return "", model.strip()


def is_bedrock_model(model: str) -> bool:
    """
    Whether a model's name is Bedrock's: a model ID or inference profile ('anthropic.claude-...',
    'us.amazon.nova-pro-v1:0'), an ARN, a short name bedrock_chat takes too ('sonnet', 'nova') or a family in
    MODEL_PRICES ('claude-haiku-4-5', 'nova-pro').
    """
    lowered = model.strip().lower()
    return bool(_BEDROCK_ID.match(lowered)) or lowered in _MODEL_ALIASES or lowered.startswith(tuple(MODEL_PRICES))


def _azure_in(environ: Mapping[str, str]) -> bool:
    """
    Whether the environment sets up an Azure OpenAI deployment.
    """
    return bool(environ.get("AZURE_OPENAI_ENDPOINT") or environ.get("AZURE_OPENAI_DEPLOYMENT"))


def _provider_for_model(model: str, *, azure: bool, environ: Mapping[str, str]) -> tuple[str, str]:
    """
    (provider, model) for a model named without provider=: the provider before its colon, else what its name says
    (see pick_provider()), else Azure when it's set up (the model is then the deployment) or OpenAI when there's a
    key for it; '' when nothing says.
    """
    provider, model = _split_prefix(model)
    lowered = model.lower()
    if provider:
        return provider, model
    if lowered.startswith("claude"):
        return ("anthropic" if environ.get("ANTHROPIC_API_KEY") else "bedrock"), model
    if is_bedrock_model(model):
        return "bedrock", model
    if lowered.startswith(_OPENAI_NAMES):
        return ("azure" if azure else "openai"), model
    if lowered.startswith("gemini"):
        return "google_genai", model
    if azure:
        return "azure", model
    return ("openai" if environ.get("OPENAI_API_KEY") else ""), model


def pick_provider(  # noqa: PLR0913
    provider: str | None = None,
    model: str | None = None,
    *,
    chat_model: Any = None,
    azure: bool = False,
    api_key: bool = False,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """
    (provider, model) the agent answers with, from what it was given, in this order:

        chat_model=             the provider it's from ('bedrock' for ChatBedrockConverse; '' for another)
        provider=               that one ('azure_openai' and 'bedrock_converse', LangChain's names, work too)
        model='openai:gpt-4.1'  the provider before the colon, when it's one LangChain knows
        model= alone            the provider its name says: a Bedrock model ID, profile, ARN, short name ('sonnet')
                                or family ('nova-pro') is Bedrock's; 'gpt-...' and 'o3' OpenAI's (Azure's deployment
                                when Azure is set up); 'claude-...' Anthropic's when ANTHROPIC_API_KEY is set, else
                                Bedrock's; 'gemini-...' Google's
        `azure`                 deployment=, endpoint= or azure_ad_token_provider= was given: Azure OpenAI
        MODEL_ENV               AWS_ANALYZER_AGENT_MODEL, read like model=, when nothing above names one
        the environment         AZURE_OPENAI_ENDPOINT or AZURE_OPENAI_DEPLOYMENT: Azure; then `api_key` (api_key=
                                was given) or OPENAI_API_KEY: OpenAI

    The provider is '' when nothing says, and the model '' when none is named (the provider's default applies).
    """
    environ = os.environ if environ is None else environ
    if chat_model is not None:
        return provider_of(chat_model), model or ""
    if provider:
        named = _named_provider(provider)
        prefix, rest = _split_prefix(model or "")
        return named, rest if prefix == named else (model or "")
    is_azure = azure or _azure_in(environ)
    if model:
        return _provider_for_model(model, azure=is_azure, environ=environ)
    if azure:
        return "azure", ""
    wanted = (environ.get(MODEL_ENV) or "").strip()
    if wanted:
        return _provider_for_model(wanted, azure=is_azure, environ=environ)
    if is_azure:
        return "azure", ""
    return ("openai" if api_key or environ.get("OPENAI_API_KEY") else ""), ""


def no_model_hint(model: str = "") -> str:
    """
    What to do when no provider is set up (or none serves `model`): each way to give the agent a model, as a call
    to copy.
    """
    if model:
        return (f"The agent can't tell which provider serves {model!r}: name it, AgentView(provider='ollama', "
                f"model={model!r}), or put it before the model, AgentView(model='ollama:{model}').")
    return ("The agent needs a model to answer with, and none is set up. A model on Amazon Bedrock, with this "
            "notebook's AWS credentials: AgentView(provider='bedrock') (Claude Haiku 4.5; model='sonnet' or any "
            "Bedrock model ID for another). OpenAI: set OPENAI_API_KEY, then AgentView(model='gpt-4.1'). Azure "
            "OpenAI: set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY and AZURE_OPENAI_DEPLOYMENT. Any provider "
            "LangChain supports: AgentView(model='anthropic:claude-sonnet-4-5'), or chat_model= with a LangChain "
            f"chat model. To set one for every notebook, set {MODEL_ENV} (os.environ['{MODEL_ENV}'] = "
            "'bedrock:sonnet'). AgentView().setup() shows each step as a cell to copy.")


def bedrock_model_id(wanted: str, models: list[ModelInfo]) -> str:
    """
    The ID or inference profile to call a Bedrock model with, from the region's model list (_bedrock's
    parse_models()): its own ID or profile as given, else the model of the family or short name it names ('haiku',
    'claude-sonnet-4-5'), on demand or through the region's profile, the shortest active one first. _Hint names the
    closest models when nothing matches.
    """
    for info in models:
        if wanted in (info.id, info.invoke_id):
            return info.invoke_id
    family = _MODEL_ALIASES.get(wanted.lower(), wanted)
    usable = [info for info in models if info.via != "provisioned only"]
    matches = [info for info in usable if _family_match(family, info.id) or _family_match(family, info.invoke_id)]
    if matches:
        best = min(matches, key=lambda info: (info.status != "ACTIVE", len(info.id), info.id))
        return best.invoke_id
    close = difflib.get_close_matches(short_model(family).lower(), [short_model(info.id) for info in usable],
                                      n=_CLOSE_MODELS, cutoff=0.5)
    hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
    raise _Hint(f"No Amazon Bedrock model matching {wanted!r} here.{hint} Pass a model ID or inference profile "
                "(BedrockChatView().models() lists them, with the ID to pass), like "
                "AgentView(provider='bedrock', model='us.anthropic.claude-haiku-4-5-20251001-v1:0').")


def is_bedrock_profile(model: str) -> bool:
    """
    Whether a Bedrock model is named in a form that's called as it is: an ARN, or a cross-region inference profile
    ('us.', 'eu.', 'global.' ... before the model ID).
    """
    return bool(_BEDROCK_PROFILE.match(model.strip().lower()))


def is_bedrock_version(model: str) -> bool:
    """
    Whether a Bedrock model ID names its version ('anthropic.claude-3-haiku-20240307-v1:0'), so it can be called as
    given when the model list can't be read.
    """
    return bool(_BEDROCK_VERSIONED.search(model.strip()))


def azure_settings(deployment: str | None = None, endpoint: str | None = None,
                   api_version: str | None = None) -> tuple[str | None, str | None, str]:
    """
    (deployment, endpoint, api_version): the ones given, else AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_ENDPOINT and
    OPENAI_API_VERSION (or AZURE_OPENAI_API_VERSION), else AZURE_API_VERSION for the version.
    """
    return (deployment or os.environ.get("AZURE_OPENAI_DEPLOYMENT") or None,
            endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT") or None,
            api_version or os.environ.get("OPENAI_API_VERSION") or os.environ.get("AZURE_OPENAI_API_VERSION")
            or AZURE_API_VERSION)


def azure_chat_model(*, endpoint: str | None, deployment: str | None, api_version: str,  # noqa: PLR0913
                     api_key: str | None = None, token_provider: Callable[[], str] | None = None,
                     max_tokens: int | None = None) -> Any:
    """
    LangChain's AzureChatOpenAI for a deployment. The key is api_key, else AZURE_OPENAI_API_KEY, or a Microsoft
    Entra ID token_provider instead. _Hint (a note, not a traceback) says which settings are missing.
    """
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
                                "api_version": api_version, "max_retries": _RETRIES}
    if max_tokens:
        settings["max_tokens"] = max_tokens
    if api_key:
        settings["api_key"] = api_key
    if token_provider:
        settings["azure_ad_token_provider"] = token_provider
    return openai.AzureChatOpenAI(**settings)


def openai_chat_model(model: str, *, api_key: str | None = None, endpoint: str | None = None,
                      max_tokens: int | None = None) -> Any:
    """
    LangChain's ChatOpenAI for an OpenAI model, or for any server that speaks OpenAI's API at `endpoint` (or
    OPENAI_BASE_URL): a LiteLLM or vLLM gateway, OpenRouter, Ollama's /v1. The key is api_key, else OPENAI_API_KEY;
    a server of your own may need none. _Hint says what's missing.
    """
    base_url = endpoint or os.environ.get("OPENAI_BASE_URL")
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not (key or base_url):
        raise _Hint("The agent needs an OpenAI API key to answer with OpenAI, and OPENAI_API_KEY is not set. Set it "
                    "in the notebook's environment (os.environ['OPENAI_API_KEY'] = 'sk-...'), or pass it: "
                    "AgentView(provider='openai', api_key=...).")
    openai = _require("langchain_openai", "The agent's OpenAI model", package="langchain-openai")
    settings: dict[str, Any] = {"model": model, "api_key": key or _NO_KEY, "max_retries": _RETRIES}
    if max_tokens:
        settings["max_tokens"] = max_tokens
    if base_url:
        settings["base_url"] = base_url
    return openai.ChatOpenAI(**settings)


def bedrock_chat_model(model_id: str, *, runtime: Any, control: Any, max_tokens: int | None = None) -> Any:
    """
    LangChain's ChatBedrockConverse for a Bedrock model ID or inference profile. It calls Bedrock's Converse API
    through `runtime`, a bedrock-runtime client the agent makes from its own session, so it uses the notebook's
    credentials, region and retries; `control` (a bedrock client) is what it reads an application inference
    profile's model with.
    """
    aws = _require("langchain_aws", "The agent's Amazon Bedrock model", package="langchain-aws")
    settings: dict[str, Any] = {"model": model_id, "client": runtime, "bedrock_client": control}
    if max_tokens:
        settings["max_tokens"] = max_tokens
    return aws.ChatBedrockConverse(**settings)


def langchain_chat_model(provider: str, model: str, *, api_key: str | None = None, endpoint: str | None = None,
                         max_tokens: int | None = None) -> Any:
    """
    Any other provider's chat model, through LangChain's init_chat_model ('anthropic', 'google_genai', 'ollama',
    'mistralai', 'groq', ...), with its key (else the provider's own environment variable) and `endpoint` as its
    base URL. max_tokens caps each answer when the model has that setting. Its ImportError says what to pip install.
    """
    info = provider_info(provider)
    if not model:
        raise _Hint(f"Name the {info.title} model the agent should answer with: "
                    f"AgentView(model='{provider}:{info.example or '<model>'}').")
    chat_models = _require("langchain.chat_models", "The agent's model", package="langchain")
    settings: dict[str, Any] = {}
    if api_key:
        settings["api_key"] = api_key
    if endpoint:
        settings["base_url"] = endpoint
    chat_model = chat_models.init_chat_model(model, model_provider=provider, **settings)
    if max_tokens and "max_tokens" in getattr(type(chat_model), "model_fields", {}):
        chat_model.max_tokens = max_tokens
    return chat_model


def model_name(chat_model: Any, named: str | None = None) -> str:
    """
    The model that answers: the chat model's own name when there is one, else the model or deployment it was
    given.
    """
    if chat_model is not None:
        for attribute in ("deployment_name", "model_name", "model_id", "model"):
            value = getattr(chat_model, attribute, None)
            if isinstance(value, str) and value:
                return value
        return type(chat_model).__name__
    return named or ""


def model_place(provider: str, model: str, endpoint: str | None = None) -> str:
    """
    Where what the agent sends goes, for the note on what leaves AWS: 'Amazon Bedrock in your own AWS account
    (us.anthropic.claude-haiku-4-5-20251001-v1:0), so it stays in AWS', 'your Azure OpenAI deployment
    (prod-gpt-4o), outside AWS', 'the server at http://localhost:4000 (gpt-4.1)'.
    """
    info = provider_info(provider)
    if endpoint and provider not in ("azure", "bedrock"):
        return f"the server at {endpoint} ({model})"
    return info.place.format(title=info.title, model=model or "not named")


class Rates(NamedTuple):
    """
    What a model's tokens cost: USD per 1M input tokens and per 1M output tokens, and whose prices they are
    ('model_prices=', the ones you passed, or 'list', Amazon Bedrock's list prices in _bedrock's MODEL_PRICES).
    """

    price_in: float
    price_out: float
    source: str


def model_rates(model: str, model_prices: dict[str, tuple[float, float]] | None,
                provider: str = "") -> Rates | None:
    """
    What the model's tokens cost: model_prices= first (a deployment or model name, the longest that appears in
    `model` wins), then for Amazon Bedrock its list prices (by model family; a 'global.' profile's own where it
    costs less). None when neither knows the model, so its cost is unknown rather than a guess.
    """
    if model and model_prices:
        wanted = model.lower()
        for key in sorted(model_prices, key=len, reverse=True):
            if key.lower() in wanted:
                price_in, price_out = model_prices[key]
                return Rates(price_in, price_out, "model_prices=")
    listed = model_price(model) if provider == "bedrock" and model else None
    return Rates(listed[0], listed[1], "list") if listed else None


def answer_cost(model: str, input_tokens: int, output_tokens: int,
                model_prices: dict[str, tuple[float, float]] | None, *, provider: str = "") -> float | None:
    """
    Estimated USD for the tokens, at model_rates(): model_prices maps a deployment or model name to (USD per 1M
    input tokens, per 1M output tokens), and a Bedrock model without one has its list price. None when no price
    matches.
    """
    rates = model_rates(model, model_prices, provider)
    if rates is None:
        return None
    return input_tokens / 1e6 * rates.price_in + output_tokens / 1e6 * rates.price_out


_CONNECTION_ERRORS = ("APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout")
_AZURE_WHY = {
    "key": "Azure OpenAI refused the key: check AZURE_OPENAI_API_KEY (or api_key=) is a key of this resource, and "
           "AZURE_OPENAI_ENDPOINT its endpoint.",
    "access": "Azure OpenAI refused access: the key or identity can't use this deployment, or the resource's network "
              "rules block this notebook.",
    "missing": "No such deployment on this endpoint: AZURE_OPENAI_DEPLOYMENT (or deployment=) is the deployment's "
               "name in Azure AI Foundry, not the model's; an old api_version can also give a 404.",
    "rate": "The deployment's rate limit was hit: wait a minute and ask again, or raise its tokens-per-minute quota.",
    "filter": "Azure's content filter blocked the prompt or the answer. Log lines can set it off: ask about a "
              "narrower window, or adjust the deployment's content filter.",
    "request": "The deployment rejected the request: it may not support tool calling, or api_version may be too old "
               "for it.",
    "network": "Couldn't reach the endpoint from this notebook: check AZURE_OPENAI_ENDPOINT and the network.",
}
_API_WHY = {
    "key": "{title} refused the key: check {key}.",
    "access": "{title} refused access: the key can't use this model, or a network rule blocks this notebook.",
    "missing": "{title} has no such model: check model= (agent.core.model is the one it asked for).",
    "rate": "{title}'s rate limit was hit: wait a minute and ask again, or raise the quota.",
    "filter": "{title}'s content filter blocked the prompt or the answer. Log lines can set it off: ask about a "
              "narrower window.",
    "request": "{title} rejected the request: the model may not support tool calling, which the agent needs.",
    "network": "Couldn't reach {title} from this notebook: check the network, and endpoint= if you gave one.",
}
_BEDROCK_BUSY = "Bedrock couldn't serve the model just now: ask again in a minute."
_BEDROCK_QUOTA = ("Bedrock's quota for this model was hit: wait a minute and ask again, or call it through a "
                  "cross-region profile ('us.', 'eu.' or 'global.' before its ID), which spreads the load.")
_BEDROCK_WHY = {
    "AccessDeniedException": "Bedrock refused the call: the notebook's role needs bedrock:InvokeModel on the model "
                             "and on its inference profile, and the account needs access to the model (the Bedrock "
                             "console's Model catalog shows it).",
    "ResourceNotFoundException": "Bedrock has no such model in this region, or it has reached its end of life: pick "
                                 "another, AgentView(provider='bedrock', model='sonnet').",
    "ThrottlingException": _BEDROCK_QUOTA,
    "ServiceQuotaExceededException": _BEDROCK_QUOTA,
    "ModelNotReadyException": _BEDROCK_BUSY,
    "ServiceUnavailableException": _BEDROCK_BUSY,
    "InternalServerException": _BEDROCK_BUSY,
    "ModelTimeoutException": "The model took too long to answer: ask again, or ask a narrower question.",
    "ModelErrorException": "The model failed to answer: ask again.",
    "NoCredentialsError": "The notebook has no AWS credentials to call Bedrock with: on SageMaker that's the "
                          "execution role; elsewhere run aws configure, or pass profile=.",
    "NoRegionError": "Bedrock needs a region: AgentView(region='us-east-1'), or set AWS_DEFAULT_REGION.",
    "EndpointConnectionError": "Couldn't reach Bedrock from this notebook: check the region and the network (a "
                               "notebook without internet access needs a VPC endpoint for bedrock-runtime).",
}
_BEDROCK_INVALID = (
    ("on-demand throughput", "This model is served only through an inference profile: put 'us.' (or 'eu.', 'apac.', "
                             "'global.') before its ID, or pass its short name and the agent finds the profile, "
                             "AgentView(provider='bedrock', model='haiku')."),
    ("tool", "This model can't call tools through Bedrock's Converse API, which the agent needs: pick one that can, "
             "such as Claude, Amazon Nova or Llama 3.1 and newer, AgentView(provider='bedrock', model='sonnet')."),
    ("model identifier is invalid", "Bedrock doesn't know this model ID in this region: check it, or pass a short "
                                    "name, AgentView(provider='bedrock', model='sonnet')."),
)
_BEDROCK_REJECTED = "Bedrock rejected the request: check the model ID, and that the model can call tools."


def _error_kind(name: str, status: Any, text: str) -> str:
    """
    What kind of failure an error from a model's API client is, by its class name or HTTP status: 'key', 'access',
    'missing', 'rate', 'filter', 'request' or 'network'; '' when it's none of those.
    """
    checks = (
        ("key", name == "AuthenticationError" or status == 401),
        ("access", name == "PermissionDeniedError" or status == 403),
        ("missing", name == "NotFoundError" or status == 404),
        ("rate", name == "RateLimitError" or status == 429),
        ("filter", "content_filter" in text or "content management policy" in text.lower()),
        ("request", name == "BadRequestError" or status == 400),
        ("network", name in _CONNECTION_ERRORS),
    )
    return next((kind for kind, matches in checks if matches), "")


def _bedrock_why(exc: BaseException, text: str) -> str:
    """
    What a failed Bedrock call means, by its error code (botocore's exception, raised through langchain-aws), and
    for a ValidationException by what its message says.
    """
    code = _error_code(exc) if isinstance(exc, ClientError) else type(exc).__name__
    if code == "ValidationException":
        lowered = text.lower()
        return next((why for clue, why in _BEDROCK_INVALID if clue in lowered), _BEDROCK_REJECTED)
    return _BEDROCK_WHY.get(code, "")


def _api_why(exc: BaseException, name: str, text: str, provider: str) -> str:
    """
    What a failed call to a model's HTTP API means (Azure OpenAI, OpenAI, Anthropic, ...), by the error's class name
    or HTTP status, naming the provider and its key.
    """
    kind = _error_kind(name, getattr(exc, "status_code", None), text)
    if not kind:
        return ""
    if provider == "azure":
        return _AZURE_WHY[kind]
    info = provider_info(provider)
    key = f"{info.key_env} (or api_key=)" if info.key_env else "the key it was given"
    why = _API_WHY[kind].format(title=info.title, key=key)
    return why[:1].upper() + why[1:]


def explain_model_error(exc: BaseException, provider: str = "azure") -> str:
    """
    What a failed model call means and what to do, from the error the provider's client raised (by its class name,
    status or error code, so this needs no openai or langchain import). `provider` is the agent's: 'azure',
    'openai', 'bedrock', another LangChain provider, or '' for a chat model passed in.
    """
    name = type(exc).__name__
    text = " ".join(str(exc).split())
    if len(text) > _ERROR_CHARACTERS:
        text = text[:_ERROR_CHARACTERS - 1] + "…"
    if name == "GraphRecursionError":
        why = "The agent took more steps than its budget allows."
    elif provider == "bedrock":
        why = _bedrock_why(exc, text)
    else:
        why = _api_why(exc, name, text, provider)
    return f"{name}: {text}" + (f" {why}" if why else "")

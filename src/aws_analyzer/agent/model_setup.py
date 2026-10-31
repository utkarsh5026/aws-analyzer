"""
How to set up the model the agent answers with, as steps to copy: what each provider needs installed, which
environment variables it reads (and which of them are secrets), the call that starts the agent on it, and what has to
be done outside Python (Bedrock's model access, a running Ollama server). AgentView.setup() checks the kernel against
these and draws the report; everything here is pure, so the steps can be tested without a model or AWS.

SETUPS describes the providers llm.PROVIDERS names, and OTHER_SETUPS a few more LangChain providers whose package and
key are well known; any other provider LangChain's init_chat_model knows gets a general guide (provider_setup()).
The cells never hold a key: a secret is read with getpass, so it stays out of the notebook file, its outputs and its
history. AGENT_INSTALL is the pip line for the agent itself, and AGENT_MODULES what it imports to run at all.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Mapping

from .llm import _LANGCHAIN_PROVIDERS, MODEL_ENV, PROVIDERS, _named_provider, provider_info

AGENT_INSTALL = '%pip install --quiet "aws-analyzer[agent,notebook]"'
AGENT_MODULES = ("deepagents",)
_FIRST_QUESTION = "Which Lambda failed last night?"
_CLOSE_PROVIDERS = 3


@dataclass(frozen=True)
class SetupVar:
    """
    An environment variable a provider reads.

        name     'OPENAI_API_KEY'
        example  what to put in it, shown in the cell to copy ('https://<resource>.openai.azure.com')
        what     what it is, in a few words, for the table
        secret   a key: the cell asks for it with getpass instead of writing it out
        needed   the provider can't answer without it (optional ones are only listed)
    """

    name: str
    example: str
    what: str
    secret: bool = False
    needed: bool = True


@dataclass(frozen=True)
class ProviderSetup:
    """
    Everything one provider needs before the agent can answer with it.

        provider   what provider= takes: 'azure'
        title      'Azure OpenAI'
        model      the model the call names ('' when the provider's default is used)
        package    the pip package of its LangChain model ('' when the agent's extra brings it)
        modules    what it imports, to check it's installed: ('langchain_openai',)
        variables  the environment variables it reads
        call       the AgentView(...) that starts the agent on it
        steps      what has to be done outside Python first, as sentences
        where      where what the agent sends goes
    """

    provider: str
    title: str
    model: str = ""
    package: str = ""
    modules: tuple[str, ...] = ()
    variables: tuple[SetupVar, ...] = ()
    call: str = ""
    steps: tuple[str, ...] = ()
    where: str = ""


def _key(name: str, title: str) -> SetupVar:
    """
    A provider's API key, read with getpass in the cell to copy.
    """
    return SetupVar(name, "", f"your {title} API key", secret=True)


_BEDROCK = ProviderSetup(
    "bedrock", "Amazon Bedrock", model="haiku", modules=("langchain_aws",),
    variables=(SetupVar("AWS_DEFAULT_REGION", "us-east-1", "the region Bedrock is called in, when the notebook's "
                        "own isn't the one you want", needed=False),),
    call='AgentView(provider="bedrock")',
    steps=("Turn on access to the model in the Bedrock console (Model access), in the notebook's region: Claude "
           "Haiku 4.5 by default, or the one model= names.",
           "Give the notebook's IAM role bedrock:InvokeModel on that model (and bedrock:ListFoundationModels and "
           "bedrock:ListInferenceProfiles, to find a short name's ID)."),
    where="Amazon Bedrock in your own AWS account: nothing leaves AWS, and no key is needed",
)
_OPENAI = ProviderSetup(
    "openai", "OpenAI", model="gpt-4.1", modules=("langchain_openai",),
    variables=(_key("OPENAI_API_KEY", "OpenAI"),
               SetupVar("OPENAI_BASE_URL", "http://localhost:4000", "a server that speaks OpenAI's API instead "
                        "(LiteLLM, vLLM, OpenRouter)", needed=False)),
    call='AgentView(model="openai:gpt-4.1")',
    steps=("Make an API key at platform.openai.com (API keys).",),
    where="OpenAI, outside AWS",
)
_AZURE = ProviderSetup(
    "azure", "Azure OpenAI", modules=("langchain_openai",),
    variables=(SetupVar("AZURE_OPENAI_ENDPOINT", "https://<resource>.openai.azure.com", "your Azure OpenAI "
                        "resource's endpoint"),
               SetupVar("AZURE_OPENAI_DEPLOYMENT", "<deployment name>", "the deployment of a model that can call "
                        "tools (gpt-4o, gpt-4.1)"),
               _key("AZURE_OPENAI_API_KEY", "Azure OpenAI"),
               SetupVar("OPENAI_API_VERSION", "2024-10-21", "the API version, when not 2024-10-21", needed=False)),
    call='AgentView(provider="azure")',
    steps=("In the Azure portal, open the Azure OpenAI resource: Keys and Endpoint has the endpoint and a key, and "
           "the deployments page the deployment's name.",),
    where="your Azure OpenAI deployment, outside AWS",
)
_ANTHROPIC = ProviderSetup(
    "anthropic", "Anthropic", model="claude-sonnet-4-5", modules=("langchain_anthropic",),
    variables=(_key("ANTHROPIC_API_KEY", "Anthropic"),),
    call='AgentView(model="anthropic:claude-sonnet-4-5")',
    steps=("Make an API key at console.anthropic.com (API keys).",),
    where="Anthropic, outside AWS",
)
_GOOGLE = ProviderSetup(
    "google_genai", "Google Gemini", model="gemini-2.5-pro", modules=("langchain_google_genai",),
    variables=(_key("GOOGLE_API_KEY", "Google Gemini"),),
    call='AgentView(model="google_genai:gemini-2.5-pro")',
    steps=("Make an API key at aistudio.google.com (Get API key).",),
    where="Google, outside AWS",
)
_OLLAMA = ProviderSetup(
    "ollama", "Ollama", model="llama3.1", package="langchain-ollama", modules=("langchain_ollama",),
    call='AgentView(model="ollama:llama3.1", endpoint="http://localhost:11434")',
    steps=("Run an Ollama server the notebook can reach, and pull a model that can call tools: ollama pull "
           "llama3.1.",),
    where="your Ollama server",
)
SETUPS: dict[str, ProviderSetup] = {setup.provider: setup for setup in
                                    (_BEDROCK, _OPENAI, _AZURE, _ANTHROPIC, _GOOGLE, _OLLAMA)}


def _keyed(provider: str, title: str, package: str, key_env: str, model: str) -> ProviderSetup:
    """
    The setup of a LangChain provider that needs its package and one API key.
    """
    module = package.replace("-", "_")
    return ProviderSetup(provider, title, model=model, package=package, modules=(module,),
                         variables=(_key(key_env, title),), call=f'AgentView(model="{provider}:{model}")',
                         steps=(f"Make a {title} API key in its console.",), where=f"{title}, outside AWS")


OTHER_SETUPS: dict[str, ProviderSetup] = {setup.provider: setup for setup in (
    _keyed("groq", "Groq", "langchain-groq", "GROQ_API_KEY", "llama-3.3-70b-versatile"),
    _keyed("mistralai", "Mistral", "langchain-mistralai", "MISTRAL_API_KEY", "mistral-large-latest"),
    _keyed("deepseek", "DeepSeek", "langchain-deepseek", "DEEPSEEK_API_KEY", "deepseek-chat"),
    _keyed("xai", "xAI", "langchain-xai", "XAI_API_KEY", "grok-4"),
    _keyed("fireworks", "Fireworks", "langchain-fireworks", "FIREWORKS_API_KEY",
           "accounts/fireworks/models/llama-v3p1-70b-instruct"),
    _keyed("together", "Together AI", "langchain-together", "TOGETHER_API_KEY",
           "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
)}


def _general_setup(provider: str) -> ProviderSetup:
    """
    The setup of a LangChain provider this module knows nothing more about: its package and key are LangChain's to
    say, so the steps send the reader there.
    """
    return ProviderSetup(
        provider, provider, model="<model>",
        call=f'AgentView(model="{provider}:<model>")',
        steps=(f"Install LangChain's package for {provider} and set the key it reads: LangChain's chat model "
               "integrations page names both.",),
        where=f"{provider}, outside AWS",
    )


def setup_providers() -> list[str]:
    """
    The providers setup() has steps for, in the order it lists them: Bedrock, OpenAI, Azure OpenAI, Anthropic,
    Google Gemini, Ollama, then the other keyed ones.
    """
    return [*SETUPS, *OTHER_SETUPS]


def provider_setup(provider: str) -> ProviderSetup:
    """
    What a provider needs, by the name provider= takes ('azure', 'azure_openai', 'Bedrock'); ValueError, naming the
    closest providers, for one the agent can't answer with.
    """
    name = _named_provider(provider)
    known = SETUPS.get(name) or OTHER_SETUPS.get(name)
    if known:
        return known
    if name in _LANGCHAIN_PROVIDERS:
        return _general_setup(name)
    choices = [*setup_providers(), *sorted(_LANGCHAIN_PROVIDERS)]
    close = difflib.get_close_matches(name, choices, n=_CLOSE_PROVIDERS)
    hint = f" Did you mean {', '.join(repr(choice) for choice in close)}?" if close else ""
    raise ValueError(f"The agent can't answer with a provider named {provider!r}.{hint} setup() lists the ones it "
                     "can.")


def unset_variables(setup: ProviderSetup, environ: Mapping[str, str], *, needed_only: bool = True) -> list[SetupVar]:
    """
    The provider's variables the environment doesn't set (the needed ones only, unless needed_only=False).
    """
    wanted = [variable for variable in setup.variables if variable.needed or not needed_only]
    return [variable for variable in wanted if not environ.get(variable.name, "").strip()]


def install_line(setup: ProviderSetup) -> str:
    """
    The notebook line that installs the agent with this provider's LangChain package.
    """
    if not setup.package:
        return AGENT_INSTALL
    return f'{AGENT_INSTALL} {setup.package}'


def _variable_line(variable: SetupVar, title: str) -> str:
    """
    The cell's line that sets one variable: a key asked for with getpass, anything else written out to edit.
    """
    if variable.secret:
        return f'os.environ["{variable.name}"] = getpass("{title} API key: ")'
    return f'os.environ["{variable.name}"] = "{variable.example}"'


def settings_cell(setup: ProviderSetup, environ: Mapping[str, str]) -> str:
    """
    The cell that sets what's still missing, for this kernel only: each needed variable the environment doesn't set,
    keys asked for with getpass so they never land in the notebook. '' when nothing is missing.
    """
    missing = unset_variables(setup, environ)
    if not missing:
        return ""
    lines = ["import os"]
    if any(variable.secret for variable in missing):
        lines.append("from getpass import getpass")
    lines.append("")
    lines += [_variable_line(variable, setup.title) for variable in missing]
    return "\n".join(lines)


def start_cell(setup: ProviderSetup, *, zone: str = "") -> str:
    """
    The cell that starts the agent on the provider and asks it a first question; `zone` (a tz name) sets the time
    zone "last night" is read in.
    """
    lines = ["from aws_analyzer import AgentView, set_timezone", ""]
    if zone:
        lines.append(f'set_timezone("{zone}")')
    lines += [f"agent = {setup.call}", f'agent.ask("{_FIRST_QUESTION}")']
    return "\n".join(lines)


def _model_env_value(setup: ProviderSetup) -> str:
    """
    What AWS_ANALYZER_AGENT_MODEL holds for this provider: 'bedrock:haiku', 'azure:<deployment name>'.
    """
    if setup.provider == "azure":
        return "azure:<deployment name>"
    return f"{setup.provider}:{setup.model or '<model>'}"


def every_notebook_lines(setup: ProviderSetup) -> str:
    """
    The shell lines that set the provider up for every notebook (a ~/.bashrc, a SageMaker lifecycle configuration,
    a .env file): each needed variable, keys as placeholders to fill from a secrets store, then
    AWS_ANALYZER_AGENT_MODEL, so AgentView() with no arguments picks the provider. Optional ones are left out: their
    examples would point every notebook somewhere it may not mean to go.
    """
    lines = []
    for variable in [variable for variable in setup.variables if variable.needed]:
        value = "<your key>" if variable.secret else variable.example
        lines.append(f'export {variable.name}="{value}"')
    lines.append(f'export {MODEL_ENV}="{_model_env_value(setup)}"')
    return "\n".join(lines)


@dataclass(frozen=True)
class KernelState:
    """
    What setup() found in this kernel, for setup_findings().

        python_ok        the Python is new enough for the agent (3.11+)
        python           its version, '3.10'
        missing_modules  the agent's and the provider's packages that aren't installed, by import name
        environ          the environment variables (only whether each is set is read)
        current          the provider the agent answers with now ('' when none)
    """

    python_ok: bool
    python: str
    missing_modules: tuple[str, ...] = ()
    environ: Mapping[str, str] = field(default_factory=dict)
    current: str = ""


def _package_of(module: str, setup: ProviderSetup) -> str:
    """
    The pip name of a module the agent imports: 'langchain_aws' -> 'langchain-aws'.
    """
    if module in AGENT_MODULES:
        return "aws-analyzer[agent]"
    return setup.package or module.replace("_", "-")


def _install_finding(setup: ProviderSetup, missing: tuple[str, ...]) -> tuple[str, str]:
    """
    The warning that names the packages to install, and the line that does.
    """
    packages = ", ".join(dict.fromkeys(_package_of(module, setup) for module in missing))
    return ("warn", f"{packages} {'is' if len(missing) == 1 else 'are'} not installed: the agent can't answer with "
                    f"{setup.title} without {'it' if len(missing) == 1 else 'them'}. Run {install_line(setup)} in a "
                    "cell, then restart the kernel.")


def setup_findings(setup: ProviderSetup, kernel: KernelState) -> list[tuple[str, str]]:
    """
    What stands between this kernel and the agent answering with the provider: an old Python, packages to install,
    variables to set. Each says the step that fixes it; none when it's ready.
    """
    findings: list[tuple[str, str]] = []
    if not kernel.python_ok:
        findings.append(("warn", f"This kernel runs Python {kernel.python}: the agent needs 3.11 or newer, since "
                                 "deepagents does. Pick a kernel with a newer Python (in SageMaker, the "
                                 "SageMaker Distribution image), then run the steps below there."))
    if kernel.missing_modules:
        findings.append(_install_finding(setup, kernel.missing_modules))
    missing = unset_variables(setup, kernel.environ)
    if missing:
        names = ", ".join(variable.name for variable in missing)
        verb = "is" if len(missing) == 1 else "are"
        findings.append(("warn", f"{names} {verb} not set: {setup.title} needs {'it' if len(missing) == 1 else 'them'}"
                                 ". Run the Settings cell below: it asks for a key without saving it in the "
                                 "notebook."))
    if kernel.current and kernel.current != setup.provider:
        now = provider_info(kernel.current).title
        findings.append(("info", f"The agent answers with {now} now: {setup.call} switches it to {setup.title}, or "
                                 f"use_model('{setup.provider}') in the agent you have."))
    return findings


def is_ready(setup: ProviderSetup, kernel: KernelState) -> bool:
    """
    Whether nothing stands between this kernel and the agent answering with the provider.
    """
    blocking = [finding for finding in setup_findings(setup, kernel) if finding[0] == "warn"]
    return not blocking


def suggested_provider(current: str, ready: list[str]) -> str:
    """
    The provider to show the steps for: the one the agent answers with now, else the first that's ready here, else
    Amazon Bedrock (the notebook's AWS role is all it needs, and nothing leaves AWS).
    """
    if current in SETUPS or current in OTHER_SETUPS:
        return current
    if ready:
        return ready[0]
    return "bedrock"


def blocker(setup: ProviderSetup, kernel: KernelState) -> str:
    """
    What stands first between this kernel and the provider, in a few words: 'Python 3.11+', 'packages',
    'AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY'; '' when it's ready.
    """
    if not kernel.python_ok:
        return "Python 3.11+"
    missing = unset_variables(setup, kernel.environ)
    if missing:
        return ", ".join(variable.name for variable in missing)
    if kernel.missing_modules:
        return "packages"
    return ""


def provider_title(provider: str) -> str:
    """
    How the overview names a provider: its PROVIDERS title, else its setup's.
    """
    if provider in PROVIDERS:
        return PROVIDERS[provider].title
    return provider_setup(provider).title

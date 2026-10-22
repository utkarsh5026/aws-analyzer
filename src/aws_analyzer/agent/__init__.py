"""
aws_analyzer.agent - ask questions about your AWS account in plain English, from a SageMaker / Jupyter notebook.

Part of aws-analyzer: ``pip install "aws-analyzer[agent]"`` (Python 3.11 or newer), then
``from aws_analyzer import AgentView``.

A question like "Which Lambda failed last night?" goes to a model, which answers it by calling read-only tools built on
the package's analyzers, and every answer says what it checked. The model is yours to pick: one on Amazon Bedrock
(called with the notebook's own AWS credentials, so nothing leaves AWS), OpenAI or any server that speaks its API, an
Azure OpenAI deployment, or any other provider LangChain supports (Anthropic, Google Gemini, Ollama, Mistral, ...).
The agent loop is LangChain's deepagents. It covers AWS Lambda and Amazon S3 for now: which functions failed, threw errors, ran
slow or near their timeout, their logs run by run, their settings and cost, and what their code calls; which buckets
there are, how they're set up, what they hold and which files arrived when; and, joining the two, which bucket events
invoke which functions and whether the files that arrived ran them. Nothing it does changes anything in AWS: the
tools only read, and a guard on every AWS client the tools use refuses any call that isn't a read, whatever asks for
it.

Requirements: boto3 (required). deepagents, langchain-openai and langchain-aws for the agent itself (the [agent]
extra; deepagents brings Anthropic's and Google's LangChain packages too), which need Python 3.11 or newer; another
provider's LangChain package (langchain-ollama, ...) for that provider; IPython only for rich HTML output.

What it sends to the model: your question, and what its tools return (function and bucket names, settings, counts, file
names, error messages and log lines, with anything that looks like a key or password hidden). Environment variable
values and what's inside files are never read. That goes to the model's provider: with a model on Amazon Bedrock it
stays in your AWS account; anywhere else, use a provider your organisation allows for that data.

Quick start
-----------
    set_timezone("Asia/Kolkata")                         # (from aws_analyzer) so "last night" means your night
    agent = AgentView(provider="bedrock")                # Claude Haiku 4.5 on Bedrock; model="sonnet" for another
    agent = AgentView(model="openai:gpt-4.1")            # OpenAI, with OPENAI_API_KEY
    agent = AgentView()                                  # Azure OpenAI from AZURE_OPENAI_ENDPOINT, _API_KEY, _DEPLOYMENT
    agent = AgentView(model="anthropic:claude-sonnet-4-5")   # any provider LangChain's init_chat_model knows
    agent.chat()                                         # a chat window: ask, and watch each tool call as it runs
    agent.ask("Which Lambda failed last night?")
    agent.ask("Why did orders-etl fail?")                # a follow-up: it remembers the conversation
    agent.ask("Were last night's uploads to acme-raw processed?")   # S3 and Lambda together
    agent.new_conversation()                             # forget it
    agent.tools()                                        # what it can call, its model and its budget
    agent.transcript()                                   # every question and answer so far, with their cost

    answer = agent.core.ask("Which functions are slow?")  # the same, as data: .text, .calls, .aws_calls, .cost
    agent.core.ask("Which functions are slow?", on_step=print)   # each AgentStep as it happens

How it's built
--------------
One concern per module, so a new service's tools or a new agent capability goes in one place:

    toolkits/        What the agent can read: a Toolkit per AWS service (base.py says what one gives), each with its
                     tools, the AWS reads they make, its part of the system prompt, and the pure summarize_*
                     functions that turn an analyzer's results into what the model reads. lambda_tools.py is
                     Lambda's and s3_tools.py S3's, with s3_triggers, which joins the two when the agent has both;
                     TOOLKITS (toolkits/__init__.py) names them.
    runtime.py       How any tool runs: the budget checked first, the call recorded, errors turned into its result.
    guard.py         The read-only guard every toolkit's AWS clients go through.
    budget.py        How far a question may go (Budget) and the question being answered (_Run).
    results.py       What tools hand the model: JSON-ready values, UTC windows, secrets hidden, permissions named.
    prompt.py        The system prompt (shared rules, then each toolkit's section) and each question's time line.
    llm.py           The model: which provider and model (pick_provider), a maker for each (Azure OpenAI, OpenAI,
                     Bedrock, any other through init_chat_model), its name, cost and errors.
    harness.py       The agent loop: deepagents built once, and one question streamed through it.
    answers.py       AgentAnswer, ToolCall and AgentStep (what the agent does, told as it happens), and the pure
                     findings and next steps a report shows about them.
    analyzer.py      AgentAnalyzer: the settings, and the parts put together. Returns data; never prints.
    view.py          AgentView: the notebook UI, each answer as a report.
    window/          The chat window (AgentView.chat()): the agent's steps drawn as they happen, then the answer
                     (chat.py), and its ⚙ model panel (model_panel.py).
"""

from __future__ import annotations

from .analyzer import AgentAnalyzer
from .answers import AgentAnswer, AgentStep, ToolCall, answer_findings, answer_next
from .budget import Budget
from .guard import READ_PREFIXES, ReadOnlyError
from .llm import (
    AZURE_API_VERSION,
    MODEL_ENV,
    OPENAI_MODEL,
    PROVIDERS,
    Provider,
    Rates,
    answer_cost,
    explain_model_error,
    model_rates,
    pick_provider,
)
from .prompt import asked_at, system_prompt
from .results import hide_secrets
from .toolkits import DEFAULT_TOOLKITS, TOOLKITS, Toolkit
from .toolkits.s3_tools import (
    S3_TOOL_READS,
    Arrivals,
    S3Toolkit,
    TriggerLink,
    count_arrivals,
    file_row,
    summarize_bucket,
    summarize_buckets,
    summarize_file,
    summarize_found,
    summarize_listing,
    summarize_prefix,
    summarize_triggers,
    trigger_findings,
)
from .toolkits.lambda_tools import (
    TOOL_READS,
    LambdaToolkit,
    code_locations,
    function_row,
    summarize_code,
    summarize_detail,
    summarize_errors,
    summarize_functions,
    summarize_health,
    summarize_performance,
    summarize_runs,
    summarize_search,
    summarize_source,
    summarize_symbol,
)
from .view import AgentView

__all__ = [
    "AgentAnalyzer",
    "AgentView",
    "AgentAnswer",
    "AgentStep",
    "ToolCall",
    "Budget",
    "ReadOnlyError",
    "READ_PREFIXES",
    "Toolkit",
    "TOOLKITS",
    "DEFAULT_TOOLKITS",
    "LambdaToolkit",
    "TOOL_READS",
    "S3Toolkit",
    "S3_TOOL_READS",
    "Arrivals",
    "TriggerLink",
    "AZURE_API_VERSION",
    "OPENAI_MODEL",
    "MODEL_ENV",
    "PROVIDERS",
    "Provider",
    "Rates",
    "pick_provider",
    "model_rates",
    "hide_secrets",
    "asked_at",
    "system_prompt",
    "answer_findings",
    "answer_next",
    "answer_cost",
    "explain_model_error",
    "function_row",
    "summarize_functions",
    "summarize_health",
    "summarize_errors",
    "summarize_runs",
    "summarize_performance",
    "summarize_detail",
    "summarize_code",
    "summarize_source",
    "summarize_search",
    "summarize_symbol",
    "code_locations",
    "file_row",
    "summarize_buckets",
    "summarize_bucket",
    "summarize_listing",
    "summarize_found",
    "summarize_prefix",
    "summarize_file",
    "count_arrivals",
    "trigger_findings",
    "summarize_triggers",
]

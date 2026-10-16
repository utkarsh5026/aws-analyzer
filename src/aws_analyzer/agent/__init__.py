"""
aws_analyzer.agent - ask questions about your AWS account in plain English, from a SageMaker / Jupyter notebook.

Part of aws-analyzer: ``pip install "aws-analyzer[agent]"`` (Python 3.11 or newer), then
``from aws_analyzer import AgentView``.

A question like "Which Lambda failed last night?" goes to a model (an Azure OpenAI deployment for now), which answers
it by calling read-only tools built on the package's analyzers, and every answer says what it checked. The agent
loop is LangChain's deepagents. It covers AWS Lambda for now: which functions failed, threw errors, ran slow or near
their timeout, their logs run by run, their settings and cost, and what their code calls. Nothing it does changes
anything in AWS: the tools only read, and a guard on every AWS client the tools use refuses any call that isn't a
read, whatever asks for it.

Requirements: boto3 (required). deepagents and langchain-openai for the agent itself (the [agent] extra), which need
Python 3.11 or newer; IPython only for rich HTML output.

What it sends to the model: your question, and what its tools return (function names, settings, counts, error messages and
log lines, with anything that looks like a key or password hidden). Environment variable values are never
read. That goes to your Azure OpenAI deployment, outside AWS: use a deployment your organisation allows for that data.

Quick start
-----------
    # export AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com  AZURE_OPENAI_API_KEY=...
    #        AZURE_OPENAI_DEPLOYMENT=<your deployment name>
    set_timezone("Asia/Kolkata")                         # (from aws_analyzer) so "last night" means your night
    agent = AgentView()                                  # or AgentView(endpoint=..., deployment=..., api_key=...)
    agent.ask("Which Lambda failed last night?")
    agent.ask("Why did orders-etl fail?")                # a follow-up: it remembers the conversation
    agent.new_conversation()                             # forget it
    agent.tools()                                        # what it can call, its model and its budget
    agent.transcript()                                   # every question and answer so far, with their cost

    answer = agent.core.ask("Which functions are slow?")  # the same, as data: .text, .calls, .aws_calls, .cost

How it's built
--------------
One concern per module, so a new service's tools or a new agent capability goes in one place:

    toolkits/        What the agent can read: a Toolkit per AWS service (base.py says what one gives), each with its
                     tools, the AWS reads they make, its part of the system prompt, and the pure summarize_*
                     functions that turn an analyzer's results into what the model reads. lambda_tools.py is
                     Lambda's; TOOLKITS (toolkits/__init__.py) names them.
    runtime.py       How any tool runs: the budget checked first, the call recorded, errors turned into its result.
    guard.py         The read-only guard every toolkit's AWS clients go through.
    budget.py        How far a question may go (Budget) and the question being answered (_Run).
    results.py       What tools hand the model: JSON-ready values, UTC windows, secrets hidden, permissions named.
    prompt.py        The system prompt (shared rules, then each toolkit's section) and each question's time line.
    llm.py           The model: Azure OpenAI from settings or the environment, its name, cost and errors.
    harness.py       The agent loop: deepagents built once, and one question streamed through it.
    answers.py       AgentAnswer and ToolCall, and the pure findings and next steps a report shows about them.
    analyzer.py      AgentAnalyzer: the settings, and the parts put together. Returns data; never prints.
    view.py          AgentView: the notebook UI, each answer as a report.
"""

from __future__ import annotations

from .analyzer import AgentAnalyzer
from .answers import AgentAnswer, ToolCall, answer_findings, answer_next
from .budget import Budget
from .guard import READ_PREFIXES, ReadOnlyError
from .llm import AZURE_API_VERSION, answer_cost, explain_model_error
from .prompt import asked_at, system_prompt
from .results import hide_secrets
from .toolkits import DEFAULT_TOOLKITS, TOOLKITS, Toolkit
from .toolkits.lambda_tools import (
    TOOL_READS,
    LambdaToolkit,
    function_row,
    summarize_code,
    summarize_detail,
    summarize_errors,
    summarize_functions,
    summarize_health,
    summarize_performance,
    summarize_runs,
)
from .view import AgentView

__all__ = [
    "AgentAnalyzer",
    "AgentView",
    "AgentAnswer",
    "ToolCall",
    "Budget",
    "ReadOnlyError",
    "READ_PREFIXES",
    "Toolkit",
    "TOOLKITS",
    "DEFAULT_TOOLKITS",
    "LambdaToolkit",
    "TOOL_READS",
    "AZURE_API_VERSION",
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
]

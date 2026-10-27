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
    agent = AgentView(memory=True, notes="AGENTS.md")    # it keeps notes between conversations; yours from a file
    agent.memory()                                       # what it remembers about this account
    agent.forget("orders-etl")                           # clear the notes about it
    agent = AgentView(python="local")                    # its own Python over files it saves, with Altair charts
    agent.ask("Chart the CSVs in s3://acme-raw/sales/ by month")   # the answer, with the charts it drew
    agent.save_charts()                                  # those charts as files, in agent-charts/
    agent.go()                                           # it stopped to ask before a big read: read it all
    agent.smaller()                                      # ... or only the smaller part it named
    agent.skip()                                         # ... or answer without it

    answer = agent.core.ask("Which functions are slow?")  # the same, as data: .text, .calls, .aws_calls, .cost
    agent.core.ask("Which functions are slow?", on_step=print)   # each AgentStep as it happens

How it's built
--------------
One concern per module, so a new service's tools or a new agent capability goes in one place:

    toolkits/        What the agent can read: a Toolkit per AWS service (base.py says what one gives), each with its
                     tools, the AWS reads they make, its part of the system prompt, and the pure summarize_*
                     functions that turn an analyzer's results into what the model reads. lambda_tools.py is
                     Lambda's and s3_tools.py S3's, with s3_triggers, which joins the two when the agent has both;
                     bedrock_kb_tools.py is Bedrock Knowledge Bases'. TOOLKITS (toolkits/__init__.py) names them.
    runtime.py       How any tool runs: the budget checked first, the call recorded, errors turned into its result,
                     and a pause to ask the user before a big read (ask_user; interrupt_on=).
    guard.py         The read-only guard every toolkit's AWS clients go through.
    budget.py        How far a question may go (Budget) and the question being answered (_Run).
    results.py       What tools hand the model: JSON-ready values, UTC windows, secrets hidden, permissions named.
    prompt.py        The system prompt (shared rules, then each toolkit's section) and each question's time line.
    llm.py           The model: which provider and model (pick_provider), a maker for each (Azure OpenAI, OpenAI,
                     Bedrock, any other through init_chat_model), its name, cost and errors.
    harness.py       The agent loop: deepagents built once, and one question streamed through it.
    memory.py        What it remembers between conversations (memory=True): its notes about the account, on a
                     deepagents StoreBackend in a local SQLite file, secrets hidden, one set per AWS account.
    answers.py       AgentAnswer, ToolCall, PlanStep (the plan it writes and ticks off), AgentStep (what the agent
                     does, told as it happens) and BigRead (what a tool asks before a big read), and the pure findings
                     and next steps a report shows about them.
    analyzer.py      AgentAnalyzer: the settings, and the parts put together. Returns data; never prints.
    view.py          AgentView: the notebook UI, each answer as a report.
    workspace/       The Python workspace (AgentAnalyzer(python=...)): the Workspace interface every sandbox gives,
                     where the model's code runs over files the tools save (__init__.py); toolkits/python_tools.py
                     gives the model run_python over it, and each service's toolkit a save_* tool.
                     files.py     deepagents' file tools on the workspace's files.
                     charts.py    The charts an answer shows: Altair (Vega-Lite) charts the model's code saves in the
                                  workspace, which the sandbox draws as SVG, or pictures it drew itself; show_chart (the
                                  Python toolkit's) puts them in the answer and save_charts() writes them to files.
                     sandboxes/   The built-in workspaces: LocalSandbox, Python in Pyodide on Deno on this machine
                                  (AgentAnalyzer(python="local")).
    window/          The chat window (AgentView.chat()): the agent's steps drawn as they happen, then the answer
                     (chat.py), its ⚙ model panel (model_panel.py) and its 🧠 memory panel (memory_panel.py).
"""

from __future__ import annotations

from .analyzer import AgentAnalyzer
from .answers import (
    CHOICES,
    AgentAnswer,
    AgentStep,
    BigRead,
    PlanStep,
    ToolCall,
    answer_findings,
    answer_next,
    choice_of,
    parse_plan,
)
from .budget import Budget
from .guard import READ_PREFIXES, SEARCHES, ReadOnlyError
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
from .memory import DEFAULT_MEMORY, MemoryState, Note, drop_notes, memory_findings, parse_notes, stamp_notes
from .prompt import asked_at, system_prompt
from .results import hide_secrets
from .toolkits import DEFAULT_TOOLKITS, TOOLKITS, Toolkit
from .toolkits.bedrock_kb_tools import (
    KB_TOOL_READS,
    BedrockKBToolkit,
    kb_sync_row,
    summarize_kb_chunks,
    summarize_kb_files,
    summarize_kb_health,
    summarize_kb_search,
    summarize_kb_syncs,
    summarize_knowledge_bases,
)
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
    summarize_preview,
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
from .workspace.charts import CHART_THEME, CHARTS_FOLDER, Chart, chart_of, image_kind, read_spec
from .workspace.sandboxes import PYODIDE_VERSION, LocalSandbox
from .toolkits.python_tools import PythonToolkit, summarize_run, summarize_workspace
from .toolkits.s3_listing import listing_read, save_read
from .view import AgentView
from .workspace import PythonRun, Workspace, WorkspaceFile, clean_path

__all__ = [
    "AgentAnalyzer",
    "AgentView",
    "AgentAnswer",
    "AgentStep",
    "PlanStep",
    "ToolCall",
    "BigRead",
    "CHOICES",
    "choice_of",
    "Budget",
    "ReadOnlyError",
    "READ_PREFIXES",
    "SEARCHES",
    "Toolkit",
    "TOOLKITS",
    "DEFAULT_TOOLKITS",
    "LambdaToolkit",
    "TOOL_READS",
    "S3Toolkit",
    "S3_TOOL_READS",
    "BedrockKBToolkit",
    "KB_TOOL_READS",
    "Arrivals",
    "TriggerLink",
    "PythonToolkit",
    "LocalSandbox",
    "PYODIDE_VERSION",
    "Workspace",
    "WorkspaceFile",
    "PythonRun",
    "clean_path",
    "summarize_run",
    "summarize_workspace",
    "Chart",
    "CHARTS_FOLDER",
    "CHART_THEME",
    "chart_of",
    "image_kind",
    "read_spec",
    "AZURE_API_VERSION",
    "OPENAI_MODEL",
    "MODEL_ENV",
    "PROVIDERS",
    "Provider",
    "Rates",
    "pick_provider",
    "model_rates",
    "hide_secrets",
    "DEFAULT_MEMORY",
    "MemoryState",
    "Note",
    "parse_notes",
    "stamp_notes",
    "drop_notes",
    "memory_findings",
    "asked_at",
    "system_prompt",
    "answer_findings",
    "answer_next",
    "parse_plan",
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
    "summarize_preview",
    "listing_read",
    "save_read",
    "count_arrivals",
    "trigger_findings",
    "summarize_triggers",
    "kb_sync_row",
    "summarize_knowledge_bases",
    "summarize_kb_health",
    "summarize_kb_syncs",
    "summarize_kb_files",
    "summarize_kb_search",
    "summarize_kb_chunks",
]

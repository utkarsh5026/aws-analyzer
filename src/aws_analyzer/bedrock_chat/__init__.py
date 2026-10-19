"""
bedrock_chat.py - a chat window for Amazon Bedrock Knowledge Bases, for SageMaker / Jupyter notebooks.

Part of aws-analyzer: ``pip install aws-analyzer``, then

    from aws_analyzer import chat
    chat()                                                  # pick a knowledge base and a model, then ask
    chat("support-docs", model="sonnet", n=8, temperature=0.2)

The window has the conversation on the left (each answer with its [1] citations, its sources, and the exact request
and response) and six tabs on the right:

    Settings       what's sent with every question: passages, search type, filter, reranker, temperature, prompt...
                   Change a value in place, remove it with x, or add any field RetrieveAndGenerate takes.
    Test           a list of questions, one per line, asked with these settings, each on its own: how each did
                   (answered, "unable to assist", grounded, the file it should cite), and what changed since the last
                   run. Change a setting, run them again, and each line says whether it did better. Try variations
                   asks them with every combination of the settings, models or data sources you list, ranked.
    Runs           every test run, ranked against the others of the same questions: open one, switch to its setup,
                   compare them, and save them to a file (or load one) so they outlast a kernel restart.
    Code           this setup to copy and run anywhere: a Python script (boto3 only) that asks your test questions,
                   the config as JSON (the request without the question), or an AWS CLI command.
    Request        the exact request your next question sends, as JSON, highlighted. Edit it by hand, or copy it as
                   Python.
    Response       what Bedrock sent back to the last question, as JSON.

Answer / Retrieve only, beside the question box, picks what a question does: an answer from the model
(RetrieveAndGenerate), or only the search behind it (Retrieve): every passage it finds, best first, with its score,
and no answer. Ask the same question both ways to tell a retrieval problem from an answer problem.

Requirements: boto3 (required). ipywidgets for the window (preinstalled on SageMaker). Without it, or outside
Jupyter, ask() and the other commands below work as reports.

The file has two layers:

    BedrockChatAnalyzer  Pure logic. Turns your settings into a RetrieveAndGenerate request, sends it and returns
                         plain Python data (an Answer with the text, citations, sources, request and response).
                         Never prints.
    BedrockChatView      Notebook UI: the chat window, plus commands that render reports (HTML in Jupyter, plain
                         text in a terminal).

Nothing here changes a knowledge base: RetrieveAndGenerate reads it and generates text, and Retrieve only reads it.

More
----
    ui = chat("support-docs")                         # the window; ui is the view behind it
    ui.ask("How long do refunds take?")               # an answer as a report, in the same conversation
    ui.retrieve("How long do refunds take?")          # only the search: every passage it finds, no answer
    ui.set(temperature=0.2, search_type="hybrid")     # change settings (an open window follows)
    ui.use(data_source="faq")                         # ask only one of the knowledge base's data sources
    ui.files()                                        # the knowledge base's files, to pick from
    ui.use(files=["refund-policy.pdf", "faq/returns.md"])   # ask only these files ("all" for every file)
    ui.set("generationConfiguration.performanceConfig.latency", "optimized")   # any field, by its path
    ui.unset("temperature")                           # stop sending one
    ui.settings()                                     # what's sent, in plain English, with warnings
    ui.values                                         # the same settings as a dict: {'n': 5, ...}
    ui.fields("reranker")                               # every field you can set: type, range, what it does
    ui.request()                                      # the exact JSON your next question sends, and the Python call
    ui.last()                                         # the last answer: sources in full, request and response
    ui.ask_all(["How long do refunds take? | refund-policy.pdf", "Can I return a gift?"])   # a list, each answered
    ui.set(n=10); ui.ask_all()                        # the same list again: which questions did better or worse
    ui.sweep(n=[5, 10], search_type=["SEMANTIC", "HYBRID"])   # every combination of these, the setups ranked
    ui.sweep(model=["haiku", "sonnet"], reranker=[None, "cohere"])    # models, data sources and files too
    ui.runs()                                         # every test run so far, ranked against the others
    ui.use_run()                                      # switch to the best run's setup (use_run(7): run 7's)
    ui.compare_runs(2, 5)                             # two runs side by side, question by question
    ui.save_runs()                                    # keep every run in kb-test-runs.jsonl, and every later one
    ui.load_runs()                                    # after a restart: the runs back, to compare and reuse
    ui.results()                                      # the last test run again, as a report
    ui.code()                                         # this setup as a Python script, JSON and an AWS CLI command
    ui.new_chat()                                     # forget the conversation
    ui.help()                                         # every command

    a = ui.answers[-1]                                # Answer: a.text, a.citations, a.sources, a.request, a.response
    df = ui.batches[-1].to_df()                       # the last test run: one row per question
    df = ui.sweeps[-1].to_df()                        # the last sweep: one row per setup, best first
    core = ui.core                                    # BedrockChatAnalyzer
    params = core.request("support-docs", "refund window?", {"n": 8})        # the request, without sending it
    a = core.ask("support-docs", "refund window?", {"n": 8, "temperature": 0.2}, model="sonnet")
    r = core.retrieve("support-docs", "refund window?", {"n": 8})          # r.sources: every passage, ranked
    batch = core.ask_all("support-docs", ["refund window?", "gift returns?"], {"n": 8})   # Batch: an Answer each
"""

from __future__ import annotations

from .._bedrock.estimates import estimate_tokens, generation_cost, query_cost
from .._bedrock.files import (
    FILE_LIST_LINKS, SavedFileList, file_list_lines, file_list_name, parse_list_link, presigned_url, write_file_list,
)
from .._bedrock.filters import DATA_SOURCE_KEY, build_filter, data_source_filter, describe_sources, with_data_sources
from .._bedrock.foundation_models import parse_models
from .._bedrock.helpers import SEARCHABLE, parse_kb_ref, source_name, split_metadata
from .._bedrock.models import Citation, KBDocument, ModelInfo, Passage
from .._bedrock.parsing import match_expected, parse_document, parse_passage, parse_retrieve
from .._bedrock.prices import (
    BEDROCK_PRICES, DEFAULT_MODEL, DEFAULT_RERANK_MODEL, GLOBAL_MODEL_PRICES, MODEL_PRICES, model_price, short_model,
)
from .._bedrock.words import best_snippet, question_terms
from .._kit.fmt import human_age, human_money
from .._kit.text import search_rank
from .helpers import (
    BATCH_LIMIT, BATCH_WORKERS, DEFAULT_PROMPT, DEFAULT_SETTINGS, FILE_LIMIT, MAX_QUESTION_CHARS, RUNS_FILE,
    RUNS_FORMAT, SWEEP_LIMIT, SWEEP_MAX_COST,
)
from .models import Answer, DataSource, FileList, KnowledgeBase, RunScore, Schema
from .analysis.parsing import collect_stream, parse_rag
from .analysis.schema import Field, request_schema
from .analysis.filters import (
    SOURCE_URI_KEY, as_filter, describe_filter, files_filter, split_condition, split_data_sources, with_files,
)
from .analysis.choices import describe_files, file_labels, file_path, kb_search_text, match_files, match_kbs, rerank_arn
from .analysis.request import (
    build_request, build_retrieve_request, coerce_setting, describe_setting, normalize_settings, retrieve_settings,
    settings_from_request, validate_request,
)
from .analysis.findings import answer_cost, answer_findings, cited_ranks, compare_findings, settings_findings
from .analysis.batches import (
    Batch, BatchItem, batch_changes, batch_estimate, batch_findings, expected_at, format_questions, item_verdict,
    parse_questions, question_list,
)
from .analysis.sweeps import (
    apply_setup, format_variations, parse_variations, run_setup, setup_label, sweep_setups, varied_setups,
)
from .analysis.ranking import Sweep, rank_runs, ranking_findings, run_score, shared_questions, sweep_estimate
from .analysis.records import read_runs, run_from_record, run_record
from .analysis.code import cli_command, config_json, config_of, python_call, python_script
from .analyzer import BedrockChatAnalyzer
from .view.chat_view import BedrockChatView, chat

__all__ = [
    "BATCH_LIMIT",
    "BATCH_WORKERS",
    "BEDROCK_PRICES",
    "DATA_SOURCE_KEY",
    "DEFAULT_MODEL",
    "DEFAULT_PROMPT",
    "DEFAULT_RERANK_MODEL",
    "DEFAULT_SETTINGS",
    "FILE_LIMIT",
    "FILE_LIST_LINKS",
    "GLOBAL_MODEL_PRICES",
    "MAX_QUESTION_CHARS",
    "MODEL_PRICES",
    "RUNS_FILE",
    "RUNS_FORMAT",
    "SEARCHABLE",
    "SOURCE_URI_KEY",
    "SWEEP_LIMIT",
    "SWEEP_MAX_COST",
    "Answer",
    "Batch",
    "BatchItem",
    "BedrockChatAnalyzer",
    "BedrockChatView",
    "Citation",
    "DataSource",
    "Field",
    "FileList",
    "KBDocument",
    "KnowledgeBase",
    "ModelInfo",
    "Passage",
    "RunScore",
    "SavedFileList",
    "Schema",
    "Sweep",
    "answer_cost",
    "answer_findings",
    "apply_setup",
    "as_filter",
    "batch_changes",
    "batch_estimate",
    "batch_findings",
    "best_snippet",
    "build_filter",
    "build_request",
    "build_retrieve_request",
    "chat",
    "cited_ranks",
    "cli_command",
    "coerce_setting",
    "collect_stream",
    "compare_findings",
    "config_json",
    "config_of",
    "data_source_filter",
    "describe_files",
    "describe_filter",
    "describe_setting",
    "describe_sources",
    "estimate_tokens",
    "expected_at",
    "file_labels",
    "file_list_lines",
    "file_list_name",
    "file_path",
    "files_filter",
    "format_questions",
    "format_variations",
    "generation_cost",
    "human_age",
    "human_money",
    "item_verdict",
    "kb_search_text",
    "match_expected",
    "match_files",
    "match_kbs",
    "model_price",
    "normalize_settings",
    "parse_document",
    "parse_kb_ref",
    "parse_list_link",
    "parse_models",
    "parse_passage",
    "parse_questions",
    "parse_rag",
    "parse_retrieve",
    "parse_variations",
    "presigned_url",
    "python_call",
    "python_script",
    "query_cost",
    "question_list",
    "question_terms",
    "rank_runs",
    "ranking_findings",
    "read_runs",
    "request_schema",
    "rerank_arn",
    "retrieve_settings",
    "run_from_record",
    "run_record",
    "run_score",
    "run_setup",
    "search_rank",
    "settings_findings",
    "settings_from_request",
    "setup_label",
    "shared_questions",
    "short_model",
    "source_name",
    "split_condition",
    "split_data_sources",
    "split_metadata",
    "sweep_estimate",
    "sweep_setups",
    "validate_request",
    "varied_setups",
    "with_data_sources",
    "with_files",
    "write_file_list",
]

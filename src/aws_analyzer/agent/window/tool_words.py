"""
What each tool call does, in plain English, for the comment beside it in the chat window: _TOOL_WORDS, by tool name,
says it while the call runs and once it's done, with {t} standing for what the call is about (the function, bucket,
state machine, API or table it names). A tool it doesn't have reads as its name.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _ToolWords:
    """
    What a tool call does, for the comment beside it: while it runs and once it's done, with {t} standing for what
    it's about (the function, bucket or folder), and what to say when the call names none.
    """

    running: str
    done: str
    nothing: str = ""


_TOOL_WORDS = {
    "investigate": _ToolWords("handing {t} to an investigator", "an investigator looked into {t}", "a service"),
    "find_functions": _ToolWords("finding functions", "found functions"),
    "function_health": _ToolWords("checking every function's health", "checked every function's health"),
    "function_errors": _ToolWords("reading {t}'s errors", "read {t}'s errors", "the function"),
    "function_logs": _ToolWords("reading {t}'s logs", "read {t}'s logs", "the function"),
    "function_performance": _ToolWords("timing {t}'s runs", "timed {t}'s runs", "the function"),
    "function_details": _ToolWords("reading {t}'s settings", "read {t}'s settings", "the function"),
    "function_code": _ToolWords("reading {t}'s code", "read {t}'s code", "the function"),
    "function_source": _ToolWords("reading {t}'s source", "read {t}'s source", "the function"),
    "search_code": _ToolWords("searching {t}'s code", "searched {t}'s code", "the function"),
    "find_symbol": _ToolWords("looking up a name in {t}'s code", "looked up a name in {t}'s code", "the function"),
    "find_buckets": _ToolWords("finding buckets", "found buckets"),
    "bucket_details": _ToolWords("reading {t}'s settings", "read {t}'s settings", "the bucket"),
    "list_folder": _ToolWords("listing {t}", "listed {t}", "a folder"),
    "find_files": _ToolWords("finding files in {t}", "found files in {t}", "a bucket"),
    "folder_summary": _ToolWords("summing up {t}", "summed up {t}", "a folder"),
    "file_info": _ToolWords("reading {t}", "read {t}", "a file"),
    "preview_file": _ToolWords("reading what's in {t}", "read what's in {t}", "a file"),
    "s3_triggers": _ToolWords("checking what {t} triggers", "checked what {t} triggers", "each bucket"),
    "find_knowledge_bases": _ToolWords("finding knowledge bases", "found knowledge bases"),
    "kb_health": _ToolWords("checking {t}'s health", "checked {t}'s health", "the knowledge base"),
    "kb_syncs": _ToolWords("reading {t}'s syncs", "read {t}'s syncs", "the knowledge base"),
    "kb_files": _ToolWords("checking {t}'s files", "checked {t}'s files", "the knowledge base"),
    "search_kb": _ToolWords("searching the knowledge base for '{t}'", "searched the knowledge base for '{t}'",
                            "a question"),
    "file_chunks": _ToolWords("reading {t}'s chunks", "read {t}'s chunks", "a file"),
    "answer_kb": _ToolWords("asking the knowledge base '{t}'", "asked the knowledge base '{t}'", "a question"),
    "find_state_machines": _ToolWords("finding state machines", "found state machines"),
    "state_machine_details": _ToolWords("reading {t}'s definition", "read {t}'s definition", "the state machine"),
    "state_machine_failures": _ToolWords("finding why {t} fails", "found why {t} fails", "the state machine"),
    "list_executions": _ToolWords("listing {t}'s executions", "listed {t}'s executions", "the state machine"),
    "execution_details": _ToolWords("following {t} step by step", "followed {t} step by step", "an execution"),
    "find_apis": _ToolWords("finding APIs", "found APIs"),
    "api_details": _ToolWords("reading {t}'s routes and stages", "read {t}'s routes and stages", "the API"),
    "api_errors": _ToolWords("finding why {t}'s calls fail", "found why {t}'s calls fail", "the API"),
    "api_calls": _ToolWords("reading {t}'s calls", "read {t}'s calls", "the API"),
    "sagemaker_running": _ToolWords("checking what SageMaker is billing", "checked what SageMaker is billing"),
    "training_jobs": _ToolWords("listing training jobs", "listed training jobs"),
    "training_job": _ToolWords("reading training job {t}", "read training job {t}", "a training job"),
    "sagemaker_endpoints": _ToolWords("checking SageMaker endpoints", "checked SageMaker endpoints"),
    "sagemaker_pipelines": _ToolWords("listing SageMaker pipelines", "listed SageMaker pipelines"),
    "pipeline_run": _ToolWords("following {t}'s run step by step", "followed {t}'s run step by step", "a pipeline"),
    "find_tables": _ToolWords("finding DynamoDB tables", "found DynamoDB tables"),
    "table_details": _ToolWords("reading table {t}", "read table {t}", "a table"),
    "table_shape": _ToolWords("sampling {t}'s items", "sampled {t}'s items", "a table"),
    "find_vector_stores": _ToolWords("finding OpenSearch domains and collections",
                                     "found OpenSearch domains and collections"),
    "store_indexes": _ToolWords("listing {t}'s indexes", "listed {t}'s indexes", "a domain"),
    "index_details": _ToolWords("reading index {t}", "read index {t}", "an index"),
    "check_vectors": _ToolWords("checking {t}'s vectors", "checked {t}'s vectors", "an index"),
    "compare_metric": _ToolWords("comparing {t} with yesterday and last week",
                                 "compared {t} with yesterday and last week", "a number"),
    "find_alarms": _ToolWords("reading CloudWatch alarms", "read CloudWatch alarms"),
    "save_function_code": _ToolWords("saving {t}'s code to the workspace", "saved {t}'s code to the workspace",
                                     "the function"),
    "save_logs": _ToolWords("saving {t}'s logs to the workspace", "saved {t}'s logs to the workspace", "the function"),
    "save_s3_files": _ToolWords("saving {t} to the workspace", "saved {t} to the workspace", "files"),
    "read_runbook": _ToolWords("reading the runbook {t}", "read the runbook {t}", "a runbook"),
    "run_python": _ToolWords("running Python", "ran Python"),
    "workspace_files": _ToolWords("listing the workspace", "listed the workspace"),
    "show_chart": _ToolWords("putting {t} in the answer", "put {t} in the answer", "a chart"),
    "ls": _ToolWords("listing {t}", "listed {t}", "the workspace"),
    "read_file": _ToolWords("reading {t}", "read {t}", "a file"),
    "write_file": _ToolWords("writing {t}", "wrote {t}", "a file"),
    "edit_file": _ToolWords("editing {t}", "edited {t}", "a file"),
    "glob": _ToolWords("finding files like {t}", "found files like {t}", "a pattern"),
    "grep": _ToolWords("searching the workspace for {t}", "searched the workspace for {t}", "a text"),
    "delete": _ToolWords("deleting {t}", "deleted {t}", "a file"),
}

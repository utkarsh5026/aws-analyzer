"""
This setup as code to copy: a Python script (boto3 only), the config as JSON, or an AWS CLI
command.
"""

from __future__ import annotations

import copy
import json
import shlex
import textwrap
from typing import Any, Iterable

from ..._bedrock.blocks import _plain_json

from .request import _is_retrieve


def _py_literal(value: Any, indent: int = 0, width: int = 100, column: int | None = None) -> str:
    """
    A JSON value as Python source, one key per line once it's too long for one line. column is where the value
    starts on its line (after its key), when that's further than indent.
    """
    flat = repr(value)
    start = indent if column is None else column
    if not isinstance(value, (dict, list)) or not value or start + len(flat) + 1 <= width:
        return flat
    pad = " " * (indent + 4)
    if isinstance(value, dict):
        lines = [f"{pad}{k!r}: {_py_literal(v, indent + 4, width, indent + 6 + len(repr(k)))},"
                 for k, v in value.items()]
        return "{\n" + "\n".join(lines) + "\n" + " " * indent + "}"
    lines = [f"{pad}{_py_literal(v, indent + 4, width)}," for v in value]
    return "[\n" + "\n".join(lines) + "\n" + " " * indent + "]"


def python_call(params: dict[str, Any], region: str = "", width: int = 100) -> str:
    """
    The same RetrieveAndGenerate (or Retrieve) call as Python, to paste into a cell or a script. width is where
    long lines break.
    """
    where = f", region_name={region!r}" if region else ""
    if _is_retrieve(params):
        return (
            "import boto3\n\n"
            f"client = boto3.client('bedrock-agent-runtime'{where})\n"
            f"response = client.retrieve(**{_py_literal(params, width=width)})\n"
            "for result in response['retrievalResults']:\n"
            "    print(result.get('score'), result['content'].get('text', '')[:200])"
        )
    return (
        "import boto3\n\n"
        f"client = boto3.client('bedrock-agent-runtime'{where})\n"
        f"response = client.retrieve_and_generate(**{_py_literal(params, width=width)})\n"
        "print(response['output']['text'])"
    )


# --------------------------------------------------------- this setup as code, to copy and run anywhere

_QUESTION_KEYS = ("input", "retrievalQuery", "sessionId")  # what a request holds besides its setup


def config_of(params: dict[str, Any]) -> dict[str, Any]:
    """
    A request without its question and session: the setup (knowledge base, model, settings, filter) to keep and send
    with any question: client.retrieve_and_generate(input={'text': question}, **config), or for a Retrieve request
    client.retrieve(retrievalQuery={'text': question}, **config).
    """
    return {key: copy.deepcopy(value) for key, value in params.items() if key not in _QUESTION_KEYS}


def config_json(params: dict[str, Any]) -> str:
    """
    config_of(params) as indented JSON: the file to keep the setup in, which the AWS CLI's --cli-input-json reads.
    """
    return json.dumps(_plain_json(config_of(params)), indent=2, ensure_ascii=False)


def _request_question(params: dict[str, Any]) -> str:
    return str((params.get("input") or params.get("retrievalQuery") or {}).get("text") or "")


def python_script(params: dict[str, Any], region: str = "", questions: Iterable[str] = (), *, about: str = "",
                  width: int = 100) -> str:
    """
    A script that asks a list of questions with the same setup as `params` (a RetrieveAndGenerate or Retrieve
    request) and prints each answer with the files it cites (for Retrieve, each passage found, with its score). It
    needs only boto3: paste it into a cell, or save it as a .py file and run it. Without questions it asks the
    request's own. about: a comment saying where the setup came from. width is where long lines break.
    """
    asked = [str(q) for q in questions] or [_request_question(params) or "<your question>"]
    where = f", region_name={region!r}" if region else ""
    lines = ["import boto3", "", f"client = boto3.client('bedrock-agent-runtime'{where})", ""]
    lines += [f"# {line}" for line in textwrap.wrap(about, width - 2)]
    lines.append(f"CONFIG = {_py_literal(config_of(params), width=width, column=9)}")
    listing = f"questions = {_py_literal(asked, width=width, column=12)}"
    if _is_retrieve(params):
        lines += [
            "", "",
            "def search(question):",
            '    """Every passage the knowledge base finds for a question, best first, with its score."""',
            "    return client.retrieve(retrievalQuery={'text': question}, **CONFIG)",
            "", "",
            listing,
            "for question in questions:",
            "    print('Q:', question)",
            "    for result in search(question)['retrievalResults']:",
            "        uri = result.get('metadata', {}).get('x-amz-bedrock-kb-source-uri', '')",
            "        text = ' '.join(result['content'].get('text', '').split())",
            "        print(f\"   {result.get('score', 0):.3f}  {uri}  {text[:100]}\")",
            "    print()",
        ]
    else:
        lines += [
            "", "",
            "def ask(question, session_id=None):",
            '    """Bedrock\'s answer to a question. Pass an earlier response\'s sessionId to follow up on it."""',
            "    request = {'input': {'text': question}, **CONFIG}",
            "    if session_id:",
            "        request['sessionId'] = session_id",
            "    return client.retrieve_and_generate(**request)",
            "", "",
            "def cited_files(response):",
            '    """The files an answer cites, each once."""',
            "    files = []",
            "    for citation in response.get('citations', []):",
            "        for ref in citation.get('retrievedReferences', []):",
            "            uri = ref.get('metadata', {}).get('x-amz-bedrock-kb-source-uri', '')",
            "            if uri and uri not in files:",
            "                files.append(uri)",
            "    return files",
            "", "",
            listing,
            "for question in questions:",
            "    response = ask(question)",
            "    print('Q:', question)",
            "    print('A:', response['output']['text'])",
            "    for uri in cited_files(response):",
            "        print('   cited:', uri)",
            "    print()",
        ]
    return "\n".join(lines)


def cli_command(params: dict[str, Any], region: str = "", question: str | None = None) -> str:
    """
    The AWS CLI (v2) command that sends the same request from a terminal (bash or zsh) with `question` (the
    request's own when None), and prints the answer: for a Retrieve request, each passage's score and file.
    """
    text = question if question is not None else (_request_question(params) or "<your question>")
    if _is_retrieve(params):
        request = {"retrievalQuery": {"text": text}, **config_of(params)}
        lines = ["aws bedrock-agent-runtime retrieve"]
        shown = """--query 'retrievalResults[].[score, metadata."x-amz-bedrock-kb-source-uri"]' --output table"""
    else:
        request = {"input": {"text": text}, **config_of(params)}
        lines = ["aws bedrock-agent-runtime retrieve-and-generate"]
        shown = "--query output.text --output text"
    if region:
        lines.append(f"--region {shlex.quote(region)}")
    lines += [shown, "--cli-input-json " + shlex.quote(json.dumps(_plain_json(request), indent=2, ensure_ascii=False))]
    return " \\\n  ".join(lines)

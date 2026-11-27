"""
The search across a SageMaker notebook's own files: search_files() and read_file() on a fake notebook's disk, the
pure side (parse_file_query, notebook_rows, match_file, wants_file, ...), and the agent's search_notebook_files and
read_notebook_file tools over them.
"""

import json
import os

import pytest

from aws_analyzer.agent import AgentAnalyzer
from aws_analyzer.agent.results import _asked_lines
from aws_analyzer.sagemaker_env import (
    SageMakerAnalyzer, file_kind, is_secret_file, match_file, notebook_rows, parse_file_query, text_rows, wants_file,
)
from aws_analyzer.sagemaker_env import files as filesmod

HOME = "home/ec2-user/SageMaker"

NOTEBOOK = {"cells": [
    {"cell_type": "markdown", "source": ["# Churn model\n", "Reads the churn table."]},
    {"cell_type": "code", "source": ["import pandas as pd\n", "df = pd.read_csv('s3://acme-raw/churn.csv')\n",
                                     "df['tenure'].mean()"],
     "outputs": [{"output_type": "error", "ename": "KeyError", "evalue": "'tenure'",
                  "traceback": ["\x1b[31m---------\x1b[0m", "\x1b[31mKeyError\x1b[0m: 'tenure'"]}]},
    {"cell_type": "code", "source": "print(len(df))",
     "outputs": [{"output_type": "stream", "text": ["120034\n"]},
                 {"output_type": "display_data", "data": {"image/png": "iVBORw0KGgo="}}]},
]}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text)


@pytest.fixture
def root(tmp_path):
    """
    A notebook instance's disk: a churn project (a notebook, a training script, a CSV, a config), notes, a log,
    and what a search never reads (a secrets file, a credentials folder, a conda environment, checkpoints,
    __pycache__, a binary file, a link) and /proc.
    """
    home = tmp_path / HOME
    write(home / "churn/explore.ipynb", json.dumps(NOTEBOOK))
    write(home / "churn/train.py", "import boto3\n\n\ndef load_orders(day):\n    return read_csv('orders.csv')\n\n"
                                   "API_KEY = 'sk-live-abcdefghijklmnop'\n")
    write(home / "churn/data/customers.csv", "id,tenure,plan\n1,12,pro\n2,3,free\n")
    write(home / "churn/config.yaml", "bucket: acme-raw\ntable: churn\n")
    write(home / "notes.md", "tenure is months since signup\n")
    write(home / "logs/run.log", "INFO loaded 120034 rows\nERROR KeyError: 'tenure'\n")
    write(home / ".env", "TENURE_SECRET=xyz\n")
    write(home / "secrets.json", '{"tenure": "xyz"}')
    write(home / ".aws/credentials", "tenure\n")
    write(home / "envs/py312/conda-meta/history", "tenure\n")
    write(home / "envs/py312/lib/tenure.py", "tenure = 1\n")
    write(home / "churn/.ipynb_checkpoints/explore-checkpoint.ipynb", "tenure")
    write(home / "churn/__pycache__/train.cpython-312.pyc", "tenure")
    write(home / "churn/tenure_model.bin", b"\x00\x01tenure\x00")
    os.symlink(tmp_path / "proc", home / "proc-link")
    write(tmp_path / "proc/self/environ", "AWS_SECRET_ACCESS_KEY=tenure\n")
    return tmp_path


@pytest.fixture
def core(root):
    return SageMakerAnalyzer(root=str(root))


def test_reading_a_search():
    query = parse_file_query('churn "feature store" -test')
    assert query.terms == ["churn", "feature store"] and query.excluded == ["test"] and not query.case_sensitive
    assert parse_file_query("KeyError tenure").case_sensitive
    assert parse_file_query('-"old notes" churn').excluded == ["old notes"]
    assert parse_file_query(r"read_csv\(.*churn", regex=True).terms_in("pd.read_csv('churn.csv')") == {0}
    for bad, message in (("", "Give words"), ("-test", "only leaves words out"), (" ".join("abcdefghi"), "at most 8"),
                         ("x" * 201, "keep it under")):
        with pytest.raises(ValueError, match=message):
            parse_file_query(bad)
    with pytest.raises(ValueError, match="repeats a group"):
        parse_file_query("(a+)+b", regex=True)
    with pytest.raises(ValueError, match="isn't a regular expression"):
        parse_file_query("read_csv(", regex=True)


def test_kinds_and_what_files_means():
    assert [file_kind(p) for p in ("a.ipynb", "b.py", "c.csv", "d.yaml", "e.md", "f.log", "g.parquet")] == [
        "notebook", "python", "data", "config", "docs", "log", "other"]
    assert wants_file("churn/a.ipynb", "notebooks") and not wants_file("churn/a.py", "notebooks")
    assert wants_file("churn/a.ipynb", "code") and wants_file("x/b.csv", "*.csv, logs")
    assert wants_file("churn/data/c.csv", "churn/") and not wants_file("other/c.csv", "churn")
    assert all(is_secret_file(n) for n in (".env", "prod.env", "id_rsa", "key.pem", "credentials", "secrets.json"))
    assert not any(is_secret_file(n) for n in ("env.py", "environment.yml", "keys.md", "secret_santa.ipynb"))


def test_a_notebook_is_read_cell_by_cell_with_its_text_outputs():
    rows = notebook_rows(json.dumps(NOTEBOOK))
    lines = [line for _, line in rows]
    assert lines[:3] == ["# %% cell 1 [markdown]", "# Churn model", "Reads the churn table."]
    assert "# >> KeyError: 'tenure'" in lines and lines.count("# >> KeyError: 'tenure'") == 1
    assert "# >> ---------" in lines and "# >> 120034" in lines and not any("iVBOR" in line for line in lines)
    assert rows[7] == ("cell 2 output", "# >> KeyError: 'tenure'") and rows[8] == ("cell 2 output", "# >> ---------")
    assert notebook_rows("not json") is None and notebook_rows('{"cells": 3}') is None


def test_a_file_matches_when_it_holds_every_word_in_its_text_or_path():
    rows = text_rows("import boto3\ndef load_orders():\n    boto3.client('s3')\n")
    hit = match_file("churn/train.py", rows, parse_file_query("churn boto3"))
    assert hit.in_name and hit.missing == [] and [line.line for line in hit.lines] == [1, 3]
    assert hit.lines[1].context == [(2, "def load_orders():")]
    near = match_file("churn/train.py", rows, parse_file_query("boto3 dynamodb"))
    assert near.missing == ["dynamodb"]
    assert match_file("churn/train.py", rows, parse_file_query("boto3 -load_orders")) is None
    assert match_file("x.py", rows, parse_file_query("nothing")) is None


def test_search_finds_the_files_that_hold_every_word_the_best_first(core):
    search = core.search_files("tenure")
    paths = [hit.path for hit in search.hits]
    assert paths[:3] == ["churn/explore.ipynb", "notes.md", "churn/tenure_model.bin"]
    assert set(paths[3:]) == {"churn/data/customers.csv", "logs/run.log"}
    assert search.hits[0].path == "churn/explore.ipynb" and search.folder == f"/{HOME}"
    cells = {line.cell for line in search.hits[0].lines}
    assert cells == {"cell 2", "cell 2 output"}
    binary = next(hit for hit in search.hits if hit.path.endswith(".bin"))
    assert binary.in_name and binary.lines == [] and "binary" in binary.note
    assert search.skipped == {"secrets files": 2, "Python environments": 1, "binary files (matched by name only)": 1}
    assert search.files_read == 6 and search.cut_short == "" and search.other_disks == []


def test_words_together_a_name_and_a_kind_narrow_it(core):
    assert [hit.path for hit in core.search_files("churn train").hits] == ["churn/train.py"]
    assert [hit.path for hit in core.search_files("KeyError tenure").hits] == ["churn/explore.ipynb", "logs/run.log"]
    assert [hit.path for hit in core.search_files("KeyError", files="notebooks").hits] == ["churn/explore.ipynb"]
    assert core.search_files("keyerror", files="logs").hits[0].lines[0].text == "ERROR KeyError: 'tenure'"
    assert core.search_files("Keyerror").hits == []
    assert [hit.path for hit in core.search_files("acme-raw -yaml").hits] == ["churn/explore.ipynb"]
    assert [hit.path for hit in core.search_files(r"def \w+\(day", regex=True).hits] == ["churn/train.py"]
    assert [hit.path for hit in core.search_files("tenure", folder="churn/data").hits] == ["churn/data/customers.csv"]


def test_when_nothing_holds_every_word_it_says_what_comes_close(core):
    search = core.search_files("load_orders dynamodb")
    assert search.hits == [] and [hit.path for hit in search.near] == ["churn/train.py"]
    assert search.near[0].missing == ["dynamodb"]
    assert "load_orders" in core.search_files("load_order").hits[0].lines[0].text
    assert "tenure" in core.search_files("tenrue").suggestions


def test_search_never_reads_secrets_credentials_or_proc(core):
    for words in ("TENURE_SECRET", "AWS_SECRET_ACCESS_KEY", "sk-live"):
        found = core.search_files(words)
        assert [hit.path for hit in found.hits] == (["churn/train.py"] if words == "sk-live" else [])
    for folder in ("/proc", ".aws", "proc-link"):
        with pytest.raises(ValueError, match="isn't searched"):
            core.search_files("x", folder=folder)
    with pytest.raises(ValueError, match="There's no folder"):
        core.search_files("x", folder="nope")
    with pytest.raises(ValueError, match="isn't a kind, a glob or a folder"):
        core.search_files("x", files="tables")


def test_a_search_stops_at_its_limits_and_says_so(core, monkeypatch):
    monkeypatch.setattr(filesmod, "_MOST_FILES_READ", 2)
    assert "after reading 2 files" in core.search_files("tenure").cut_short
    monkeypatch.setattr(filesmod, "_MOST_FILES_READ", 20_000)
    core.search_seconds = -1
    assert "time limit" in core.search_files("tenure").cut_short


def test_read_file_shows_a_notebook_as_its_cells(core):
    text = core.read_file("churn/explore.ipynb")
    assert text.kind == "notebook" and text.lines[3] == "# %% cell 2 [code]" and not text.cut_short
    hit = core.search_files("read_csv", files="notebooks").hits[0]
    assert hit.lines[0].line == 6 and text.lines[5] == "df = pd.read_csv('s3://acme-raw/churn.csv')"
    assert core.read_file(f"/{HOME}/churn/train.py").lines[3] == "def load_orders(day):"
    assert core.read_file("~/SageMaker/notes.md").lines == ["tenure is months since signup"]
    assert "secrets file" in core.read_file(".env").note
    assert "credentials folders" in core.read_file(".aws/credentials").note
    assert "system's view" in core.read_file("/proc/self/environ").note
    assert "system's view" in core.read_file("proc-link/self/environ").note
    assert "a binary file" in core.read_file("churn/tenure_model.bin").note
    assert "is a folder holding 6 entries" in core.read_file("churn").note
    assert "There's no file" in core.read_file("nope.py").note


@pytest.fixture
def agent(core):
    return AgentAnalyzer(region="us-east-1", toolkits=["sagemaker"], cores={"sagemaker": core})


def tool(agent, name):
    return next(t for t in agent.tools() if t.__name__ == name)


def test_the_agent_searches_the_notebooks_files_and_reads_one(agent):
    found = tool(agent, "search_notebook_files")("KeyError tenure")
    assert found["found"] == 2 and found["results"][0]["file"] == "churn/explore.ipynb"
    first = found["results"][0]
    assert first["kind"] == "notebook" and first["cells"] == ["cell 2", "cell 2 output"]
    assert first["lines"] == ["6: df = pd.read_csv('s3://acme-raw/churn.csv')", ">> 7: df['tenure'].mean()",
                              ">> 8: # >> KeyError: 'tenure'", "9: # >> ---------"]
    assert found["not_read"]["secrets files"] == 2 and "hint" not in found

    secret = tool(agent, "search_notebook_files")("API_KEY", files="python")
    assert secret["results"][0]["lines"] == ["6: ", ">> 7: API_KEY = '[hidden]'"]

    missing = tool(agent, "search_notebook_files")("tenrue")
    assert missing["found"] == 0 and "tenure" in missing["did_you_mean"] and "Close spellings" in missing["hint"]

    page = tool(agent, "read_notebook_file")("churn/train.py", lines="4")
    assert page["total_lines"] == 7 and ">> 4: def load_orders(day):" in page["lines"]
    assert page["lines"][-1] == "7: API_KEY = '[hidden]'" and "next" not in page
    kit = agent.toolkit("sagemaker")
    assert kit.read_notebook_file("churn/train.py", lines="4")["_summary"] == "churn/train.py: lines 1-7"
    assert kit.read_notebook_file(".env")["_summary"].startswith(".env looks like a secrets file")
    assert "secrets file" in tool(agent, "read_notebook_file")(".env")["note"]


def test_the_agent_lists_the_file_tools_as_local_reads(agent):
    kit = next(t for t in agent.toolkits if t.name == "sagemaker")
    assert kit.reads["search_notebook_files"] == ("this notebook's own files (read on its disk; no AWS call)",)
    assert "search_notebook_files" in kit.instructions(agent.budget) and "/proc" in kit.privacy


def test_line_ranges_are_shared_with_the_lambda_tools():
    assert _asked_lines("42", 100) == (30, 54, {42}) and _asked_lines("", 10) == (1, 10, set())

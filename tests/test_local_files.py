"""
aws_analyzer.local_files() and clean_up(): what aws-analyzer saved on this machine, and removing only what it made.
"""

import os

import pytest

import aws_analyzer
from aws_analyzer._kit.local_files import find_local_files, remove_local_files

S3_MARK = "# Made by aws-analyzer for what download() and download_zip() save from S3.\n*\n"
LAMBDA_MARK = "# Made by aws-analyzer for the deployment packages download() saves from Lambda.\n*\n"


def write(path, data=b"x" * 100):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture
def notebook(tmp_path, monkeypatch):
    """
    A notebook folder with what aws-analyzer saves beside it, and the sandbox's cache under XDG_CACHE_HOME.
    """
    folder = tmp_path / "work"
    write(folder / "s3-downloads" / ".gitignore", S3_MARK.encode())
    write(folder / "s3-downloads" / "churn" / "part-0.csv", b"x" * 5000)
    write(folder / "lambda-downloads" / ".gitignore", LAMBDA_MARK.encode())
    write(folder / "lambda-downloads" / "etl.zip", b"x" * 2000)
    write(folder / "agent-charts" / "errors-by-day.svg")
    write(folder / "agent-charts" / "errors-by-day.vl.json")
    write(folder / "agent-charts" / "my-notes.md")
    write(folder / "analysis.ipynb")
    write(tmp_path / "cache" / "aws-analyzer" / "pyodide" / "0.27" / "pyodide.wasm", b"x" * 9000)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.chdir(folder)
    return folder


def test_finds_what_aws_analyzer_saved(notebook):
    found = find_local_files()

    kinds = {folder.kind: folder for folder in found.folders}
    assert set(kinds) == {"s3", "lambda", "charts", "sandbox"}
    assert kinds["s3"].files == 2 and kinds["s3"].removable
    assert kinds["charts"].files == 2
    assert kinds["sandbox"].size == 9000


def test_clean_up_removes_what_comes_back_by_itself(notebook, tmp_path):
    result = remove_local_files()

    assert {folder.kind for folder in result.removed} == {"s3", "lambda", "sandbox"}
    assert not (notebook / "s3-downloads").exists()
    assert not (notebook / "lambda-downloads").exists()
    assert not (tmp_path / "cache" / "aws-analyzer").exists()
    assert (notebook / "agent-charts" / "errors-by-day.svg").exists()
    assert (notebook / "analysis.ipynb").exists()


def test_the_agents_memory_and_saved_chats_stay_in_the_cache(notebook, tmp_path):
    kept = tmp_path / "cache" / "aws-analyzer" / "agent"
    write(kept / "memory.sqlite", b"x" * 300)
    write(kept / "chats.sqlite", b"x" * 400)
    sandbox = next(folder for folder in find_local_files().folders if folder.kind == "sandbox")
    assert sandbox.size == 9000 and sandbox.files == 1

    remove_local_files("all")

    assert sorted(os.listdir(tmp_path / "cache" / "aws-analyzer")) == ["agent"]
    assert sorted(os.listdir(kept)) == ["chats.sqlite", "memory.sqlite"]


def test_charts_go_only_when_named_and_other_files_stay(notebook):
    remove_local_files("charts")

    assert sorted(os.listdir(notebook / "agent-charts")) == ["my-notes.md"]
    assert (notebook / "s3-downloads").exists()


def test_dry_run_removes_nothing(notebook):
    result = remove_local_files("all", dry_run=True)

    assert len(result.removed) == 4
    assert (notebook / "s3-downloads" / "churn" / "part-0.csv").exists()
    assert result.freed == sum(folder.size for folder in result.removed)


def test_a_folder_aws_analyzer_didnt_make_is_kept(notebook):
    (notebook / "lambda-downloads" / ".gitignore").unlink()

    result = remove_local_files("lambda")

    kept = {folder.kind: folder.kept for folder in result.kept}
    assert "lambda" not in {folder.kind for folder in result.removed}
    assert "didn't make it" in kept["lambda"]
    assert (notebook / "lambda-downloads" / "etl.zip").exists()


def test_never_the_notebooks_own_folder(notebook):
    write(notebook / ".gitignore", S3_MARK.encode())

    result = remove_local_files("s3", downloads=[notebook])

    assert {folder.path for folder in result.removed} == {str(notebook / "s3-downloads")}
    assert any("notebook's own folder" in folder.kept for folder in result.kept)
    assert (notebook / "analysis.ipynb").exists()


def test_reports_read_and_a_wrong_kind_is_a_note(notebook, capsys):
    aws_analyzer.local_files(mode="text")
    listing = capsys.readouterr().out
    assert "What aws-analyzer saved here" in listing and "clean_up(dry_run=True)" in listing

    aws_analyzer.clean_up(dry_run=True, mode="text")
    assert "Would remove 3 folders" in capsys.readouterr().out

    aws_analyzer.clean_up("buckets", mode="text")
    assert "clean_up() takes 's3'" in capsys.readouterr().out
    assert (notebook / "s3-downloads").exists()


def test_nothing_saved(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)

    aws_analyzer.local_files(mode="text")

    assert "Nothing: no downloads" in capsys.readouterr().out

# Contributing to aws-analyzer

Thanks for helping. Bug reports, ideas for reports that would help you decide something, and pull requests are all
welcome. For a security problem, please follow [SECURITY.md](SECURITY.md) instead of opening an issue.

## Report a bug or ask for something

[Open an issue](https://github.com/utkarsh5026/aws-analyzer/issues/new/choose). For a bug, the most useful things
are the command you ran, what it showed (the text, or a screenshot of the report), what you expected, and your
versions: `import aws_analyzer, boto3, sys; print(aws_analyzer.__version__, boto3.__version__, sys.version)`.
Remove bucket names, account IDs and data you'd rather not share.

## Set up

```bash
git clone https://github.com/utkarsh5026/aws-analyzer.git
cd aws-analyzer
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt    # pinned versions of everything the tests use
python -m pytest                       # every test; moto stands in for AWS, so no account is needed
ruff check .                           # lint (real errors only, see ruff.toml)
```

For the guide site: `pip install -r requirements-docs.txt`, then `mkdocs serve`.

## How the code is laid out

[CLAUDE.md](CLAUDE.md) is the full guide to the code and its conventions. It's written for Claude Code, but it reads
just as well for people. The rules that matter most:

- **One file per service.** Each file in [`analyzers/`](analyzers/) works on its own, pasted into a notebook or
  copied next to one, so files never import each other. Helpers they all need (`human_size`, the render blocks,
  `View.help`, ...) are copied into each file on purpose: when you change one, change the copies too.
- **Only boto3 and the standard library at import time.** pandas, pyarrow, IPython and the file readers are
  imported inside the function that needs them, through `_require(...)`, so a missing package becomes a note that
  says what to install.
- **Read-only against AWS.** Nothing writes to a bucket, table or knowledge base, or stops or deletes anything.
  Reports show the command to run instead. A new AWS call needs its read-only IAM permission listed in README.md.
  On the notebook's disk, the only thing deleted is what `clean_downloads()` removes from `s3.py`'s downloads folder.
- **Python 3.10 and up**, with pandas 2 and 3 both supported.
- **Reports help someone decide what to do.** The answer comes first, findings say what's wrong, what it costs and
  what to run next, and errors become a short note instead of a traceback. CLAUDE.md's "Product goal" section has
  the details, and the existing commands are the examples to follow.

## Make a change

1. Branch from `main`.
2. Add or update tests in [`tests/`](tests/): pure functions directly, AWS calls with moto (or botocore's
   `Stubber` or a fake client for Bedrock, SageMaker Studio, OpenSearch Serverless and the few Lambda reads moto
   doesn't cover; [`tests/fake_opensearch.py`](tests/fake_opensearch.py) stands in for OpenSearch's REST API).
3. Update the docs for anything a user would notice: the service's section in [README.md](README.md) and its guide
   in [`docs/`](docs/).
4. Add a line under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md), in the group it belongs to (Added, Changed,
   Fixed, Removed). Write it for someone upgrading: start with the command (`S3View.preview()`:) and say what they
   can do now. Leave out changes users never see (tests, CI, refactors).
5. Check that each analyzer still imports alone with only boto3:

   ```bash
   for f in analyzers/*.py; do d=$(mktemp -d); cp "$f" "$d/"; (cd "$d" && python -c "import $(basename "$f" .py)") && echo "ok: $f"; done
   ```

   With Claude Code, `/check` runs this and everything else CI runs, plus the project's own rule checks.
6. Open a pull request. CI runs lint and the tests on Python 3.10 to 3.14, imports each analyzer on its own, builds
   the package, and builds the guide site with `--strict` when `docs/` changes.

## Releases

Maintainers release from `main`. With Claude Code, `/release` does it: it writes the changelog entry, picks the
version, opens the release pull request, and after you confirm, merges it and publishes. By hand:

1. `python .claude/skills/release/release.py status` shows what's on `main` since the last release, and whether
   it's ready.
2. `python .claude/skills/release/release.py bump minor` (or `patch`, or a version) moves the Unreleased entries
   under the new version and sets `__version__` in `src/aws_analyzer/__init__.py`. Merge that through a pull
   request.
3. `gh release create v<version> --target <merge commit> --notes-file <(python .claude/skills/release/release.py notes)`.
   [The Release workflow](.github/workflows/release.yml) builds, checks and uploads it to PyPI.

Before 1.0, a minor version (0.2.0) adds commands or changes what one shows, and a patch (0.1.1) only fixes things.
A version can be uploaded to PyPI only once, even if it's deleted afterwards.

## License

By contributing, you agree that your contributions are licensed under the [Apache License 2.0](LICENSE), like the
rest of the project.

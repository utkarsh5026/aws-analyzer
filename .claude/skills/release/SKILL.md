---
name: release
description: Publish a new version of aws-analyzer to PyPI - check what's on main since the last release, write its CHANGELOG entry, pick the version, open a release pull request, and once the user confirms, merge it and publish the GitHub release that uploads to PyPI. Use when the user asks to release, publish, ship or cut a version, or to push their changes to PyPI.
argument-hint: "[patch | minor | major | 0.2.0]"
allowed-tools: Bash(.venv/bin/python .claude/skills/release/release.py:*), Bash(python .claude/skills/release/release.py:*), Bash(git fetch:*), Bash(git log:*), Bash(git diff:*), Bash(git status:*), Bash(gh pr view:*), Bash(gh pr checks:*), Bash(gh run list:*), Bash(gh run view:*), Bash(gh run watch:*)
---

# Release

Ship what's on `main` to PyPI as a new version. Publishing is the one step here that can't be undone: PyPI never
takes the same version twice, even after it's deleted. So everything up to the release pull request is safe to
redo, and the user confirms before anything is merged or published.

How it's wired (see "The PyPI package" in CLAUDE.md): a GitHub release tagged `v<version>` starts
`.github/workflows/release.yml`, which builds, checks, refuses a tag that doesn't match `__version__`, and uploads
with trusted publishing from the `pypi` environment. `release.py` does the mechanical parts. Use `.venv/bin/python`
if it exists, otherwise `python`.

## 1. See where main stands

```bash
.venv/bin/python .claude/skills/release/release.py status
```

It fetches, then prints `__version__`, the last `v*` tag, what PyPI shows, CI on `main`'s newest commit, the pull
requests merged since the last release, what kind of files they changed, and the `## [Unreleased]` entries in
`CHANGELOG.md`. It ends with notes and either `ready to release` or the reasons it isn't.

Stop and tell the user, without changing anything, when:

- **Nothing reaches pip users.** Only docs or development files changed: the guide site already publishes from
  `main` on its own, so there's nothing to release.
- **CI on main isn't green.** Releasing a red build ships the bug. Say which run failed and offer to look.
- **The work isn't on main yet.** Releases are cut from `origin/main`. If the user means changes still on a branch,
  they go through their own pull request first; ask whether to do that or release what `main` has.
- **The working tree is dirty.** Ask before stashing or committing anything.

## 2. Make the release branch

```bash
git switch -c release/v<version> origin/main
```

Use the version you expect from step 3's rules; rename the branch (`git branch -m`) if it changes.

## 3. Write the CHANGELOG entry

Pull requests should already have added their lines under `## [Unreleased]`, but check each pull request from
`status` against it, and fill in what's missing. To see what one changed: `gh pr view <n> --json title,body`,
`git diff v<last>..origin/main --stat`, and the README and `docs/` diffs, which already describe the change in
the user's words.

Write for a data scientist upgrading with `pip install -U aws-analyzer`, in README's voice. Someone should get
each change from a glance, so keep a bullet small:

```markdown
- 🤖 **Agent: asks before a big read** ([#148](https://github.com/utkarsh5026/aws-analyzer/pull/148))
  - Before listing over 20,000 S3 keys or reading more than 3 days of logs, it stops and asks.
  - Pick **go**, **smaller** or **skip**: buttons in `chat()`, or `go()` / `smaller()` / `skip()` after `ask()`.
  - `AgentView(interrupt_on=False)` turns it off.
```

- **The headline**: the service's emoji, then in bold the service and what changed in a few plain words (no code,
  no more than about eight words), then the pull request link.
- **The points under it**: at most three, each one short line holding one fact: what you can do now, the call that
  does it, how to turn it off or what it costs. Lists of tool names, every argument, numbers of bytes and how it
  works inside belong in README and the guide, not here; link the guide section when there's more to tell.
- A fix or a small change can be the headline alone.
- The service emojis: 🪣 S3, 🧭 S3 explorer, 📊 DynamoDB, 📚 Bedrock Knowledge Bases, 💬 Bedrock chat,
  🧪 SageMaker, 🔎 OpenSearch, ⚡ Lambda, 🔀 Step Functions, 🤖 Agent, 🧰 every service (themes, time zones, install).
- Group the bullets under `### Added` (new commands, arguments, file types, services), `### Changed` (output,
  defaults or behaviour that's different), `### Fixed`, `### Removed`, in that order, and leave out empty groups.
  Write the headings plain: `notes` adds their emojis (✨ 🔄 🐛 🗑️).
- A change that can break a notebook goes first under Changed, starts with `**Breaking**:` (`notes` adds ⚠️) and
  its first point says what to change: a renamed or removed command or argument, a data method that returns
  something else, a raised `boto3>=` floor or Python floor.
- Leave out what users never see: tests, CI, Dependabot updates to the dev pins, screenshots, refactors, internal
  names (`_capture`, `_later`), and docs edits other than a new guide.

Rewrite a pull request's line that doesn't read like this: a long bullet is what this step is for.

## 4. Pick the version and bump it

`$ARGUMENTS` may name one (`patch`, `minor`, `major` or `0.2.0`). Before 1.0, the rules are:

| Bump | When | Example |
|---|---|---|
| `minor` | anything under Added or Removed, or anything **Breaking** | 0.1.0 → 0.2.0 |
| `patch` | only Fixed, and Changed that's a fix or a wording change (prices, messages) | 0.1.0 → 0.1.1 |
| `major` | only when the user says the API is stable | 0.x → 1.0.0 |

`status` prints the bump its rules suggest. If the user's choice doesn't fit the entries (a `patch` with
something **Breaking**), say so and ask. Then:

```bash
.venv/bin/python .claude/skills/release/release.py bump <minor|patch|major|0.2.0>
```

It moves the Unreleased entries under `## [<version>] - <today>`, leaves an empty Unreleased above them, updates
the compare links at the bottom and sets `__version__`. It refuses a version that isn't newer than the last tag,
one the changelog already has, or an empty Unreleased.

## 5. Check and open the release pull request

1. Run `/check`. It has to pass, `package` included: that builds the wheel with the new version and imports it
   with only boto3. `tests/test_package.py` checks the changelog has an entry for `__version__`.
2. `release.py notes` prints the entry the GitHub release will show: hard-wrapped lines joined, an emoji on each
   group, a 📸 **What it looks like** section with the screenshots from each linked pull request's description
   (read with `gh pr view`), and the full-diff link. Read it once more as the user, and check every change a user
   can see has its picture. A pull request merged without one (from before the rule, or one whose screenshots
   were ticked off) gets one now: draw the report with `/demo <service> '<call>' --html <scratchpad>/x.html`,
   screenshot it, and add it to that pull request's Screenshots section, so `notes` picks it up.
3. Commit only `CHANGELOG.md` and `src/aws_analyzer/__init__.py`, as `Release <version>`, push the branch and open
   the pull request with the notes as its body:

   ```bash
   git commit -m "Release <version>" -- CHANGELOG.md src/aws_analyzer/__init__.py
   git push -u origin release/v<version>
   gh pr create --title "Release <version>" --body-file <notes file in the scratchpad>
   gh pr checks <n> --watch
   ```

## 6. Confirm, merge and publish

Show the user the version (and why that bump), the release notes, the pull request link and its checks. Then ask
with AskUserQuestion: **Merge and publish <version> to PyPI** / **Change the notes first** / **Not now**. Go on only
on an explicit yes. On "not now", leave the pull request open and say how to finish later (`/release` again).

```bash
gh pr merge <n> --merge --delete-branch
sha=$(gh pr view <n> --json mergeCommit --jq .mergeCommit.oid)
.venv/bin/python .claude/skills/release/release.py notes <version> > <scratchpad>/notes.md
gh release create v<version> --target "$sha" --title "v<version>" --notes-file <scratchpad>/notes.md
```

Tag the merge commit (`--target "$sha"`), not `main`, so a pull request merged in between can't slip into the
release.

## 7. Watch it reach PyPI

```bash
gh run watch "$(gh run list --workflow release.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
```

Then install it in a fresh virtual environment in the scratchpad (`pip install --no-cache-dir
aws-analyzer==<version>`), import `aws_analyzer` from outside the repo and print `__version__`. PyPI's JSON API can
lag a few minutes behind pip.

If the workflow fails:

- **Build and check**: the tag doesn't match `__version__`, or the wheel doesn't build or import. Nothing was
  uploaded. Ask before deleting the release and tag (`gh release delete v<version> --cleanup-tag --yes`), fix it
  in a new pull request, and publish again.
- **Publish to PyPI**, "invalid-publisher": PyPI's trusted publisher must name owner `utkarsh5026`, repository
  `aws-analyzer`, workflow `release.yml` and environment `pypi`. The user fixes that on pypi.org; then re-run the
  failed job (`gh run rerun <id> --failed`).
- **Publish to PyPI**, "file already exists": that version is on PyPI for good. Release the next patch version
  instead.

Finally `git switch main && git pull --ff-only`.

## Report

The version and why, the release and PyPI links, the result of the clean install, and the notes as published
(with how many screenshots they carry).

"""
Fails a pull request that changes the package but shows no screenshot of what it looks like.

    python .github/scripts/check_screenshots.py        # in GitHub Actions, on a pull_request event

A pull request that changes src/aws_analyzer/ needs a picture in its description's "## Screenshots" section: a
markdown image, an <img> tag or a GitHub attachment link. When nothing a user sees changed (tests, a refactor), the
author ticks "No screenshots: nothing a user sees changed" instead. Release pull requests (release/ branches) and
Dependabot's are exempt: their changes are someone else's screenshots, or none.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

PACKAGE = "src/aws_analyzer/"
EXEMPT_BRANCHES = ("release/", "dependabot/")
SCREENSHOTS_HEADING = re.compile(r"^##\s+.*screenshots?\b.*$", re.I | re.M)
NEXT_HEADING = re.compile(r"^##\s", re.M)
PICTURE = re.compile(r"!\[[^\]]*\]\([^)\s]+|<img\s[^>]*src=|https://github\.com/user-attachments/assets/\S+", re.I)
OPTED_OUT = re.compile(r"^\s*[-*]\s*\[[xX]\]\s*No screenshots", re.M)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def screenshots_section(body: str) -> str:
    """
    The text under the description's Screenshots heading, without the template's <!-- --> hints.
    """
    heading = SCREENSHOTS_HEADING.search(body)
    if not heading:
        return ""
    following = NEXT_HEADING.search(body, heading.end())
    section = body[heading.end():following.start() if following else len(body)]
    return HTML_COMMENT.sub("", section)


def problem(body: str, changed_files: list[str], branch: str) -> str:
    """
    Why the pull request fails the rule, or '' when it passes.
    """
    if branch.startswith(EXEMPT_BRANCHES):
        return ""
    touches_package = any(path.startswith(PACKAGE) for path in changed_files)
    if not touches_package:
        return ""

    section = screenshots_section(body or "")
    if PICTURE.search(section) or OPTED_OUT.search(section):
        return ""
    if not SCREENSHOTS_HEADING.search(body or ""):
        return ("The description has no '## Screenshots' section. Add one (see .github/pull_request_template.md) "
                "with a screenshot of each report or window the change touches.")
    return ("The '## Screenshots' section has no picture. Drag a screenshot of each report or window the change "
            "touches into it, or tick 'No screenshots: nothing a user sees changed' if that's true.")


def changed_files(base: str, head: str) -> list[str]:
    """
    The paths the pull request changes, from the checkout's history.
    """
    names = subprocess.run(["git", "diff", "--name-only", f"{base}...{head}"], capture_output=True, text=True,
                           check=True)
    return names.stdout.split()


def main() -> int:
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as event_file:
        pull_request = json.load(event_file)["pull_request"]
    files = changed_files(pull_request["base"]["sha"], pull_request["head"]["sha"])
    reason = problem(pull_request.get("body") or "", files, pull_request["head"]["ref"])
    if reason:
        print(f"::error title=Screenshots needed::{reason}")
        return 1
    print("Screenshots: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

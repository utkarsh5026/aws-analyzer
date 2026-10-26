## What changes for the user

<!-- What someone in a notebook can do now, or what looks different, in two or three short lines. -->

## Screenshots

<!--
Required when the pull request changes src/aws_analyzer/ (the Screenshots check fails without one).
Show each report or window the change touches, the way a user sees it in a notebook:
- a new report: one screenshot of it, with the call that made it as a caption (`ui.summary("s3://demo-lake/")`)
- a changed report: Before and After, side by side or one under the other
- a change to colours or layout: light and dark
`/demo <service> '<call>' --html out.html` draws any report on demo data, no AWS account needed.
Drag the pictures in here. The release notes copy them from this section.
-->

- [ ] No screenshots: nothing a user sees changed (tests, CI, a refactor, docs text)

## Checklist

- [ ] Tests cover the change (`python -m pytest`)
- [ ] README.md and the service's guide in `docs/` describe it
- [ ] A line under `## [Unreleased]` in CHANGELOG.md (not needed for changes users don't see: tests, CI, refactors)
- [ ] Still read-only: any new AWS call only reads, and its IAM permission is listed in README.md
- [ ] Code another service has, or could use, lives once in `_kit/` (`_bedrock/` for the Bedrock pair), not copied

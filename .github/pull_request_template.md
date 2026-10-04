## What changes for the user

<!-- What someone in a notebook can do now, or what looks different. A screenshot of the report helps. -->

## Checklist

- [ ] Tests cover the change (`python -m pytest`)
- [ ] README.md and the service's guide in `docs/` describe it
- [ ] A line under `## [Unreleased]` in CHANGELOG.md (not needed for changes users don't see: tests, CI, refactors)
- [ ] Still read-only: any new AWS call only reads, and its IAM permission is listed in README.md
- [ ] A helper changed in one analyzer is changed in the others too

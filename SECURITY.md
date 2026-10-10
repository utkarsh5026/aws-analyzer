# Security policy

aws-analyzer runs inside notebooks with real AWS credentials and shows data from buckets, tables and knowledge bases,
so security problems in it matter. Thank you for reporting them privately.

## Report a vulnerability

Use GitHub's private reporting: **[Report a vulnerability](https://github.com/utkarsh5026/aws-analyzer/security/advisories/new)**
(the Security tab, then "Report a vulnerability"). Only the maintainers can see the report. Please don't open a
public issue, pull request or discussion about it.

Include what's affected (the file and command), how to reproduce it, and what an attacker could do with it. The aim
is to reply within a week. Once a fix is released, the advisory is published with credit to you, unless you'd
rather not be named.

## What counts

Examples of what we want to hear about:

- **A write to AWS.** The tool promises to be read-only. Any command that creates, changes or deletes something in
  an AWS account, or stops a notebook, is a security bug.
- **Content from AWS that runs or renders as markup.** Object keys, file contents, item values and knowledge base
  passages are untrusted. If any of them can inject HTML, scripts or styles into a report, or turn into
  instructions in the prompt that `ask()` sends to a model, report it.
- **Leaked secrets.** Credentials, session tokens or presigned URLs ending up somewhere they shouldn't, such as in
  a report that's kept when the notebook is saved, in a log or in an error note.
- **Unsafe file handling.** A download or zip that writes outside the folder it was given (path traversal through
  object keys), a `clean_downloads()` that deletes anything but what's in the downloads folder `s3.py` made, or a
  file parser that can be made to use unbounded memory or disk.

Not security bugs: the findings the tool reports about your own AWS account (a public bucket, missing encryption),
and problems in boto3 or in the optional packages it uses, which belong with those projects.

## Supported versions

Fixes go into the latest release on [PyPI](https://pypi.org/project/aws-analyzer/): `%pip install -U aws-analyzer`
gets them. An analyzer file copied next to a notebook (how earlier versions could be used) doesn't get fixes;
install the package instead.

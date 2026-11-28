"""
`python -m aws_analyzer serve`: the same as the `aws-analyzer serve` command, for when the environment's scripts
folder isn't on PATH.
"""

from __future__ import annotations

import sys

from .server.cli import main

if __name__ == "__main__":
    sys.exit(main())

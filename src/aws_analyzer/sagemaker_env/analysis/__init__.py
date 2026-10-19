"""
Pure analysis of a SageMaker notebook, its disk, what's running and its Python environments: no AWS
calls and no reads of the machine, so it runs on data you pass it.
"""

from __future__ import annotations

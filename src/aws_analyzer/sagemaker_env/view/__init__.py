"""
SageMakerView, the notebook UI: notebook.py holds the class and its commands about the notebook, its disk, its
Python environments and what's running, and jobs.py the commands about training jobs, endpoints and pipelines.
"""

from __future__ import annotations

from .notebook import SageMakerView

__all__ = ["SageMakerView"]

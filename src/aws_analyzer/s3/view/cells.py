"""
What more than one group of commands shows: a folder's label and the names of file formats.
"""

from __future__ import annotations


def _folder_label(folder: str) -> str:
    return folder or "(files at this level)"


_FORMAT_LABELS = {
    "arrow": "feather / arrow",
    "excel": "excel",
    "torch": "PyTorch checkpoint",
    "pdf": "PDF",
    "docx": "Word document",
    "pptx": "PowerPoint deck",
    "oldoffice": "Office 97-2003 file",
    "notebook": "Jupyter notebook",
    "npy": "NumPy array",
    "npz": "NumPy arrays (npz)",
}

"""
The address to open the page at. The server listens on 127.0.0.1 only, which a browser on your laptop can't reach on
a SageMaker machine; JupyterLab's server proxy (jupyter-server-proxy, which SageMaker installs) forwards
<your Jupyter address>/proxy/<port>/ to it, behind the same sign-in as the notebook. SageMaker's metadata file says
which kind of machine this is, and so which address that is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

METADATA_FILE = Path("/opt/ml/metadata/resource-metadata.json")
STUDIO_APP_PATHS = {"JupyterLab": "jupyterlab/default", "CodeEditor": "codeeditor/default",
                    "JupyterServer": "jupyter/default", "KernelGateway": "jupyter/default"}


@dataclass(frozen=True)
class PageLink:
    """
    Where to open the page: `url` (through SageMaker's proxy on SageMaker, else the local address), where the
    server listens (`local`), what kind of machine this is in words, and the token the page asks for.
    """

    url: str
    local: str
    where: str
    token: str

    @property
    def is_proxied(self) -> bool:
        return self.url != self.local


def _with_token(base: str, token: str) -> str:
    return f"{base}?token={token}" if token else base


def _proxy_base(metadata: dict, port: int) -> tuple[str, str]:
    """
    (the proxied address without the token, what kind of machine says so), or ('', '') when the metadata doesn't
    say enough to know it.
    """
    from ..sagemaker_env.analysis.machine import parse_metadata

    machine = parse_metadata(metadata)
    region = machine.region
    if machine.kind == "notebook instance" and machine.name and region:
        return f"https://{machine.name}.notebook.{region}.sagemaker.aws/proxy/{port}/", machine.label
    app_path = STUDIO_APP_PATHS.get(machine.app_type, "")
    if machine.kind == "studio" and machine.domain_id and region and app_path:
        return f"https://{machine.domain_id}.studio.{region}.sagemaker.aws/{app_path}/proxy/{port}/", machine.label
    return "", ""


def read_metadata(path: Path = METADATA_FILE) -> dict:
    """
    SageMaker's metadata file as a dict, or {} when this isn't SageMaker (or the file can't be read).
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def page_link(port: int, token: str, *, host: str = "127.0.0.1", metadata: dict | None = None) -> PageLink:
    """
    The page's address for a server on this port: through the notebook's own address on a SageMaker notebook
    instance or a Studio app, else the local one.
    """
    local = f"http://{host}:{port}/"
    proxied, where = _proxy_base(read_metadata() if metadata is None else metadata, port)
    if proxied:
        return PageLink(_with_token(proxied, token), _with_token(local, token), where, token)
    return PageLink(_with_token(local, token), _with_token(local, token), "this machine", token)

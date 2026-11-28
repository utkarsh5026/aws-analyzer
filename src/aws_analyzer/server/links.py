"""
The address to open the page at. The server listens on 127.0.0.1 only, which a browser on your laptop can't reach on
a SageMaker machine; JupyterLab's server proxy (jupyter-server-proxy, which SageMaker installs) forwards
<your Jupyter address>/proxy/<port>/ to it, behind the same sign-in as the notebook. SageMaker's metadata file says
which kind of machine this is, and so which address that is. A notebook instance's address isn't always its name:
newer ones carry a few letters after it (test2-kbmv.notebook...), which only DescribeNotebookInstance's Url says.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

METADATA_FILE = Path("/opt/ml/metadata/resource-metadata.json")
STUDIO_APP_PATHS = {"JupyterLab": "jupyterlab/default", "CodeEditor": "codeeditor/default",
                    "JupyterServer": "jupyter/default", "KernelGateway": "jupyter/default"}
LOOKUP_CONFIG = Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1})

FindAddress = Callable[[str, str], str]


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
    is_guess: bool = False

    @property
    def is_proxied(self) -> bool:
        return self.url != self.local

    @property
    def port(self) -> int:
        return urlsplit(self.local).port or 0


def _with_token(base: str, token: str) -> str:
    return f"{base}?token={token}" if token else base


def notebook_instance_address(name: str, region: str, *, profile: str | None = None) -> str:
    """
    The notebook instance's own address as SageMaker gives it (DescribeNotebookInstance's Url, such as
    'test2-kbmv.notebook.ap-south-1.sagemaker.aws'), or '' when it can't be read: no permission, no network.
    """
    try:
        client = boto3.Session(profile_name=profile).client("sagemaker", region_name=region, config=LOOKUP_CONFIG)
        found = client.describe_notebook_instance(NotebookInstanceName=name).get("Url") or ""
    except (BotoCoreError, ClientError):
        return ""
    return found.removeprefix("https://").removeprefix("http://").strip("/")


def _notebook_base(name: str, region: str, port: int, find_address: FindAddress | None) -> tuple[str, bool]:
    """
    (the proxied address of a notebook instance, whether it's a guess from its name because SageMaker didn't say).
    """
    found = find_address(name, region) if find_address else ""
    if found:
        return f"https://{found}/proxy/{port}/", False
    return f"https://{name}.notebook.{region}.sagemaker.aws/proxy/{port}/", True


def _proxy_base(metadata: dict, port: int, find_address: FindAddress | None) -> tuple[str, str, bool]:
    """
    (the proxied address without the token, what kind of machine says so, whether the address is a guess), or
    ('', '', False) when the metadata doesn't say enough to know it.
    """
    from ..sagemaker_env.analysis.machine import parse_metadata

    machine = parse_metadata(metadata)
    region = machine.region
    if machine.kind == "notebook instance" and machine.name and region:
        base, is_guess = _notebook_base(machine.name, region, port, find_address)
        return base, machine.label, is_guess
    app_path = STUDIO_APP_PATHS.get(machine.app_type, "")
    if machine.kind == "studio" and machine.domain_id and region and app_path:
        base = f"https://{machine.domain_id}.studio.{region}.sagemaker.aws/{app_path}/proxy/{port}/"
        return base, machine.label, False
    return "", "", False


def read_metadata(path: Path = METADATA_FILE) -> dict:
    """
    SageMaker's metadata file as a dict, or {} when this isn't SageMaker (or the file can't be read).
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def page_link(port: int, token: str, *, host: str = "127.0.0.1", metadata: dict | None = None,
              find_address: FindAddress | None = None) -> PageLink:
    """
    The page's address for a server on this port: through the notebook's own address on a SageMaker notebook
    instance or a Studio app, else the local one. `find_address(name, region)` looks up a notebook instance's
    address (`notebook_instance_address`); without it, or when it finds none, the address is guessed from the name.
    """
    local = f"http://{host}:{port}/"
    proxied, where, is_guess = _proxy_base(read_metadata() if metadata is None else metadata, port, find_address)
    if proxied:
        return PageLink(_with_token(proxied, token), _with_token(local, token), where, token, is_guess)
    return PageLink(_with_token(local, token), _with_token(local, token), "this machine", token)

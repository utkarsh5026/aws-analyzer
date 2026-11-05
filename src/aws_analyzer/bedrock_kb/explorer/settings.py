"""
The Settings tab's mixin: every setting in plain English, then as AWS returns them.
"""

from __future__ import annotations

from typing import Any

from ..._bedrock.blocks import _Json
from ..._kit.blocks import _Table, _Text, _Title
from ..._kit.fmt import _fmt_dt, human_money

from ..models import DataSourceInfo, KnowledgeBaseInfo
from ..analysis.settings import describe_chunking, describe_parsing, describe_vector_store
from ..analysis.cost import idle_cost_label, vector_store_monthly_cost
from ..view.cells import _DELETION, _KB_TYPES, _section
from .frame import _FIELD_ROLES


def _field_mapping(info: KnowledgeBaseInfo) -> dict[str, str]:
    """
    Where the vector store keeps a chunk's vector, text and metadata: the fieldMapping of its *Configuration.
    """
    detail = info.vector_store_detail
    config_key = next((key for key in detail if key.endswith("Configuration")), "")
    return (detail.get(config_key, {}) or {}).get("fieldMapping") or {}


def _source_table(ds: DataSourceInfo) -> _Table:
    rows = [
        ["ID", ds.id], ["Type", ds.source_type or "?"],
        ["Location", _section(ds, "data_source", ds.location or "-")],
        ["Chunking", _section(ds, "data_source", describe_chunking(ds.chunking))],
        ["Parsing", _section(ds, "data_source", describe_parsing(ds.parsing))],
        ["Transformation", ds.transformation or "none"],
        ["When deleted", _DELETION.get(ds.deletion_policy or "", ds.deletion_policy or "-")],
        ["Status", ds.status or "-"], ["Created", _fmt_dt(ds.created)], ["Last changed", _fmt_dt(ds.updated)],
    ]
    return _Table(["Setting", "Value"], rows, title=f"Data source {ds.name or ds.id}", max_rows=0)


def _as_aws_returns(info: KnowledgeBaseInfo) -> list[_Json]:
    blocks = [_Json(info.raw, "GetKnowledgeBase, as AWS returns it", open_depth=1, collapsed=True)] if info.raw else []
    blocks += [_Json(ds.raw, f"GetDataSource for {ds.name or ds.id}", open_depth=1, collapsed=True)
               for ds in info.data_sources if ds.raw]
    return blocks


def _terminal_commands(info: KnowledgeBaseInfo, region: str | None) -> _Text:
    commands = [f"aws bedrock-agent get-knowledge-base --knowledge-base-id {info.id} --region {region}"]
    commands += [f"aws bedrock-agent get-data-source --knowledge-base-id {info.id} --data-source-id {ds.id} "
                 f"--region {region}" for ds in info.data_sources]
    return _Text("\n".join(commands), title="The same from a terminal (read-only)", code=True)


class _SettingsTab:
    """
    The Settings tab of KBExplorer: the knowledge base's and each data source's settings, its tags, the JSON AWS
    returns, and the read-only commands that show the same.
    """

    def _kb_table(self, info: KnowledgeBaseInfo) -> _Table:
        cost = vector_store_monthly_cost(info, self.core.prices)
        idle = (f"about {human_money(cost)}/month ({self.ui._price_basis()})" if cost is not None
                else idle_cost_label(info, self.core.prices))
        rows = [
            ["Name", info.name or "-"], ["ID", info.id], ["ARN", info.arn or "-"], ["Status", info.status or "-"],
            ["Type", _KB_TYPES.get(info.kb_type, info.kb_type or "-")],
            ["Embedding model", info.embedding_model or "-"],
            ["Dimensions", f"{info.embedding_dims:,} numbers in each vector" if info.embedding_dims else "-"],
            ["Vector store", describe_vector_store(info.vector_store_detail)],
            ["Idle cost", idle],
            ["Service role", info.role_arn or "-"], ["Created", _fmt_dt(info.created)],
            ["Last changed", _fmt_dt(info.updated)],
        ]
        return _Table(["Setting", "Value"], rows, title="Knowledge base", max_rows=0)

    def _draw_settings(self) -> None:
        info = self.info
        if info is None:
            return
        blocks: list[Any] = [_Title(f"Settings of {info.name}", "in plain English, then as AWS returns them"),
                             self._kb_table(info)]
        fields = _field_mapping(info)
        if fields:
            blocks.append(_Table(["Part of a chunk", "Kept in", "Setting"],
                                 [[_FIELD_ROLES.get(k, k), v, k] for k, v in fields.items()],
                                 title="Where the vector store keeps each part of a chunk", max_rows=0))
        blocks += [_source_table(ds) for ds in info.data_sources]
        if info.tags:
            blocks.append(_Table(["Tag", "Value"], [[k, v] for k, v in sorted(info.tags.items())], title="Tags",
                                 max_rows=0))
        blocks += _as_aws_returns(info)
        blocks.append(_terminal_commands(info, self.core.region))
        self._draw("settings", self.settings_view, blocks)

    def _settings_line(self) -> str:
        if self.info is None:
            return ""
        return "Every setting in plain English, then as AWS returns them (folded, at the end)."

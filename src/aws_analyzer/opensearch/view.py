"""
OpenSearchView: the notebook UI, which renders what OpenSearchAnalyzer returns as cards and tables.
"""

from __future__ import annotations

import difflib
import json
import re
from collections import Counter
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from .._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title, _Tone
from .._kit.errors import _Hint, _error_code, _why
from .._kit.fmt import _as_int, _count, _fmt_dt, _plural, _total, human_age, human_money, human_size
from .._kit.render import _call
from .._kit.text import _clip
from .._kit.view import _BaseView, _friendly_errors

from .prices import EMBEDDING_MODELS, SERVERLESS_MIN_OCUS
from .helpers import _format_value, parse_location, values_at, vector_summary
from .models import Collection, Domain, Endpoint, IndexInfo, Overview, VectorField
from .analysis.parsing import _BEST, _SPACE_NAMES, _TEXT_TYPES, score_meaning, similarity_name, source_field, source_of
from .analysis.vectors import domain_knn_memory, index_vector_memory, vector_count, vector_memory
from .analysis.cost import _key_label, domain_monthly_cost, ocu_monthly_cost, serverless_minimum
from .analysis.queries import describe_filter, describe_vector_field, knn_query, query_python
from .analysis.findings import (
    _is_serverless, collection_findings, domain_findings, index_findings, knn_findings, search_findings,
    store_findings, vector_findings,
)
from .http import OpenSearchError
from .embeddings import _base_model
from .analyzer import OpenSearchAnalyzer
from .style import _render_html


_COLLECTION_KINDS = {"VECTORSEARCH": "vector search", "SEARCH": "search", "TIMESERIES": "time series"}

_NETWORK = {"public": "public", "vpc": "VPC endpoints", "aws services": "AWS services only"}

_FILTER_EXAMPLES = {  # mapping type -> a where= example for index_info's table of fields
    "keyword": "'<value>'",
    "constant_keyword": "'<value>'",
    "text": "('contains', 'some words')",
    "match_only_text": "('contains', 'some words')",
    "integer": "('>=', 10)",
    "long": "('>=', 10)",
    "short": "('>=', 10)",
    "byte": "('>=', 10)",
    "float": "('>=', 0.5)",
    "double": "('>=', 0.5)",
    "half_float": "('>=', 0.5)",
    "scaled_float": "('>=', 0.5)",
    "unsigned_long": "('>=', 10)",
    "date": "('>=', '2024-01-01')",
    "date_nanos": "('>=', '2024-01-01')",
    "boolean": "True",
    "ip": "'10.0.0.1'",
}


def _state_tone(status: str) -> str:
    """
    '' for a healthy status, 'bad' for a failed one, 'warn' for anything in between.
    """
    lowered = (status or "").lower()
    if lowered in ("active", "green", "open", ""):
        return ""
    return "bad" if lowered in ("failed", "red", "update_failed", "isolated") else "warn"


def _target_arg(store: Any) -> str:
    """
    What to pass to a command to name this domain, collection or endpoint.
    """
    return store.url if isinstance(store, Endpoint) else store.name


def _same(a: Any, b: Any) -> bool:
    return type(a) is type(b) and getattr(a, "name", None) == getattr(b, "name", None)


def _node_label(domain: Domain) -> str:
    """
    '3 × r6g.large.search' (+ masters and UltraWarm).
    """
    parts = [f"{domain.instance_count} × {domain.instance_type or '?'}"]
    if domain.master_type:
        parts.append(f"{domain.master_count} masters")
    if domain.warm_type:
        parts.append(f"{domain.warm_count} UltraWarm")
    return "\n".join(parts)


def _storage_label(domain: Domain) -> str:
    if not domain.volume_type:
        return "instance disks"
    return f"{domain.volume_gb:,} GB {domain.volume_type} × {domain.instance_count}"


def _shards_label(info: IndexInfo) -> str:
    """
    '3 primaries, 1 replica each' / '1 primary, no replicas'.
    """
    if not info.shards:
        return "-"
    primaries = f"{info.shards} primar{'y' if info.shards == 1 else 'ies'}"
    if info.replicas is None:
        return primaries
    return primaries + (f", {_plural(info.replicas, 'replica')} each" if info.replicas else ", no replicas")


def _vector_label(info: IndexInfo) -> str:
    """
    'embedding: 1,024 dims · faiss HNSW · cosine', one line per vector field.
    """
    return "\n".join(f"{vf.path}: {describe_vector_field(vf)}" for vf in info.vectors) or "-"


def _with_vector_cell(info: IndexInfo) -> Any:
    """
    '48,750 (97.5%)' for the first vector field, toned when some documents have none.
    """
    if not info.vectors:
        return "-"
    have, total = info.with_vector.get(info.vectors[0].path), info.top_docs
    if have is None or not total:
        return "?" if "counts" in info.errors else "-"
    text = f"{have:,} ({have / total:.0%})" if have < total else f"{have:,} (all)"
    return _Tone(text, "warn" if have < total else "")


def _method_settings(vf: VectorField) -> str:
    """
    'm=16 · ef_construction=512 · ef_search=100', defaults marked.
    """
    if vf.model_id:
        return f"trained model {vf.model_id}"
    if vf.algorithm == "ivf":
        return f"nlist={vf.nlist or 4}" + ("" if vf.nlist else " (default)")
    parts = []
    for name, value, default in (("m", vf.m, 16), ("ef_construction", vf.ef_construction, 100),
                                 ("ef_search", vf.ef_search, 100)):
        parts.append(f"{name}={value}" if value else f"{name}={default} (default)")
    return " · ".join(parts)


def _compression_label(vf: VectorField) -> str:
    factor = vf.compression_factor
    parts = []
    if vf.mode == "on_disk":
        parts.append("on disk")
    if vf.data_type != "float":
        parts.append(f"{vf.data_type} vectors")
    if vf.encoder:
        parts.append(vf.encoder)
    label = ", ".join(parts) or "none (32-bit floats)"
    return label + (f", {factor:g}x smaller" if factor > 1 else "")


def _hit_text(source: dict[str, Any], text_field: str | None, width: int = 300) -> str:
    values = values_at(source, text_field) if text_field else []
    value = values[0] if values else ""
    return _clip(" ".join(str(value).split()), width) if value not in (None, "") else ""


def _flat(doc: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """
    A document with its objects spread into dotted fields ('metadata.page'); lists stay values.
    """
    flat: dict[str, Any] = {}
    for key, value in doc.items():
        if isinstance(value, dict) and value:
            flat.update(_flat(value, f"{prefix}{key}."))
        else:
            flat[f"{prefix}{key}"] = value
    return flat


def _metadata_columns(docs: list[dict[str, Any]], skip: set[str], limit: int) -> list[str]:
    """
    The fields most documents have (objects spread into dotted fields), besides the text, source and vectors.
    """
    counts: Counter = Counter()
    for doc in docs:
        counts.update(k for k in _flat(doc) if k not in skip and not k.startswith("_"))
    return [name for name, _ in counts.most_common(limit or None)]


def _filter_rows(info: IndexInfo) -> list[list[str]]:
    """
    Fields a where= filter can use, with an example for each.
    """
    vectors = {vf.path for vf in info.vectors}
    rows = []
    for path, kind in info.fields.items():
        parent = path.rsplit(".", 1)[0] if "." in path else ""
        if path in vectors or kind in ("object", "nested", "knn_vector", "") or info.fields.get(parent) in _TEXT_TYPES:
            continue  # sub-fields such as title.keyword are used through their parent
        if path in info.unindexed:
            continue
        example = _FILTER_EXAMPLES.get(kind)
        if example is None:
            continue
        if kind in ("text", "match_only_text") and info.fields.get(f"{path}.keyword") == "keyword":
            example, kind = "'<exact value>'", f"{kind} (+ keyword)"
        rows.append([path, kind, f"where={{{path!r}: {example}}}"])
    return rows


class OpenSearchView(_BaseView):
    """
    Notebook UI over OpenSearchAnalyzer. Each method renders a report and returns nothing; for the underlying
    data call the matching method on `view.core` (an OpenSearchAnalyzer).

    index: the domain or collection, and optionally the index ('vectors-prod/docs'), that commands use when they
    aren't given one; use() changes it. Without it, commands use the only domain or collection in the region and its
    only vector index, or say how to pick one.
    mode: 'auto' (HTML inside Jupyter, text elsewhere), 'html' or 'text'.
    max_rows: default cap for long tables (set to 0 for no cap).
    max_columns: most document fields shown side by side next to the text (0 = all).
    progress: 'auto' (a tqdm bar while long commands run, when tqdm is installed; else a line with the count,
    rate and time left), 'plain' (always that line) or 'off'.
    """

    _render_html = staticmethod(_render_html)  # its look: the stylesheet, root class and badge
    _GROUPS = {  # help() lists the commands in these groups, in this order
        "🗂️ Domains and collections": ("overview", "use"),
        "📇 Indexes": ("indexes", "index_info"),
        "🧭 Vectors": ("sample", "search"),
        "❓ Help": ("help",),
    }
    _START = (
        ("overview()", "every domain and collection: size, cost and warnings"),
        ("indexes('name')", "its indexes, which hold vectors, and the memory they need"),
        ("index_info('name/index')", "one vector index in plain English"),
    )

    def __init__(
        self,
        core: OpenSearchAnalyzer | None = None,
        *,
        index: str | None = None,
        mode: str = "auto",
        max_rows: int = 50,
        max_columns: int = 6,
        progress: str = "auto",
    ):
        super().__init__(mode=mode, max_rows=max_rows, progress=progress)
        self.core: OpenSearchAnalyzer = core or OpenSearchAnalyzer()
        self.max_columns = max_columns
        self.target: str | None = None  # what use() picked: a domain or collection name, or a URL
        self.index: str | None = None
        if index:
            self.target, self.index = parse_location(index)

    # ------------------------------------------------------------------ plumbing

    def _explain(self, code: str, message: str) -> str:
        """
        An AWS API error message plus what to do about it.
        """
        lowered = message.lower()
        text = message.rstrip() + ("" if message.rstrip().endswith((".", "!", "?")) else ".")
        if code in ("ResourceNotFoundException", "ResourceNotFound"):
            return f"{text} overview() lists the domains and collections in {self.core.region}."
        if code == "AccessDeniedException" and "model" in lowered and "not authorized to perform" not in lowered:
            return (f"{text} Enable the model in the Bedrock console (Model access), or pass model= with one you can "
                    "use, or embed=your_function.")
        if code in ("AccessDeniedException", "AccessDenied", "UnauthorizedOperation") or "not authorized" in lowered:
            return f"{text} README lists the read-only IAM permissions each command needs."
        if code == "ValidationException" and "model" in lowered:
            return f"{text} Pass model= with a Bedrock embedding model ID (EMBEDDING_MODELS lists the ones this file knows)."
        if code in ("ThrottlingException", "TooManyRequestsException"):
            return f"{text} AWS throttled the call: wait a few seconds and retry."
        return message

    def _explain_http(self, exc: OpenSearchError) -> str:
        """
        A failed REST request in words, with what to change.
        """
        store, status = exc.store, exc.status
        where = store.label
        reason = exc.reason.rstrip(".")
        if status is None:
            if isinstance(store, Domain) and store.vpc:
                return (f"Couldn't reach {where} ({exc.kind}). It only answers inside VPC {store.vpc}: run this "
                        "notebook in that VPC, and allow HTTPS (port 443) from it in the domain's security group.")
            if isinstance(store, Collection) and store.network in ("vpc", "aws services"):
                return (f"Couldn't reach {where} ({exc.kind}): its network policy {store.network_policy} only lets "
                        f"{_NETWORK[store.network]} in. Run the notebook where the policy allows, or allow it there.")
            return (f"Couldn't reach {store.url} ({exc.kind}: {reason}). Check the address, and that this notebook "
                    "can reach it (VPC, security groups, proxy).")
        if status == 401:
            return (f"{where} asked for a user name and password ({reason}): with fine-grained access control's "
                    "internal users, pass OpenSearchView(OpenSearchAnalyzer(auth=('user', 'password'))).")
        if status == 403:
            if _is_serverless(store):
                index = f"index/{store.name}/*" if isinstance(store, Collection) else "index/<collection>/*"
                return (f"{where} refused the request ({reason}). Two policies must allow it: an IAM policy with "
                        f"aoss:APIAccessAll, and a data access policy that gives your role aoss:DescribeIndex and "
                        f"aoss:ReadDocument on {index} (Serverless console, Data access policies).")
            if isinstance(store, Domain) or getattr(store, "service", None) == "es":
                arn = f"{store.arn}/*" if isinstance(store, Domain) and store.arn else "the domain's ARN/*"
                fgac = (" Fine-grained access control is on, so also map your role to an OpenSearch role that can "
                        "read, such as the built-in readall_and_monitor (OpenSearch Dashboards: Security, Roles)."
                        if isinstance(store, Domain) and store.fine_grained else "")
                return (f"{where} refused the request ({reason}). Your role needs es:ESHttpGet and es:ESHttpPost on "
                        f"{arn}, allowed by the domain's access policy too.{fgac}")
            return f"{where} refused the request ({reason}). If it uses basic authentication, pass auth=('user', 'password')."
        if status == 404 and exc.kind == "index_not_found_exception":
            missing = re.search(r"\[([^\]]+)\]", reason)
            name = missing.group(1) if missing else exc.path.strip("/").split("/")[0]
            try:
                names = [i.name for i in self.core.cat_indices(store) if not i.name.startswith(".")]
            except (OpenSearchError, ValueError, BotoCoreError):
                names = []
            close = difflib.get_close_matches(name, names, n=3)
            hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
            return f"No index {name!r} in {where}.{hint} indexes({_target_arg(store)!r}) lists them."
        if status == 429:
            return f"{where} is busy ({reason}): wait a few seconds and retry."
        kind = "" if exc.kind.startswith("HTTP ") else f" {exc.kind}"
        return f"{where} answered {status}{kind}: {reason} ({exc.path})."

    def _extra_note(self, exc: Exception, command: str) -> _Note | None:
        """
        OpenSearch's own errors as notes: a failed request in words (_explain_http), and an answer it can't read.
        """
        if isinstance(exc, OpenSearchError):
            return _Note(f"{self._explain_http(exc)}  [{command}]", "warn")
        if isinstance(exc, (KeyError, AttributeError, IndexError)):  # an answer shaped unlike any this file has seen
            return _Note(
                f"An answer from OpenSearch wasn't shaped the way this file expects ({type(exc).__name__}: {exc}), "
                f"so the report stopped. Please report it with the command you ran.  [{command}]", "warn")
        return None

    def _store(self, target: str | None = None) -> Domain | Collection | Endpoint:
        """
        The domain, collection or endpoint a command works on: `target`, else use()'s, else the only one.
        """
        if target is not None:
            return self.core.resolve(target)
        if self.target is not None:
            return self.core.resolve(self.target)
        names: list[str] = []
        problems: list[str] = []
        for lister, permission in (
            (self.core.domain_names, "es:ListDomainNames"),
            (lambda: [s.get("name", "") for s in self.core.collection_summaries()], "aoss:ListCollections"),
        ):
            try:
                names += lister()
            except ClientError as exc:
                problems.append(_why(_error_code(exc), permission))
            except BotoCoreError as exc:  # e.g. Serverless isn't offered in this region
                problems.append(type(exc).__name__)
        if len(names) == 1:
            return self.core.resolve(names[0])
        region = self.core.region
        if not names:
            couldnt = f" (couldn't list them: {'; '.join(problems)})" if problems else ""
            raise _Hint(
                f"There are no OpenSearch domains or Serverless collections in {region}{couldnt}. They're regional: "
                "try OpenSearchView(OpenSearchAnalyzer(region='us-west-2')). An OpenSearch you run yourself works by "
                "URL: indexes('https://localhost:9200')."
            )
        listed = sorted(names, key=str.lower)
        raise _Hint(
            f"Which domain or collection? There are {len(names)} in {region}: {', '.join(listed[:15])}"
            f"{', …' if len(listed) > 15 else ''}. Pass one, like indexes('{listed[0]}'), or pick one for every "
            f"command with use('{listed[0]}')."
        )

    def _maybe_store(self, name: str) -> Domain | Collection | Endpoint | None:
        try:
            return self.core.resolve(name)
        except ValueError:
            return None

    def _index_ref(self, index: str | None, command: str) -> tuple[Any, str]:
        """
        (store, index name) for a command: 'name/index', a bare index name (in use()'s domain or collection, or
        the only one), a domain or collection on its own (its only vector index), or use()'s index.
        """
        if index is not None:
            head, tail = parse_location(index)
            if tail is not None:
                return self.core.resolve(head), tail
            if re.match(r"^(https?://|arn:)", head, re.IGNORECASE):
                store = self.core.resolve(head)
                return store, self._only_vector_index(store, command)
            if self.target is not None:
                return self.core.resolve(self.target), head
            store = self._maybe_store(head)
            if store is not None:
                return store, self._only_vector_index(store, command)
            return self._store(), head
        store = self._store()
        if self.index and self.target is not None:
            return store, self.index
        return store, self._only_vector_index(store, command)

    def _only_vector_index(self, store: Any, command: str) -> str:
        report = self.core.indexes(store, details=False)
        vectors = sorted(i.name for i in report.vector_indexes)
        if len(vectors) == 1:
            return vectors[0]
        arg = _target_arg(store)
        if not vectors:
            names = sorted(i.name for i in report.indexes)
            listed = f" (its indexes: {', '.join(names[:10])}{', …' if len(names) > 10 else ''})" if names else ""
            raise _Hint(f"{store.label} has no vector indexes{listed}. indexes({arg!r}) shows them all.")
        example = (f"search('your question', index='{arg}/{vectors[0]}')" if command == "search"
                   else f"{command}('{arg}/{vectors[0]}')")
        raise _Hint(
            f"Which index? {store.label} has {len(vectors)} vector indexes: {', '.join(vectors[:15])}"
            f"{', …' if len(vectors) > 15 else ''}. Pass one, like {example}, or pick one for every command with "
            f"use('{arg}/{vectors[0]}')."
        )

    def _is_default(self, store: Any, index: str | None = None) -> bool:
        """
        Whether use() picked this store (and index), so next steps can leave the argument out.
        """
        if self.target is None or (index is not None and self.index != index):
            return False
        try:
            return _same(self.core.resolve(self.target), store)
        except (ValueError, ClientError, BotoCoreError):
            return False

    def _ref_args(self, store: Any, index: str) -> tuple[str, ...]:
        """
        ('vectors-prod/docs',) for a next step's call, or () when it's use()'s index.
        """
        return () if self._is_default(store, index) else (f"{_target_arg(store)}/{index}",)

    def _ref_kwargs(self, store: Any, index: str) -> dict[str, str]:
        return {} if self._is_default(store, index) else {"index": f"{_target_arg(store)}/{index}"}

    # ---------------------------------------------------- domains and collections

    @_friendly_errors
    def use(self, where: str) -> None:
        """
        Sets the domain or collection, and with 'name/index' the index, that later commands use when you don't
        pass one: use('vectors-prod/docs') and then index_info(), sample() or search('a question').
        """
        head, tail = parse_location(where)
        store = self.core.resolve(head)
        if tail is not None:
            tail = self.core.index(store, tail, counts=False).name  # checks it exists
        self.target, self.index = _target_arg(store), tail
        if tail:
            text = f"Using index {tail} in {store.label} from now on."
            steps = [
                ("index_info()", "the index in plain English"),
                ("sample()", "a few documents, and how healthy their vectors are"),
                (_call("search", "a question your documents answer"), "the documents nearest to it"),
            ]
        else:
            text = f"Using {store.label} from now on."
            steps = [("indexes()", "its indexes, and which hold vectors")]
        self._show([_Note(text, "ok"), _Next(steps)])

    @_friendly_errors
    def overview(self, match: str | None = None, *, metrics: bool = True) -> None:
        """
        Every OpenSearch Service domain and Serverless collection in the region: what it runs on, where it
        answers, estimated monthly cost, and warnings. match='prod-*' keeps matching names; metrics=False skips
        CloudWatch (the OCUs Serverless used).
        """
        with self._progress("Checking domains", unit="domains") as tick:
            ov = self.core.overview(match=match, metrics=metrics, progress=tick)
        prices = self.core.prices
        domains = sorted(ov.domains, key=lambda d: d.name)
        collections = sorted(ov.collections, key=lambda c: c.name)
        warnings: list[list[str]] = []
        domain_rows: list[list[Any]] = []
        domain_total = 0.0
        unreadable: list[str] = []
        for d in domains:
            if "describe" in d.errors:
                unreadable.append(f"{d.name} ({_why(d.errors['describe'], 'es:DescribeDomains')})")
                domain_rows.append([d.name, "?", "?", "-", "-", "-", "-", "-", "-"])
                continue
            cost = _total(domain_monthly_cost(d, prices))
            domain_total += cost or 0.0
            found = [m for level, m in domain_findings(d, prices) if level == "warn"]
            warnings += [[d.name, m] for m in found]
            domain_rows.append([
                d.name,
                f"{d.engine} {d.version}".strip(),
                _Tone(d.status or "?", _state_tone(d.status)),
                _node_label(d),
                _storage_label(d),
                f"VPC {d.vpc}" if d.vpc else "public",
                human_size(domain_knn_memory(d)),
                human_money(cost) if cost is not None else "?",
                _Tone(str(len(found)), "warn" if found else ""),
            ])
        collection_rows: list[list[Any]] = []
        for c in collections:
            found = [m for level, m in collection_findings(c) if level == "warn"]
            warnings += [[c.name, m] for m in found]
            collection_rows.append([
                c.name,
                c.id,
                _COLLECTION_KINDS.get(c.kind, c.kind.lower() or "-"),
                _Tone(c.status or "?", _state_tone(c.status)),
                "on" if c.standby else "off (dev)",
                _NETWORK.get(c.network or "", "?" if "network" in c.errors else "-"),
                _key_label(c.kms_key),
                human_age(c.created),
                _Tone(str(len(found)), "warn" if found else ""),
            ])
        groups = serverless_minimum(collections)
        minimum = sum(ocus for _, ocus, _ in groups)
        used = (ov.ocus["indexing"] + ov.ocus["search"]) if ov.ocus else None
        billed = max(used or 0.0, minimum)  # Serverless never bills less than its minimum
        serverless_total = ocu_monthly_cost(billed, prices) if collections else 0.0
        if collections and used is not None:
            basis = f"Serverless at the OCUs it used in the last {ov.hours}h (at least its idle minimum)"
        elif collections:
            basis = "Serverless at its idle minimum"
        else:
            basis = ""
        title = f"OpenSearch in {ov.region} ({_plural(len(domains), 'domain')}, {_plural(len(collections), 'collection')})"
        blocks: list[Any] = [
            _Title(
                title,
                (f"names matching {match!r} · " if match else "")
                + "cost: domains at their instances and storage"
                + (f", {basis}" if basis else "")
                + f", at {self._price_basis()}",
            ),
            _Cards([
                ("Domains", f"{len(domains):,}"),
                ("Serverless collections", f"{len(collections):,}"),
                ("Vector collections", f"{sum(c.vector for c in collections):,}"),
                ("Est. cost / month", human_money(domain_total + serverless_total)),
                ("With warnings", f"{len({name for name, _ in warnings}):,}", "warn" if warnings else "ok"),
            ]),
        ]
        for section, permission in (("domains", "es:ListDomainNames"), ("collections", "aoss:ListCollections")):
            if section in ov.errors:
                blocks.append(_Note(f"Couldn't list the {section} ({_why(ov.errors[section], permission)}).", "warn"))
        if not domains and not collections and not ov.errors:
            where = f"matching {match!r} " if match else ""
            blocks.append(_Note(
                f"No OpenSearch domains or Serverless collections {where}in {ov.region}. They're regional: try "
                "OpenSearchView(OpenSearchAnalyzer(region='us-west-2')). An OpenSearch you run yourself works by URL: "
                "indexes('https://localhost:9200')."
            ))
            self._show(blocks)
            return
        if unreadable:
            blocks.append(_Note(f"Couldn't describe {', '.join(unreadable)}.", "warn"))
        if domains:
            blocks.append(_Table(
                ["Domain", "Engine", "Status", "Data nodes", "Storage", "Endpoint", "k-NN memory", "Est. $/month",
                 "Warnings"],
                domain_rows,
                title="Domains (k-NN memory: what the data nodes have for vector graphs, about a quarter of their RAM)",
                max_rows=0,
            ))
        if collections:
            blocks.append(_Table(
                ["Collection", "ID", "Type", "Status", "Standby replicas", "Network", "Encryption", "Created",
                 "Warnings"],
                collection_rows,
                title="Serverless collections",
                max_rows=0,
            ))
            blocks += self._serverless_blocks(ov, groups, minimum, used)
        if warnings:
            blocks.append(_Table(
                ["Name", "Warning"],
                warnings,
                prose_cols=(1,),
                title="Warnings (indexes(name) shows every finding for one domain or collection)",
                max_rows=0,
            ))
        readable = [d.name for d in domains if "describe" not in d.errors] + [c.name for c in collections]
        if readable:
            flagged = Counter(name for name, _ in warnings).most_common(1)
            vector = [c.name for c in collections if c.vector]
            look = flagged[0][0] if flagged else (vector or readable)[0]
            blocks.append(_Next([
                (_call("indexes", look), "its indexes, which hold vectors, and their memory"),
                (_call("use", look), "make it the one later commands use"),
            ]))
        self._show(blocks)

    def _serverless_blocks(
        self, ov: Overview, groups: list[tuple[str, float, list[str]]], minimum: float, used: float | None
    ) -> list[Any]:
        prices = self.core.prices
        rows = [
            [f"Idle minimum: {label}", ", ".join(names), f"{ocus:g}", human_money(ocu_monthly_cost(ocus, prices))]
            for label, ocus, names in groups
        ]
        if used is not None and ov.ocus:
            rows.append([
                f"Used: average over the last {ov.hours}h (CloudWatch)",
                "all",
                f"{used:.1f} ({ov.ocus['indexing']:.1f} indexing + {ov.ocus['search']:.1f} search)",
                human_money(ocu_monthly_cost(used, prices)),
            ])
        if ov.max_indexing_ocus is not None or ov.max_search_ocus is not None:
            top = (ov.max_indexing_ocus or 0) + (ov.max_search_ocus or 0)
            rows.append([
                "Account limit: the most it scales to",
                "all",
                f"{top:g} ({ov.max_indexing_ocus or 0:g} indexing + {ov.max_search_ocus or 0:g} search)",
                human_money(ocu_monthly_cost(top, prices)),
            ])
        blocks: list[Any] = [_Table(
            ["Capacity", "Collections", "OCUs", "Est. $/month"],
            rows,
            title=f"Serverless capacity (OpenSearch Compute Units, ${prices['ocu_hour']:g} per OCU-hour; storage "
            f"${prices['serverless_storage']:g} per GB-month is extra)",
            max_rows=0,
        )]
        if minimum:
            blocks.append(_Note(
                f"Serverless bills at least {minimum:g} OCUs ({human_money(ocu_monthly_cost(minimum, prices))}/month) "
                f"for these collections even with no traffic: {SERVERLESS_MIN_OCUS[True]:g} for each group of "
                f"collections that share an encryption key, kind and standby setting "
                f"({SERVERLESS_MIN_OCUS[False]:g} without standby replicas). Delete collections you no longer use in "
                "the console to stop paying for their group."
            ))
        for section, permission, what in (
            ("capacity", "aoss:GetAccountSettings", "the account's capacity limits"),
            ("usage", "cloudwatch:GetMetricData", "the OCUs used (CloudWatch)"),
        ):
            if section in ov.errors:
                blocks.append(_Note(f"Couldn't read {what} ({_why(ov.errors[section], permission)})."))
        if used is None and "usage" not in ov.errors and ov.ocus is None and minimum:
            blocks.append(_Note(f"CloudWatch has no OCU numbers for the last {ov.hours}h, so the cost is the minimum."))
        return blocks

    # ------------------------------------------------------------------ indexes

    @_friendly_errors
    def indexes(self, target: str | None = None, *, hidden: bool = False) -> None:
        """
        Every index in a domain or collection: documents, size, shards, vector fields (dimensions, engine,
        similarity), documents missing a vector, the memory the vectors need against what the nodes have, and
        warnings. hidden=True adds system indexes (names starting with a dot).
        """
        store = self._store(target)
        with self._progress("Counting vectors", unit="indexes") as tick:
            report = self.core.indexes(store, hidden=hidden, progress=tick)
        knn_memory = domain_knn_memory(store) if isinstance(store, Domain) else None
        version = store.version if isinstance(store, Domain) else None
        data_nodes = store.instance_count if isinstance(store, Domain) else (
            report.health.data_nodes if report.health else None)
        serverless = _is_serverless(store)
        ordered = sorted(report.indexes, key=lambda i: (not i.vectors, i.name))
        rows: list[list[Any]] = []
        warnings: list[list[str]] = []
        for info in ordered:
            found = [m for level, m in index_findings(info, store, version=version, data_nodes=data_nodes)
                     if level == "warn"]
            warnings += [[info.name, m] for m in found]
            memory = index_vector_memory(info)
            rows.append([
                info.name,
                _Tone(info.health, _state_tone(info.health)) if info.health else "-",
                _count(info.documents),
                human_size(info.size_bytes),
                "managed" if serverless else f"{info.shards} × {info.copies}" if info.shards else "-",
                _vector_label(info),
                _with_vector_cell(info),
                human_size(memory) if memory is not None else "-",
                _Tone(str(len(found)), "warn" if found else ""),
            ])
        need = sum(index_vector_memory(i) or 0 for i in report.vector_indexes)
        native = sum(index_vector_memory(i, native=True) or 0 for i in report.vector_indexes)
        cards: list[tuple[str, ...]] = [
            ("Indexes", f"{len(report.indexes):,}"),
            ("Vector indexes", f"{len(report.vector_indexes):,}"),
            ("Documents", _count(sum(i.documents or 0 for i in report.indexes))),
            ("Size", human_size(sum(i.size_bytes or 0 for i in report.indexes))),
            ("Est. vector memory", human_size(need) if need else "-"),
        ]
        if knn_memory:
            cards.append(("k-NN memory", human_size(knn_memory), "warn" if native > knn_memory else ""))
        if report.knn:
            cards.append(("Graph memory in use", human_size(report.knn.memory_bytes)))
        if report.health:
            cards.append(("Cluster health", report.health.status or "?", _state_tone(report.health.status)))
        subtitle = (
            f"{store.url or 'no endpoint'} · "
            + ("" if serverless else "shards: primaries × copies · ")
            + "vector memory: the HNSW / IVF graphs of all copies, estimated by OpenSearch's sizing rule"
        )
        blocks: list[Any] = [_Title(f"Indexes in {store.label}", subtitle), _Cards(cards)]
        notes = {
            "_cat/indices": "document counts and sizes",
            "_mapping": "some mappings",
            "_settings": "settings (shards, replicas, index.knn)",
            "segments": "segment counts",
            "health": "cluster health",
            "k-NN stats": "the k-NN plugin's memory numbers",
        }
        missing = [f"{what} ({report.errors[key]})" for key, what in notes.items() if key in report.errors]
        if missing:
            blocks.append(_Note("Couldn't read " + ", ".join(missing) + "."))
        own = (domain_findings(store, self.core.prices) if isinstance(store, Domain)
               else collection_findings(store) if isinstance(store, Collection) else [])
        blocks.append(_Findings(own + store_findings(report, knn_memory)))
        if not report.indexes:
            blocks.append(_Note(f"No indexes in {store.label} yet{'' if hidden else ' (hidden=True adds system ones)'}."))
            self._show(blocks)
            return
        if not report.vector_indexes:
            blocks.append(_Note("None of these indexes has a vector (knn_vector) field."))
        blocks.append(_Table(
            ["Index", "Health", "Documents", "Size", "Shards", "Vector fields", "With a vector", "Est. vector memory",
             "Warnings"],
            rows,
            max_rows=0,
        ))
        if warnings:
            blocks.append(_Table(
                ["Index", "Warning"],
                warnings,
                prose_cols=(1,),
                title="Warnings (index_info(name) shows every finding for one index)",
                max_rows=0,
            ))
        if report.vector_indexes:
            counts = Counter(name for name, _ in warnings)
            size = {i.name: i.documents or 0 for i in report.indexes}
            flagged = max(counts, key=lambda n: (counts[n], size.get(n, 0))) if counts else None
            look = flagged or max(report.vector_indexes, key=lambda i: i.documents or 0).name
            ref = f"{_target_arg(store)}/{look}"
            blocks.append(_Next([
                (_call("index_info", ref), "why it's flagged, and what to change" if flagged
                 else "its vector fields in plain English, and the query to copy"),
                (_call("sample", ref), "a few documents, and how healthy the vectors are"),
            ]))
        self._show(blocks)

    @_friendly_errors
    def index_info(self, index: str | None = None) -> None:
        """
        One index in plain English: its vector fields (dimensions, engine, algorithm and settings, similarity and
        what a score means), documents without a vector, the memory the vectors need against what the nodes have,
        the fields you can filter on, findings, and the k-NN query to copy. index='vectors-prod/docs', or 'docs'
        after use('vectors-prod').
        """
        store, name = self._index_ref(index, "index_info")
        info = self.core.index(store, name)
        serverless = _is_serverless(store)
        blocks: list[Any] = [_Title(f"Index {info.name}", f"{store.label} · {store.url or 'no endpoint'}")]
        knn = None
        if info.vectors and not serverless:
            try:
                knn = self.core.knn_stats(store)
            except OpenSearchError as exc:
                blocks.append(_Note(f"Couldn't read the k-NN plugin's memory numbers ({exc.status} {exc.kind})."))
        knn_memory = domain_knn_memory(store) if isinstance(store, Domain) else None
        version = store.version if isinstance(store, Domain) else None
        data_nodes = store.instance_count if isinstance(store, Domain) else None
        need = index_vector_memory(info)
        native = index_vector_memory(info, native=True)
        cards: list[tuple[str, ...]] = [("Documents", _count(info.documents))]
        if info.vectors:
            cell = _with_vector_cell(info)
            cards.append(("With a vector", str(cell), getattr(cell, "tone", "")))
        cards += [
            ("Size", human_size(info.size_bytes)),
            ("Shards", "managed by Serverless" if serverless else _shards_label(info)),
        ]
        if info.health:
            cards.append(("Health", info.health, _state_tone(info.health)))
        if info.vectors:
            vf = info.vectors[0]
            cards += [
                ("Vector fields", f"{len(info.vectors):,}"),
                ("Dimensions", f"{vf.dimension:,}" if vf.dimension else "?"),
                ("Engine", f"{vf.engine or 'default'} {vf.algorithm.upper()}" if not vf.model_id else "trained model"),
                ("Similarity", _SPACE_NAMES.get(vf.space, vf.space)),
                ("Est. vector memory", human_size(need) if need is not None else "?",
                 "warn" if knn_memory and native and native > knn_memory else ""),
            ]
            if knn_memory:
                cards.append(("k-NN memory (all nodes)", human_size(knn_memory)))
            if knn and info.name in knn.by_index:
                cards.append(("Graphs loaded now", human_size(knn.by_index[info.name])))
        if info.created:
            cards.append(("Created", _fmt_dt(info.created)))
        blocks.append(_Cards(cards))
        found = index_findings(info, store, knn_memory=knn_memory, stats=knn, version=version, data_nodes=data_nodes)
        found += knn_findings(knn)
        blocks.append(_Findings(found, empty="No issues found by these checks."))
        if not info.vectors:
            blocks.append(_Note(
                f"{info.name} has no vector (knn_vector) fields, so it can't be searched by similarity. "
                f"indexes({_target_arg(store)!r}) shows which indexes have them."
            ))
        else:
            blocks += self._vector_blocks(store, info)
        rows = _filter_rows(info)
        if rows:
            blocks.append(_Table(
                ["Field", "Type", "Filter with (search(..., where=...))"],
                rows,
                title=f"Fields you can filter on ({len(rows):,})",
                code_cols=(2,),
                collapsed=len(rows) > 12,
            ))
        if info.mapping:
            blocks.append(_Text(json.dumps(info.mapping, indent=2, ensure_ascii=False), title="Mapping (JSON)",
                                collapsed=True))
        if info.settings:
            blocks.append(_Table(["Setting", "Value"], [[k, str(v)] for k, v in sorted(info.settings.items())],
                                 title="Settings", collapsed=True))
        if not info.vectors:
            blocks.append(_Next([(_call("indexes", _target_arg(store)), "which of its indexes hold vectors")]))
        else:
            blocks.append(_Next([
                (_call("sample", *self._ref_args(store, info.name)), "a few documents, and how healthy the vectors are"),
                (_call("search", "a question your documents answer", **self._ref_kwargs(store, info.name)),
                 "the documents nearest to it"),
            ]))
        self._show(blocks)

    def _vector_blocks(self, store: Any, info: IndexInfo) -> list[Any]:
        rows = []
        for vf in info.vectors:
            count = info.with_vector.get(vf.path)
            vectors = vector_count(info, vf)
            memory = vector_memory(vf, vectors or 0) if vectors is not None else None
            rows.append([
                vf.path + (f"\n(nested in {vf.nested})" if vf.nested else ""),
                f"{vf.dimension:,}" if vf.dimension else "?",
                f"{vf.engine or 'default engine'} {vf.algorithm.upper()}" if not vf.model_id else "trained model",
                _SPACE_NAMES.get(vf.space, vf.space) + ("" if vf.space_type else " (default)"),
                _method_settings(vf),
                _compression_label(vf),
                _count(count),
                human_size(memory * info.copies) if memory is not None else "?",
            ])
        blocks: list[Any] = [_Table(
            ["Vector field", "Dimensions", "Engine", "Similarity", "Graph settings", "Compression", "Documents",
             "Est. memory (all copies)"],
            rows,
            title="Vector fields",
            max_rows=0,
        )]
        for space in dict.fromkeys(vf.space for vf in info.vectors):
            first = next(vf for vf in info.vectors if vf.space == space)
            blocks.append(_Note(f"How to read scores ({_SPACE_NAMES.get(space, space)}): {score_meaning(first)}."))
        if info.text_field:
            blocks.append(_Note(f"Each document's text is in '{info.text_field}'; search() and sample() show it."))
        vf = info.vectors[0]
        example = knn_query(vf, [0.0], 10, exclude=[v.path for v in info.vectors])
        blocks.append(_Text(
            f"query_vector = ...  # {f'{vf.dimension:,}' if vf.dimension else 'the'} numbers from the model that "
            "embedded the documents\n"
            + query_python(example, info.name),
            title="The k-NN query, to copy",
            code=True,
        ))
        return blocks

    # ------------------------------------------------------------------ vectors

    @_friendly_errors
    def sample(self, index: str | None = None, n: int = 10, *, check: int = 200, where: Any = None) -> None:
        """
        A few random documents (text, where each came from, its fields, and its vector as dimensions and length),
        and a check of `check` documents' vectors: lengths, all-zero vectors and exact repeats. where= picks the
        documents, e.g. where={'embedding': ('missing',)} for those without a vector.
        """
        store, name = self._index_ref(index, "sample")
        n = _as_int(n, "n")
        result = self.core.sample(store, name, n, check=check, where=where)
        info = result.info or IndexInfo(name)
        docs = result.docs
        shown = docs[: max(0, n)]
        how = "random documents" if result.random else "the first documents (random sampling isn't available here)"
        blocks: list[Any] = [_Title(
            f"Sample of {result.index}",
            f"{store.label} · {how}" + (f" · matching {describe_filter(where)}" if where else ""),
        )]
        cards: list[tuple[str, ...]] = [
            ("Documents" + (" matching" if where else ""), _count(result.total)),
            ("Looked at", f"{len(docs):,}"),
        ]
        vf = info.vectors[0] if info.vectors else None
        check_result = result.checks.get(vf.path) if vf else None
        findings: list[tuple[str, str]] = []
        for field_vf in info.vectors:
            if field_vf.path in result.checks:
                findings += vector_findings(result.checks[field_vf.path], field_vf)
        if vf and check_result and check_result.vectors:
            c = check_result
            length = (
                "1.00 (unit length)" if c.unit_length
                else f"{c.norm_min:.2f} – {c.norm_max:.2f}" if c.norm_min is not None and c.norm_max is not None
                else "?"
            )
            cards += [
                ("Dimensions", ", ".join(f"{d:,}" for d in c.dims) or "?"),
                ("Vector length", length),
                ("All-zero vectors", f"{c.zeros:,}", "warn" if c.zeros else ""),
                ("Repeats", f"{c.repeats:,}", "warn" if any(lvl == "warn" and "repeat" in m for lvl, m in findings)
                 else ""),
            ]
        blocks.append(_Cards(cards))
        if not docs:
            blocks.append(_Note("No documents" + (f" match {describe_filter(where)}." if where else " in this index.")))
            self._show(blocks)
            return
        blocks.append(_Findings(findings, empty="The sampled vectors look healthy: one size, no zeros, no repeats."
                                if info.vectors else ""))
        text_field = info.text_field
        vector_paths = [v.path for v in info.vectors]
        source_fields = {source_field(doc)[0] for doc in shown} - {""}
        skip = {text_field or "", *vector_paths, "AMAZON_BEDROCK_METADATA", *source_fields}
        extra = _metadata_columns(shown, skip, self.max_columns)
        has_source = bool(source_fields)
        headers = ["ID"] + ([f"Text ({text_field})"] if text_field else []) + (["Source"] if has_source else [])
        headers += [f"Vector ({vector_paths[0]})"] if vector_paths else []
        headers += extra
        rows = []
        for doc in shown:
            row: list[Any] = [str(doc.get("_id", ""))]
            if text_field:
                row.append(_hit_text(doc, text_field))
            if has_source:
                row.append(_clip(source_of(doc), 120))
            if vector_paths:
                vectors = values_at(doc, vector_paths[0])
                row.append(vector_summary(vectors[0]) if vectors else "(none)")
            flat = _flat(doc)
            row += [_format_value(flat.get(column), 60) for column in extra]
            rows.append(row)
        blocks.append(_Table(headers, rows, title=f"Documents ({len(shown):,} of the {len(docs):,} looked at)",
                             max_rows=0))
        if len(info.vectors) > 1 or (check_result and check_result.vectors):
            vector_rows = []
            for path, c in result.checks.items():
                vector_rows.append([
                    path,
                    f"{c.vectors:,}",
                    ", ".join(f"{d:,}" for d in c.dims) or "-",
                    f"{c.norm_min:.3f} – {c.norm_max:.3f} (mean {c.norm_mean:.3f})" if c.norm_min is not None else "-",
                    f"{c.zeros:,}",
                    f"{c.repeats:,}",
                    f"{c.missing:,}",
                ])
            blocks.append(_Table(
                ["Vector field", "Vectors checked", "Dimensions", "Length (min – max)", "All zeros", "Repeats",
                 "Documents without"],
                vector_rows,
                title=f"Vectors in the {len(docs):,} documents looked at",
                max_rows=0,
            ))
        first = str(shown[0].get("_id", "")) if shown else ""
        steps = []
        if first and info.vectors:
            steps.append((_call("search", like=first, **self._ref_kwargs(store, result.index)),
                          "the documents nearest to the first one"))
        steps.append((_call("index_info", *self._ref_args(store, result.index)), "its settings and memory in plain English"))
        blocks.append(_Next(steps))
        self._show(blocks)

    @_friendly_errors
    def search(
        self,
        query: str | None = None,
        *,
        index: str | None = None,
        vector: Any = None,
        like: str | None = None,
        k: int = 10,
        where: Any = None,
        field: str | None = None,
        model: str | None = None,
        embed: Callable[[str], Any] | None = None,
    ) -> None:
        """
        The k documents nearest a question: rank, score and the similarity it stands for (cosine or distance),
        text, source and fields. The question is embedded with a Bedrock model (Titan Text Embeddings V2 for 256,
        512 or 1,024 dimensions; model= picks another) or with embed=your_function; vector=[...] searches with your
        own vector, and like='doc-id' finds the documents nearest an existing one. where= filters, e.g.
        where={'lang': 'en', 'year': ('>=', 2024)}.
        """
        store, name = self._index_ref(index, "search")
        result = self.core.search(store, name, query, vector=vector, like=like, k=k, field=field, where=where,
                                  model=model, embed=embed)
        info = result.info or IndexInfo(name)
        vf = info.vector(result.field) if info.vectors else VectorField(result.field)
        if result.like is not None:
            title = f"Nearest to document {result.like}"
        elif result.text is not None:
            title = f"Nearest to “{_clip(result.text, 70)}”"
        else:
            title = "Nearest to your vector"
        similarity = similarity_name(vf)
        best_label = _BEST.get(similarity, f"Smallest {similarity}")
        sub = f"{result.index} in {store.label} · field '{result.field}' · {_SPACE_NAMES.get(vf.space, vf.space)} · k={result.k}"
        if where:
            sub += f" · where {describe_filter(where)}"
        best = result.hits[0] if result.hits else None
        cards: list[tuple[str, ...]] = [
            ("Results", f"{len(result.hits):,}"),
            ("Best score", f"{best.score:.3f}" if best else "-"),
            (best_label, f"{best.similarity:.3f}" if best and best.similarity is not None else "-"),
            ("Search time", f"{result.took_ms:,} ms" if result.took_ms is not None else "-"),
            ("Total time", f"{result.seconds:.1f}s"),
        ]
        emb = result.embedding
        if emb:
            label = EMBEDDING_MODELS.get(_base_model(emb.model), ("", 0, (), 0.0, emb.model))[4]
            cost = human_money(emb.cost) if emb.cost is not None else "?"
            cards.append(("Embedded with", label))
            cards.append(("Embedding cost", f"{cost}" + (f" ({emb.tokens:,} tokens)" if emb.tokens else "")))
        blocks: list[Any] = [_Title(title, sub), _Cards(cards)]
        if emb and model is None:
            blocks.append(_Note(
                f"The question was embedded with {cards[-2][1]}, picked because '{result.field}' has "
                f"{vf.dimension:,} dimensions. Vectors from different models aren't comparable: if the documents "
                "were embedded another way, pass model= or embed=your_function."
            ))
        blocks.append(_Findings(search_findings(result, vf, result.hit_norms)))
        if not result.hits:
            blocks.append(_Note(
                "No results." + (f" Nothing near the query matches {describe_filter(where)}." if where else "")
            ))
        else:
            text_field = result.text_field
            sources = [h.source for h in result.hits]
            source_fields = {source_field(s)[0] for s in sources} - {""}
            skip = {text_field or "", "AMAZON_BEDROCK_METADATA", *source_fields}
            extra = _metadata_columns(sources, skip, self.max_columns)
            has_source = bool(source_fields)
            headers = ["#", "Score", similarity, "ID"] + (["Text"] if text_field else [])
            headers += (["Source"] if has_source else []) + extra
            rows = []
            for hit in result.hits:
                row: list[Any] = [
                    str(hit.rank),
                    f"{hit.score:.4f}",
                    f"{hit.similarity:.4f}" if hit.similarity is not None else "-",
                    hit.id,
                ]
                if text_field:
                    row.append(_hit_text(hit.source, text_field))
                if has_source:
                    row.append(_clip(source_of(hit.source), 120))
                flat = _flat(hit.source)
                row += [_format_value(flat.get(column), 60) for column in extra]
                rows.append(row)
            blocks.append(_Table(headers, rows, max_rows=0))
            blocks.append(_Note(f"How to read scores: {score_meaning(vf)}."))
            if result.like is not None and not result.excluded_self and not where:
                blocks.append(_Note(f"Document {result.like} itself wasn't among the nearest: other documents hold "
                                    "the same vector (repeats), or the search is approximate."))
        blocks.append(_Text(
            "query_vector = ...  # the vector searched with\n" + query_python(result.query, result.index),
            title="This search as Python (opensearch-py)",
            code=True,
            collapsed=True,
        ))
        steps = []
        if result.hits:
            steps.append((_call("search", like=result.hits[0].id, **self._ref_kwargs(store, result.index)),
                          "the documents nearest to the top result"))
        steps.append((_call("sample", *self._ref_args(store, result.index)), "a few documents, and how healthy the vectors are"))
        blocks.append(_Next(steps))
        self._show(blocks)

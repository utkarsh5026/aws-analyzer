"""
OpenSearchAnalyzer: talks to AWS and to each domain's or collection's REST API, and returns the
dataclasses in models. Never prints.
"""

from __future__ import annotations

import base64
import difflib
import fnmatch
import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import BotoCoreError, ClientError, NoRegionError
from botocore.httpsession import URLLib3Session
from botocore.utils import get_environ_proxies

from .._kit.analyzer import _BaseAnalyzer
from .._kit.errors import _error_code, _error_name, _why
from .._kit.fmt import _as_int, _utcnow
from .._kit.text import _clip

from .prices import EMBEDDING_MODELS, OPENSEARCH_PRICES, _EMBEDDING_ALIASES
from .helpers import _ARN_RE, _int, endpoint_service, parse_location, values_at, vector_norm
from .models import (
    ClusterHealth, Collection, Domain, Embedding, Endpoint, Hit, IndexInfo, KnnStats, Overview, Sample, SearchResult,
    StoreReport, VectorField,
)
from .analysis.parsing import (
    guess_text_field, index_from_cat, network_access, parse_collection, parse_domain, parse_knn_stats, parse_mapping,
    read_settings, unindexed_fields,
)
from .analysis.vectors import check_vectors, score_to_similarity
from .analysis.queries import _check_where, build_filter, knn_query
from .analysis.findings import _is_serverless
from .http import _READ_POSTS, OpenSearchError, _error_details, _path, _store_url
from .embeddings import _as_vector, _base_model, _embedding_from, _embedding_request, _embedding_style


def _exists(vf: VectorField) -> dict[str, Any]:
    """
    Query matching documents that have a vector in `vf` (through its nested field when it's inside one).
    """
    query = {"exists": {"field": vf.path}}
    return {"nested": {"path": vf.nested, "query": query}} if vf.nested else query


def _drop(source: dict[str, Any], path: str) -> None:
    """
    Remove a (possibly dotted) field from a document, so vectors don't fill the table of results.
    """
    if path in source:
        source.pop(path)
        return
    head, _, rest = path.partition(".")
    if rest and isinstance(source.get(head), dict):
        _drop(source[head], rest)


class OpenSearchAnalyzer(_BaseAnalyzer):
    """
    Pure-logic OpenSearch analysis: every method returns data; nothing is printed or written.

    Domains and Serverless collections are found through the AWS APIs. Their indexes are read through each one's own
    REST endpoint, with requests signed with your AWS credentials (SigV4), or with a user name and password when you
    pass auth=('user', 'password') (fine-grained access control's internal users, or an OpenSearch you run yourself).
    Only GET requests are sent, plus POSTs to _search and _count; anything else is refused before it's sent.

    Methods that read an index take `target` (a domain or collection name, a collection ID, an ARN or an endpoint URL)
    and `index`. `prices` overrides OPENSEARCH_PRICES. `client` is the OpenSearch Service client and `clients` other
    boto3 clients by service name ('opensearchserverless', 'cloudwatch', 'sts', 'bedrock-runtime'). `http` replaces
    the HTTP transport: a function (method, url, body, headers) -> (status, body bytes), e.g. for tests.
    """

    _LIST_PRICES = OPENSEARCH_PRICES

    def __init__(
        self,
        session: Any = None,
        *,
        region: str | None = None,
        profile: str | None = None,
        client: Any = None,
        clients: dict[str, Any] | None = None,
        prices: dict[str, float] | None = None,
        auth: tuple[str, str] | None = None,
        http: Callable[[str, str, bytes | None, dict[str, str]], tuple[int, bytes]] | None = None,
        timeout: float | tuple[float, float] = (5, 60),
    ):
        super().__init__(session, region=region, profile=profile, prices=prices)
        self._clients: dict[str, Any] = dict(clients or {})
        if client is not None:
            self._clients["opensearch"] = client
        self.auth = auth
        self.timeout = timeout
        self.max_workers = 8
        self._transport = http or self._send
        self._http_session: Any = None
        self._domains: dict[str, Domain] = {}
        self._collections: dict[str, Collection] = {}  # by name and by ID

    # ------------------------------------------------------------------ clients

    def _service(self, name: str) -> Any:
        """
        A boto3 client, made on first use (or the one passed in client= / clients=).
        """
        if name not in self._clients:
            makers = {  # named one by one so the project's read-only check can see which services are used
                "opensearch": lambda: self.session.client("opensearch", config=self._config),
                "opensearchserverless": lambda: self.session.client("opensearchserverless", config=self._config),
                "cloudwatch": lambda: self.session.client("cloudwatch", region_name=self.region),
                "sts": lambda: self.session.client("sts", region_name=self.region),
                "bedrock-runtime": lambda: self.session.client(
                    "bedrock-runtime", region_name=self.region, config=self._config
                ),
            }
            try:
                self._clients[name] = makers[name]()
            except NoRegionError:
                raise ValueError(
                    "No AWS region is set, and OpenSearch domains and collections are regional. Pass one: "
                    "OpenSearchView(OpenSearchAnalyzer(region='us-east-1')), or set AWS_DEFAULT_REGION."
                ) from None
        return self._clients[name]

    @property
    def client(self) -> Any:
        """
        The OpenSearch Service client (domains).
        """
        return self._service("opensearch")

    # ---------------------------------------------------------- domains and collections

    def domain_names(self) -> list[str]:
        """
        Every OpenSearch Service domain in the region (OpenSearch and Elasticsearch).
        """
        return sorted(d["DomainName"] for d in self.client.list_domain_names().get("DomainNames", []))

    def list_domains(
        self, names: list[str] | None = None, *, progress: Callable[..., None] | None = None
    ) -> list[Domain]:
        """
        Every domain's configuration (DescribeDomains, five domains a call). A domain that can't be described
        comes back with errors['describe'] set.
        """
        names = self.domain_names() if names is None else names
        chunks = [names[i : i + 5] for i in range(0, len(names), 5)]

        def describe(chunk: list[str]) -> list[Domain]:
            try:
                found = self.client.describe_domains(DomainNames=chunk).get("DomainStatusList", [])
            except (ClientError, BotoCoreError) as exc:
                return [Domain(name, errors={"describe": _error_name(exc)}) for name in chunk]
            domains = [parse_domain(status) for status in found]
            seen = {d.name for d in domains}
            return domains + [Domain(n, errors={"describe": "not returned"}) for n in chunk if n not in seen]

        domains: list[Domain] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for part in pool.map(describe, chunks):
                domains += part
                if progress:
                    progress(len(domains), len(names))
        for d in domains:
            if "describe" not in d.errors:
                self._domains[d.name] = d
        return domains

    def domain(self, name: str, *, refresh: bool = False) -> Domain:
        """
        One domain's configuration (DescribeDomain, cached).
        """
        if refresh or name not in self._domains:
            self._domains[name] = parse_domain(self.client.describe_domain(DomainName=name)["DomainStatus"])
        return self._domains[name]

    def _serverless_client(self) -> Any:
        return self._service("opensearchserverless")

    def collection_summaries(self) -> list[dict[str, Any]]:
        """
        Every Serverless collection in the region: ListCollections' id, name, status and ARN.
        """
        found: list[dict[str, Any]] = []
        token = None
        while True:
            resp = self._serverless_client().list_collections(**({"nextToken": token} if token else {}))
            found += resp.get("collectionSummaries", [])
            token = resp.get("nextToken")
            if not token:
                return found

    def list_collections(self, *, network: bool = True) -> list[Collection]:
        """
        Every Serverless collection with its endpoint and settings (BatchGetCollection, 100 a call), and with
        network=True which network policy covers it. Parts that can't be read are in each collection's errors.
        """
        summaries = self.collection_summaries()
        ids = [s["id"] for s in summaries if s.get("id")]
        details: dict[str, dict[str, Any]] = {}
        failed: dict[str, str] = {}
        for start in range(0, len(ids), 100):
            chunk = ids[start : start + 100]
            try:
                resp = self._serverless_client().batch_get_collection(ids=chunk)
            except (ClientError, BotoCoreError) as exc:
                failed.update(dict.fromkeys(chunk, _error_name(exc)))
                continue
            details.update({d["id"]: d for d in resp.get("collectionDetails", []) if d.get("id")})
        collections = []
        for summary in summaries:
            c = parse_collection(details.get(summary.get("id", ""), summary))
            if summary.get("id") in failed:
                c.errors["describe"] = failed[summary["id"]]
            collections.append(c)
        if network and collections:
            self._add_network(collections)
        for c in collections:
            self._collections[c.name] = self._collections[c.id] = c
        return collections

    def collection(self, name: str, *, refresh: bool = False) -> Collection:
        """
        One collection by name or ID (BatchGetCollection, cached), with its network policy.
        """
        if not refresh and name in self._collections:
            return self._collections[name]
        details = self._serverless_client().batch_get_collection(names=[name]).get("collectionDetails") or []
        if not details and re.fullmatch(r"[a-z0-9]{3,40}", name):
            details = self._serverless_client().batch_get_collection(ids=[name]).get("collectionDetails") or []
        if not details:
            raise ValueError(f"No Serverless collection named {name!r} in {self.region}")
        found = parse_collection(details[0])
        self._add_network([found])
        self._collections[found.name] = self._collections[found.id] = found
        return found

    def network_policies(self) -> list[tuple[str, Any]]:
        """
        Every Serverless network policy: (name, policy document).
        """
        names: list[str] = []
        token = None
        while True:
            resp = self._serverless_client().list_security_policies(type="network", **({"nextToken": token} if token else {}))
            names += [p["name"] for p in resp.get("securityPolicySummaries", [])]
            token = resp.get("nextToken")
            if not token:
                break
        return [
            (name, self._serverless_client().get_security_policy(name=name, type="network")["securityPolicyDetail"].get("policy"))
            for name in names
        ]

    def _add_network(self, collections: list[Collection]) -> None:
        try:
            policies = self.network_policies()
        except (ClientError, BotoCoreError) as exc:
            for c in collections:
                c.errors["network"] = _error_name(exc)
            return
        for c in collections:
            c.network, c.network_policy, c.vpc_endpoints = network_access(policies, c.name)

    def serverless_capacity(self) -> tuple[float | None, float | None]:
        """
        The account's Serverless capacity limits: (most indexing OCUs, most search OCUs) it may scale to.
        """
        detail = self._serverless_client().get_account_settings().get("accountSettingsDetail") or {}
        limits = detail.get("capacityLimits") or {}
        return limits.get("maxIndexingCapacityInOCU"), limits.get("maxSearchCapacityInOCU")

    def serverless_ocus(self, hours: int = 24) -> dict[str, float] | None:
        """
        Average indexing and search OCUs the account's Serverless collections used over the last `hours`
        (CloudWatch AWS/AOSS IndexingOCU and SearchOCU) -> {'indexing': ..., 'search': ...}; None without data.
        Collections in a collection group report their OCUs separately and aren't included.
        """
        account = self._service("sts").get_caller_identity()["Account"]
        names = ("IndexingOCU", "SearchOCU")
        queries = [
            {
                "Id": f"m{i}",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/AOSS",
                        "MetricName": name,
                        "Dimensions": [{"Name": "ClientId", "Value": account}],
                    },
                    "Period": 3600,
                    "Stat": "Average",
                },
            }
            for i, name in enumerate(names)
        ]
        now = _utcnow()
        values: dict[int, list[float]] = {0: [], 1: []}
        for page in (
            self._service("cloudwatch")
            .get_paginator("get_metric_data")
            .paginate(MetricDataQueries=queries, StartTime=now - timedelta(hours=hours), EndTime=now)
        ):
            for series in page["MetricDataResults"]:
                values[int(series["Id"][1:])] += series.get("Values", [])
        if not values[0] and not values[1]:
            return None
        return {
            "indexing": sum(values[0]) / len(values[0]) if values[0] else 0.0,
            "search": sum(values[1]) / len(values[1]) if values[1] else 0.0,
        }

    def overview(
        self, *, match: str | None = None, metrics: bool = True, progress: Callable[..., None] | None = None
    ) -> Overview:
        """
        Every domain and Serverless collection in the region (match='prod-*' keeps matching names), the account's
        Serverless capacity limits, and with metrics=True the OCUs it used in the last 24 hours (CloudWatch).
        Lists that can't be read are recorded in `errors`.
        """
        result = Overview(region=self.region)

        def wanted(name: str) -> bool:
            return match is None or fnmatch.fnmatchcase(name, match)

        try:
            names = [n for n in self.domain_names() if wanted(n)]
            result.domains = self.list_domains(names, progress=progress)
        except (ClientError, BotoCoreError) as exc:
            result.errors["domains"] = _error_name(exc)
        try:
            result.collections = [c for c in self.list_collections() if wanted(c.name)]
        except (ClientError, BotoCoreError) as exc:
            result.errors["collections"] = _error_name(exc)
        if result.collections:
            try:
                result.max_indexing_ocus, result.max_search_ocus = self.serverless_capacity()
            except (ClientError, BotoCoreError) as exc:
                result.errors["capacity"] = _error_name(exc)
            if metrics:
                try:
                    result.ocus = self.serverless_ocus(result.hours)
                except (ClientError, BotoCoreError) as exc:
                    result.errors["usage"] = _error_name(exc)
        return result

    def resolve(self, target: Any) -> Domain | Collection | Endpoint:
        """
        A domain, Serverless collection or other OpenSearch from its name, collection ID, ARN or endpoint URL ('name/index'
        works too; the index is ignored). A Domain, Collection or Endpoint passes through. Raises ValueError, with the
        closest names, when there's no such domain or collection.
        """
        if isinstance(target, (Domain, Collection, Endpoint)):
            return target
        head, _ = parse_location(target)
        if re.match(r"^https?://", head, re.IGNORECASE):
            return self._endpoint(head)
        arn = _ARN_RE.match(head)
        if arn:
            return self.domain(arn.group(5)) if arn.group(4) == "domain" else self.collection(arn.group(5))
        head = head.lower()  # domain and collection names (and collection IDs) are lowercase only
        if head in self._domains:
            return self._domains[head]
        if head in self._collections:
            return self._collections[head]
        problems: list[str] = []
        try:
            return self.domain(head)
        except ClientError as exc:
            if _error_code(exc) not in ("ResourceNotFoundException", "ValidationException"):
                problems.append(f"domains: {_why(_error_code(exc), 'es:DescribeDomain')}")
        except BotoCoreError as exc:
            problems.append(f"domains: {type(exc).__name__}")
        try:
            return self.collection(head)
        except ValueError:
            pass
        except ClientError as exc:
            problems.append(f"collections: {_why(_error_code(exc), 'aoss:BatchGetCollection')}")
        except BotoCoreError as exc:
            problems.append(f"collections: {type(exc).__name__}")
        raise ValueError(self._not_found(head, problems))

    def _not_found(self, name: str, problems: list[str]) -> str:
        names: list[str] = []
        for lister in (self.domain_names, lambda: [s.get("name", "") for s in self.collection_summaries()]):
            try:
                names += lister()
            except (ClientError, BotoCoreError):
                pass
        close = [n for n in names if n.lower() == name.lower()] or difflib.get_close_matches(name, names, n=3)
        hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
        couldnt = f" (couldn't check {'; '.join(problems)})" if problems else ""
        return (
            f"No domain or Serverless collection named {name!r} in {self.region}{couldnt}.{hint} overview() lists "
            "them; they're regional. An OpenSearch you run yourself works by URL, like indexes('https://host:9200')."
        )

    def _endpoint(self, url: str) -> Domain | Collection | Endpoint:
        """
        A URL -> the domain or collection it belongs to when this analyzer knows it, else an Endpoint.
        """
        host = (urlsplit(url).hostname or "").lower()
        for d in self._domains.values():
            if d.endpoint and d.endpoint.lower() == host:
                return d
        for c in self._collections.values():
            if c.endpoint and (urlsplit(c.endpoint).hostname or "").lower() == host:
                return c
        service, region = endpoint_service(url)
        try:
            if service == "aoss":  # a collection's endpoint starts with its ID
                return self.collection(host.split(".")[0])
            if service == "es":  # a domain's endpoint: name it when it's one of this region's domains
                for d in self.list_domains():
                    if d.endpoint and d.endpoint.lower() == host:
                        return d
        except (ClientError, BotoCoreError, ValueError):
            pass  # not one this account can see: still usable by URL
        if service and region is None:
            region = self.session.region_name
        return Endpoint(url.rstrip("/"), service, region)

    # ------------------------------------------------------------- REST requests

    def _send(self, method: str, url: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, bytes]:
        """
        Send one request with botocore's HTTP client, through the proxies and CA bundle boto3 would use.
        """
        if self._http_session is None:
            core = getattr(self.session, "_session", None)
            bundle = core.get_config_variable("ca_bundle") if hasattr(core, "get_config_variable") else None
            self._http_session = URLLib3Session(
                verify=bundle or os.environ.get("REQUESTS_CA_BUNDLE") or True,
                proxies=get_environ_proxies(url),
                timeout=self.timeout,
                max_pool_connections=max(10, self.max_workers),
            )
        response = self._http_session.send(AWSRequest(method=method, url=url, data=body, headers=headers).prepare())
        return response.status_code, response.content

    def _headers(self, store: Any, method: str, url: str, body: bytes | None) -> dict[str, str]:
        """
        Request headers: SigV4 for AWS endpoints (the 'es' or 'aoss' service), Basic auth with auth=.
        """
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.auth is not None:
            token = base64.b64encode(f"{self.auth[0]}:{self.auth[1]}".encode()).decode()
            return {**headers, "Authorization": f"Basic {token}"}
        if isinstance(store, Domain):
            service, region = "es", self.region
        elif isinstance(store, Collection):
            service, region = "aoss", self.region
        else:
            service, region = store.service, store.region
        if not service:
            return headers
        credentials = self.session.get_credentials()
        if credentials is None:
            raise ValueError(
                f"No AWS credentials to sign requests to {store.label}. On SageMaker the notebook's role provides "
                "them; elsewhere run aws configure, or pass OpenSearchAnalyzer(profile='...')."
            )
        request = AWSRequest(method=method, url=url, data=body or b"", headers=headers)
        request.headers["X-Amz-Content-SHA256"] = hashlib.sha256(body or b"").hexdigest()  # Serverless requires it
        SigV4Auth(credentials.get_frozen_credentials(), service, region or self.region).add_auth(request)
        return dict(request.headers.items())

    def request(self, target: Any, path: str, body: Any = None, *, params: dict[str, Any] | None = None) -> Any:
        """
        GET `path` from a domain, collection or URL -> the parsed JSON answer. With `body`, POSTs it, which only
        _search and _count accept: anything that could change data is refused before it's sent.
        """
        store = self.resolve(target)
        base = _store_url(store)
        if not base:
            state = getattr(store, "status", "") or "not ready"
            raise ValueError(f"{store.label} has no endpoint yet ({state}).")
        clean = "/" + path.lstrip("/")
        method, data = "GET", None
        if body is not None:
            if not _READ_POSTS.search(clean):
                raise ValueError(f"OpenSearchAnalyzer only reads: it sends a body only to _search and _count, not {clean}")
            method, data = "POST", json.dumps(body, ensure_ascii=False).encode("utf-8")
        url = base.rstrip("/") + clean + ("?" + urlencode(params) if params else "")
        headers = self._headers(store, method, url, data)
        try:
            status, content = self._transport(method, url, data, headers)
        except BotoCoreError as exc:
            raise OpenSearchError(store, clean, None, type(exc).__name__, str(exc)) from exc
        if status >= 400:
            kind, reason = _error_details(status, content)
            raise OpenSearchError(store, clean, status, kind, reason)
        try:
            return json.loads(content) if content else None
        except ValueError:
            text = _clip((content or b"").decode("utf-8", "replace"), 200)
            raise OpenSearchError(store, clean, status, "not JSON", text) from None

    def _search(self, store: Any, index: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request(store, f"{_path(index)}/_search", body) or {}

    def _count(self, store: Any, index: str, query: dict[str, Any]) -> int:
        return int((self.request(store, f"{_path(index)}/_count", {"query": query}) or {}).get("count", 0))

    # ------------------------------------------------------------------- indexes

    def cat_indices(self, target: Any) -> list[IndexInfo]:
        """
        Every index with its health, documents, size, shards and replicas (GET _cat/indices).
        """
        rows = self.request(target, "_cat/indices", params={"format": "json", "bytes": "b"})
        return [index_from_cat(row) for row in rows or [] if isinstance(row, dict)]

    def _with_mapping(self, info: IndexInfo, mapping: dict[str, Any]) -> IndexInfo:
        info.mapping = mapping.get("mappings", mapping) if isinstance(mapping, dict) else {}
        info.fields, info.vectors, info.source_excludes = parse_mapping(mapping or {})
        info.unindexed = unindexed_fields(info.mapping)
        info.text_field = guess_text_field({k: v for k, v in info.fields.items() if k not in info.unindexed})
        return info

    def _counts(self, store: Any, info: IndexInfo) -> None:
        """
        Top-level documents, and how many have each vector field (one _count each).
        """
        try:
            info.top_docs = self._count(store, info.name, {"match_all": {}})
            for vf in info.vectors:
                info.with_vector[vf.path] = self._count(store, info.name, _exists(vf))
        except OpenSearchError as exc:
            info.errors["counts"] = f"{exc.status or ''} {exc.kind}".strip()

    def index(self, target: Any, index: str, *, counts: bool = True) -> IndexInfo:
        """
        Everything about one index: its counts and size (_cat/indices), mapping (fields and vector fields) and
        settings, and with counts=True how many documents have each vector field and (outside Serverless) its
        segments. Parts that can't be read are recorded in `errors`; an index that doesn't exist raises
        OpenSearchError (404).
        """
        store = self.resolve(target)
        info = IndexInfo(index)
        try:
            rows = self.request(store, f"_cat/indices/{_path(index)}", params={"format": "json", "bytes": "b"})
            if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict):
                info = index_from_cat(rows[0])
        except OpenSearchError as exc:
            if exc.status in (None, 401, 403) or exc.kind == "index_not_found_exception":
                raise
            info.errors["_cat/indices"] = f"{exc.status} {exc.kind}"
        mappings = self.request(store, f"{_path(index)}/_mapping") or {}
        real = info.name if info.name in mappings else next(iter(mappings), index)
        info.name = real
        self._with_mapping(info, mappings.get(real) or {})
        try:
            settings = self.request(store, f"{_path(real)}/_settings", params={"flat_settings": "true"}) or {}
            read_settings(info, (settings.get(real) or next(iter(settings.values()), {})).get("settings", {}))
        except OpenSearchError as exc:
            info.errors["settings"] = f"{exc.status or ''} {exc.kind}".strip()
        if counts and info.vectors:
            self._counts(store, info)
            if not _is_serverless(store):
                try:
                    stats = self.request(store, f"{_path(real)}/_stats/segments") or {}
                    total = (stats.get("indices", {}).get(real) or stats.get("_all") or {}).get("total", {})
                    info.segments = _int(total.get("segments", {}).get("count"))
                except OpenSearchError as exc:
                    info.errors["segments"] = f"{exc.status or ''} {exc.kind}".strip()
        return info

    def indexes(
        self,
        target: Any,
        *,
        hidden: bool = False,
        details: bool = True,
        progress: Callable[..., None] | None = None,
    ) -> StoreReport:
        """
        Every index in a domain, collection or cluster with its mapping and settings, and with details=True the
        documents that have each vector field, plus (outside Serverless) segment counts, cluster health and the k-NN
        plugin's memory use. hidden=True includes system indexes (names starting with a dot). Parts that can't be
        read are recorded in `errors`.
        """
        store = self.resolve(target)
        report = StoreReport(store)
        infos: dict[str, IndexInfo] = {}
        listed = False
        try:
            infos, listed = {i.name: i for i in self.cat_indices(store)}, True
        except OpenSearchError as exc:
            if exc.status in (None, 401, 403):
                raise
            report.errors["_cat/indices"] = f"{exc.status} {exc.kind}"
        if listed and not infos:
            return report
        mappings = self._bulk(store, "_mapping", list(infos), {}, report)
        for name in mappings:
            infos.setdefault(name, IndexInfo(name))
        names = sorted(n for n in infos if hidden or not n.startswith("."))
        settings = self._bulk(store, "_settings", names, {"flat_settings": "true"}, report)
        for name in names:
            info = self._with_mapping(infos[name], mappings.get(name) or {})
            if name in settings:
                read_settings(info, (settings[name] or {}).get("settings", {}))
            elif "_settings" in report.errors:
                info.errors.setdefault("settings", report.errors["_settings"])
            report.indexes.append(info)
        if not details:
            return report
        vector_indexes = report.vector_indexes
        done = [0]

        def count(info: IndexInfo) -> None:
            self._counts(store, info)
            done[0] += 1

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = [pool.submit(count, info) for info in vector_indexes]
            for future in futures:
                future.result()
                if progress:
                    progress(done[0], len(vector_indexes))
        if not _is_serverless(store):
            self._cluster_details(store, report)
        return report

    def _bulk(
        self, store: Any, what: str, names: list[str], params: dict[str, str], report: StoreReport
    ) -> dict[str, Any]:
        """
        GET /_mapping or /_settings for every index in one call; when that's refused (Serverless), one call per
        index. -> index name -> its answer.
        """
        try:
            found = self.request(store, what, params=params or None) or {}
            if isinstance(found, dict):
                return found
        except OpenSearchError as exc:
            if exc.status in (None, 401) or not names:
                raise
        out: dict[str, Any] = {}

        def one(name: str) -> None:
            try:
                answer = self.request(store, f"{_path(name)}/{what}", params=params or None) or {}
                out[name] = answer.get(name) or next(iter(answer.values()), {})
            except OpenSearchError as exc:
                report.errors.setdefault(what, f"{exc.status or ''} {exc.kind}".strip())

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            list(pool.map(one, names))
        return out

    def _cluster_details(self, store: Any, report: StoreReport) -> None:
        """
        Segment counts, cluster health and k-NN memory, each optional.
        """
        try:
            stats = self.request(store, "_stats/segments") or {}
            for info in report.indexes:
                total = (stats.get("indices", {}).get(info.name) or {}).get("total", {})
                info.segments = _int(total.get("segments", {}).get("count"))
        except OpenSearchError as exc:
            report.errors["segments"] = f"{exc.status or ''} {exc.kind}".strip()
        try:
            report.health = self.cluster_health(store)
        except OpenSearchError as exc:
            report.errors["health"] = f"{exc.status or ''} {exc.kind}".strip()
        if report.vector_indexes:
            try:
                report.knn = self.knn_stats(store)
            except OpenSearchError as exc:
                report.errors["k-NN stats"] = f"{exc.status or ''} {exc.kind}".strip()

    def cluster_health(self, target: Any) -> ClusterHealth:
        """
        GET _cluster/health (not on Serverless).
        """
        resp = self.request(target, "_cluster/health") or {}
        return ClusterHealth(
            status=str(resp.get("status", "")),
            nodes=int(resp.get("number_of_nodes") or 0),
            data_nodes=int(resp.get("number_of_data_nodes") or 0),
            active_shards=int(resp.get("active_shards") or 0),
            unassigned_shards=int(resp.get("unassigned_shards") or 0),
        )

    def knn_stats(self, target: Any) -> KnnStats:
        """
        The k-NN plugin's memory and cache numbers (GET _plugins/_knn/stats; _opendistro on Elasticsearch).
        """
        try:
            resp = self.request(target, "_plugins/_knn/stats")
        except OpenSearchError as exc:
            if exc.status not in (400, 404):
                raise
            resp = self.request(target, "_opendistro/_knn/stats")
        return parse_knn_stats(resp or {})

    # --------------------------------------------------------------- documents

    def sample(
        self, target: Any, index: str, n: int = 10, *, check: int = 200, where: Any = None
    ) -> Sample:
        """
        Random documents from an index: max(n, check) of them (at most 1,000), with each vector field's vectors
        checked (dimensions, lengths, zeros, repeats). where= narrows the sample (see build_filter). Falls back to
        the first documents when the endpoint can't sample at random.
        """
        store = self.resolve(target)
        info = self.index(store, index, counts=False)
        size = max(1, min(1000, max(_as_int(n, "n"), _as_int(check, "check"))))
        _check_where(where, info)
        query = build_filter(where, info.fields) or {"match_all": {}}
        body: dict[str, Any] = {
            "size": size,
            "query": {"function_score": {"query": query, "random_score": {}, "boost_mode": "replace"}},
            "track_total_hits": True,
        }
        random = True
        try:
            resp = self._search(store, info.name, body)
        except OpenSearchError as exc:
            if exc.status != 400:
                raise
            random, body = False, {"size": size, "query": query, "track_total_hits": True}
            resp = self._search(store, info.name, body)
        hits = (resp.get("hits") or {}).get("hits") or []
        docs = [{"_id": h.get("_id"), **(h.get("_source") or {})} for h in hits]
        checks = {}
        for vf in info.vectors:
            vectors = [v for doc in docs for v in values_at(doc, vf.path)]
            result = check_vectors(vectors, vf.path)
            result.docs = len(docs)
            result.missing = sum(1 for doc in docs if not values_at(doc, vf.path))
            checks[vf.path] = result
        total = (resp.get("hits") or {}).get("total")
        sample = Sample(
            target=store.name,
            index=info.name,
            docs=docs,
            checks=checks,
            random=random,
            total=total.get("value") if isinstance(total, dict) else _int(total),
            took_ms=resp.get("took"),
            info=info,
        )
        return sample

    # ------------------------------------------------------------------ search

    def embed(self, text: str, *, model: str | None = None, dimension: int | None = None) -> Embedding:
        """
        Embed a text with a Bedrock embedding model: an ID from EMBEDDING_MODELS (or an inference profile), or a short
        name ('titan', 'titan-v1', 'cohere', 'cohere-multilingual', 'cohere-v4'). Without a model, Titan Text
        Embeddings V2 is used for 256, 512 or 1,024 dimensions; other sizes need a model. Needs bedrock:InvokeModel.
        """
        if model:
            model_id = _EMBEDDING_ALIASES.get(model.lower(), model)
        elif dimension in (256, 512, 1024):
            model_id = "amazon.titan-embed-text-v2:0"
        else:
            fits = [mid for mid, spec in EMBEDDING_MODELS.items() if dimension in spec[2]]
            options = " or ".join(f"model={mid!r}" for mid in fits) or "model='<Bedrock model ID>'"
            raise ValueError(
                f"Which model embedded these {dimension or '?'}-dimension vectors? Pass {options}, embed=your_function "
                "(text -> list of floats), or vector=[...]. Vectors from a different model aren't comparable."
            )
        style = _embedding_style(model_id)
        spec = EMBEDDING_MODELS.get(_base_model(model_id))
        size = dimension if spec and dimension in spec[2] and dimension != spec[1] else None
        resp = self._service("bedrock-runtime").invoke_model(  # read-only: computes an embedding, changes no AWS resource
            modelId=model_id,
            body=json.dumps(_embedding_request(style, text, size)),
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(resp["body"].read())
        headers = (resp.get("ResponseMetadata") or {}).get("HTTPHeaders") or {}
        tokens = _int(payload.get("inputTextTokenCount")) or _int(headers.get("x-amzn-bedrock-input-token-count"))
        price = spec[3] if spec else None
        cost = tokens * price / 1e6 if tokens is not None and price is not None else None
        return Embedding([float(x) for x in _embedding_from(payload)], model_id, tokens, cost)

    def doc_vector(self, target: Any, index: str, doc_id: str, field: str | None = None) -> list[float]:
        """
        The vector a document holds in a vector field (the index's first one by default).
        """
        store = self.resolve(target)
        info = self.index(store, index, counts=False)
        return self._doc_vector(store, info, str(doc_id), info.vector(field))

    def _doc_vector(self, store: Any, info: IndexInfo, doc_id: str, vf: VectorField) -> list[float]:
        resp = self._search(store, info.name, {"size": 1, "query": {"ids": {"values": [doc_id]}}})
        hits = (resp.get("hits") or {}).get("hits") or []
        if not hits:
            raise ValueError(f"No document {doc_id!r} in {info.name}. sample() shows some document IDs.")
        vectors = [v for v in values_at(hits[0].get("_source") or {}, vf.path) if isinstance(v, list)]
        if not vectors:
            raise ValueError(
                f"Document {doc_id!r} has no '{vf.path}' in what OpenSearch returns (it has no vector, or the "
                "mapping leaves vectors out of _source), so like= can't use it."
            )
        return [float(x) for x in vectors[0]]

    def search(
        self,
        target: Any,
        index: str,
        text: str | None = None,
        *,
        vector: Any = None,
        like: str | None = None,
        k: int = 10,
        field: str | None = None,
        where: Any = None,
        model: str | None = None,
        embed: Callable[[str], Any] | None = None,
        ef_search: int | None = None,
    ) -> SearchResult:
        """
        The k nearest neighbours of a query, by one k-NN search. The query is one of: `text`, embedded by
        embed=your_function or a Bedrock model (see embed()); vector=[...]; or like='<document id>', that document's
        own vector (the document itself is left out of the results). field: the vector field (the index's first by
        default); where: a filter (see build_filter); ef_search: candidates to consider (more is slower, more exact).
        """
        started = time.monotonic()
        store = self.resolve(target)
        info = self.index(store, index, counts=False)
        vf = info.vector(field)
        if sum(x is not None for x in (text, vector, like)) != 1:
            raise ValueError(
                "search() takes one query: a text (search('how do refunds work?')), vector=[...] or "
                "like='<document id>'"
            )
        _check_where(where, info)
        query_filter = build_filter(where, info.fields)  # before embedding, so a typo costs nothing
        embedding = None
        if like is not None:
            query_vector = self._doc_vector(store, info, str(like), vf)
        elif vector is not None:
            query_vector = _as_vector(vector)
        elif embed is not None:
            query_vector = _as_vector(embed(str(text)))
        else:
            embedding = self.embed(str(text), model=model, dimension=vf.dimension)
            query_vector = embedding.vector
        if vf.dimension and len(query_vector) != vf.dimension:
            raise ValueError(
                f"The query vector has {len(query_vector):,} numbers, but '{vf.path}' holds {vf.dimension:,}-dimension "
                "vectors: embed the query with the model (and dimension setting) that embedded the documents."
            )
        k = max(1, _as_int(k, "k"))
        body = knn_query(
            vf,
            query_vector,
            k + (1 if like is not None else 0),
            filter=query_filter,
            ef_search=ef_search,
            exclude=[v.path for v in info.vectors if v.path != vf.path],
        )
        resp = self._search(store, info.name, body)
        hits: list[Hit] = []
        norms: list[float] = []
        excluded = False
        for h in (resp.get("hits") or {}).get("hits") or []:
            if like is not None and h.get("_id") == str(like):
                excluded = True
                continue
            source = h.get("_source") or {}
            for value in values_at(source, vf.path):
                norm = vector_norm(value) if isinstance(value, list) else None
                if norm is not None:
                    norms.append(norm)
            _drop(source, vf.path)
            score = float(h.get("_score") or 0.0)
            hits.append(Hit(len(hits) + 1, str(h.get("_id")), score, source, score_to_similarity(score, vf.space)))
        result = SearchResult(
            target=store.name,
            index=info.name,
            field=vf.path,
            space_type=vf.space,
            k=k,
            hits=hits[:k],
            text_field=info.text_field,
            query=body,
            took_ms=resp.get("took"),
            seconds=time.monotonic() - started,
            text=text,
            like=None if like is None else str(like),
            where=where,
            embedding=embedding,
            query_norm=vector_norm(query_vector),
            hit_norms=norms,
            excluded_self=excluded,
            info=info,
        )
        return result

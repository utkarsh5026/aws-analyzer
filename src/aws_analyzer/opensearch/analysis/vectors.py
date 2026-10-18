"""
How much memory vectors need, how many an index holds, and how healthy a sample of them is.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from ..prices import INSTANCE_TYPES
from ..helpers import GB
from ..models import Domain, IndexInfo, VectorCheck, VectorField


def vector_memory(vf: VectorField, vectors: int) -> int | None:
    """
    Estimated bytes the search structure of `vectors` vectors needs in memory, by OpenSearch's sizing rules:
    HNSW 1.1 x (bytes per vector + 8 x m) per vector; IVF 1.1 x (bytes per vector x vectors + 4 x nlist x dimension).
    fp16, byte, binary and on-disk compression shrink the bytes per vector. None for a trained (PQ) model or an unknown
    dimension. Multiply by IndexInfo.copies for the replicas, which hold their own graphs.
    """
    if not vf.dimension or vf.model_id or vf.encoder == "pq":
        return None
    per_vector = 4 * vf.dimension / vf.compression_factor
    if vf.algorithm == "ivf":
        return int(1.1 * (per_vector * vectors + 4 * (vf.nlist or 4) * vf.dimension))
    return int(1.1 * (per_vector + 8 * vf.links) * vectors)


def vector_count(info: IndexInfo, vf: VectorField) -> int | None:
    """
    How many vectors a field holds: the documents that have one, or for a field inside a nested field, the nested
    documents (Lucene's count, which _cat/indices reports, less the top-level documents).
    """
    if vf.nested and info.docs is not None and info.top_docs is not None and info.docs > info.top_docs:
        return info.docs - info.top_docs
    count = info.with_vector.get(vf.path)
    if count is None:
        count = info.docs if vf.nested else info.documents
    return count


def index_vector_memory(info: IndexInfo, *, native: bool = False) -> int | None:
    """
    Estimated memory of all of an index's vector fields, primaries and replicas; None when it can't be told.
    native=True counts only the fields whose graphs live in the k-NN plugin's memory (faiss and nmslib, not lucene).
    """
    total, known = 0, False
    for vf in info.vectors:
        if native and vf.engine == "lucene":
            continue
        count = vector_count(info, vf)
        need = vector_memory(vf, count or 0) if count is not None else None
        if need is not None:
            total, known = total + need * info.copies, True
    return total if known else None


def knn_memory_limit(memory_gib: float) -> float:
    """
    Bytes of native memory the k-NN plugin may use on a node with `memory_gib` of RAM, at its default limit: half
    of what the Java heap leaves (OpenSearch Service gives the heap half the RAM, up to 32 GiB). faiss and nmslib
    graphs live there; lucene's live in the operating system's file cache instead.
    """
    heap = min(memory_gib / 2, 32)
    return 0.5 * (memory_gib - heap) * GB


def domain_knn_memory(domain: Domain) -> float | None:
    """
    k-NN memory of all the domain's data nodes together (knn_memory_limit per node); None for an unknown type.
    """
    spec = INSTANCE_TYPES.get(domain.instance_type)
    return knn_memory_limit(spec[2]) * domain.instance_count if spec and domain.instance_count else None


def score_to_similarity(score: float | None, space_type: str | None) -> float | None:
    """
    OpenSearch's k-NN score -> the similarity or distance it was computed from (faiss, lucene and nmslib use the
    same formulas): cosinesimil: score = (1 + cosine) / 2; innerproduct: score = 1 + ip when ip > 0, else
    1 / (1 - ip); l2, l1, linf and hamming: score = 1 / (1 + d), where d for l2 is the squared distance on faiss and
    lucene and the distance itself on nmslib.
    """
    if score is None or score <= 0:
        return None
    space = space_type or "l2"
    if space == "cosinesimil":
        return 2 * score - 1
    if space == "innerproduct":
        return score - 1 if score >= 1 else 1 - 1 / score
    return 1 / score - 1


def check_vectors(vectors: Iterable[Any], field: str = "") -> VectorCheck:
    """
    Look over vectors (lists of numbers) -> their dimensions, range of lengths, zeros, exact repeats and vectors
    that hold NaN, infinity or something that isn't a number.
    """
    check = VectorCheck(field)
    seen: set[int] = set()
    norms: list[float] = []
    for vector in vectors:
        check.vectors += 1
        if not isinstance(vector, (list, tuple)) or not vector:
            check.bad += 1
            continue
        try:
            numbers = [float(x) for x in vector]
        except (TypeError, ValueError):
            check.bad += 1
            continue
        if not all(math.isfinite(x) for x in numbers):
            check.bad += 1
            continue
        check.dims[len(numbers)] += 1
        norm = math.sqrt(sum(x * x for x in numbers))
        norms.append(norm)
        check.zeros += norm == 0
        marker = hash(tuple(numbers))
        if marker in seen:
            check.repeats += 1
        seen.add(marker)
    if norms:
        check.norm_min, check.norm_max, check.norm_mean = min(norms), max(norms), sum(norms) / len(norms)
    return check

"""
The region's foundation models and inference profiles, read into ModelInfo: which answer
text, the profile to call each with, and its price.
"""

from __future__ import annotations

import re
from typing import Any

from .prices import model_price
from .helpers import _model_id
from .models import ModelInfo


_GEOGRAPHY_PROFILES = {"us": "us.", "eu": "eu.", "ap": "apac.", "ca": "ca.", "sa": "sa."}


def parse_models(
    summaries: list[dict[str, Any]],
    profiles: list[dict[str, Any]] | None,
    region: str = "",
    model_prices: dict[str, tuple[float, float]] | None = None,
) -> list[ModelInfo]:
    """
    ListFoundationModels summaries + ListInferenceProfiles summaries -> the text models ask() can use. A model
    that can't be called on demand gets the inference profile for this region's geography (e.g. 'us.' in us-east-1),
    else a global one. profiles=None means they couldn't be listed, so such a model's profile is unknown.
    """
    known = profiles is not None
    profiles = profiles or []
    served = _served_models(profiles)
    geo = _GEOGRAPHY_PROFILES.get(region.split("-")[0], "")
    found = [_model_info(summary, served, geo, known, model_prices) for summary in summaries
             if _is_text_model(summary)]
    found += [_application_profile_model(profile, model_prices) for profile in profiles
              if profile.get("type") == "APPLICATION"]
    return sorted(found, key=lambda m: (m.provider.lower(), m.name.lower(), m.id))


def _is_text_model(summary: dict[str, Any]) -> bool:
    """
    Whether a ListFoundationModels summary is a model that answers in text: not a reranker or an embedding model.
    """
    is_text = "TEXT" in (summary.get("outputModalities") or ["TEXT"])
    return is_text and not re.search("rerank|embed", summary.get("modelId", ""))


def _served_models(profiles: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """
    Each model ID -> the inference profiles that serve it.
    """
    served: dict[str, list[dict[str, Any]]] = {}
    for profile in profiles:
        for model_id in dict.fromkeys(_model_id(m.get("modelArn")) for m in profile.get("models") or []):
            served.setdefault(model_id, []).append(profile)
    return served


def _profile_preference(profile: dict[str, Any], geo: str) -> tuple[int, str]:
    """
    How a profile sorts among the ones serving a model: this region's geography (`geo`, 'us.') first, then global.
    """
    pid = profile.get("inferenceProfileId", "")
    return 0 if geo and pid.startswith(geo) else 1 if pid.startswith("global.") else 2, pid


def _model_info(summary: dict[str, Any], served: dict[str, list[dict[str, Any]]], geo: str, known: bool,
                model_prices: dict[str, tuple[float, float]] | None) -> ModelInfo:
    """
    One model, and how to call it: on demand, or through the best inference profile that serves it. One that
    neither serves is 'provisioned only', unless the profiles couldn't be listed (`known` is False). Its price is
    the one for what it's called through, since a global profile can cost less.
    """
    model_id = summary.get("modelId", "")
    options = sorted(served.get(model_id, []), key=lambda profile: _profile_preference(profile, geo))
    info = ModelInfo(
        id=model_id,
        name=summary.get("modelName", ""),
        provider=summary.get("providerName", ""),
        invoke_id=model_id,
        arn=summary.get("modelArn", ""),
        status=(summary.get("modelLifecycle") or {}).get("status", "ACTIVE"),
    )
    if "ON_DEMAND" not in (summary.get("inferenceTypesSupported") or []):
        if options:
            info.via = "inference profile"
            info.invoke_id, info.arn = options[0]["inferenceProfileId"], options[0]["inferenceProfileArn"]
        else:
            info.via = "provisioned only" if known else "inference profile (unknown)"
    price = model_price(info.invoke_id, model_prices)
    if price:
        info.price_in, info.price_out = price
    return info


def _application_profile_model(profile: dict[str, Any],
                               model_prices: dict[str, tuple[float, float]] | None) -> ModelInfo:
    """
    An application inference profile the account made, as a model to call, priced as the model it serves.
    """
    model = _model_id(((profile.get("models") or [{}])[0]).get("modelArn"))
    price = model_price(model, model_prices)
    return ModelInfo(
        id=profile["inferenceProfileArn"],
        name=profile.get("inferenceProfileName", ""),
        provider="your inference profile",
        invoke_id=profile["inferenceProfileArn"],
        arn=profile["inferenceProfileArn"],
        via="inference profile",
        price_in=price[0] if price else None,
        price_out=price[1] if price else None,
    )

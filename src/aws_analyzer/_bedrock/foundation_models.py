"""
The region's foundation models and inference profiles, read into ModelInfo: which answer
text, the profile to call each with, and its price.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .prices import model_price
from .helpers import _model_id
from .models import InferenceProfile, ModelInfo


_GEOGRAPHY_PROFILES = {"us": "us.", "eu": "eu.", "ap": "apac.", "ca": "ca.", "sa": "sa."}

_GEOGRAPHY_NAMES = {"us": "US", "eu": "Europe", "apac": "Asia Pacific", "ap": "Asia Pacific", "ca": "Canada",
                    "sa": "South America", "jp": "Japan", "au": "Australia", "us-gov": "US GovCloud",
                    "global": "Global"}

PROFILE_TYPES = ("SYSTEM_DEFINED", "APPLICATION")


def unique_profiles(summaries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    ListInferenceProfiles summaries, each profile once, in the order listed. The profiles are listed once per type
    in PROFILE_TYPES, since a call without typeEquals returns only the system-defined (cross-region) ones and
    leaves out the application inference profiles the account made.
    """
    profiles: dict[str, dict[str, Any]] = {}
    for summary in summaries:
        key = summary.get("inferenceProfileArn") or summary.get("inferenceProfileId", "")
        profiles.setdefault(key, summary)
    return list(profiles.values())


def names_profile(wanted: str, profile: dict[str, Any]) -> bool:
    """
    Whether `wanted` is this inference profile's ID or ARN, or, for an application inference profile, its name.
    """
    names = [profile.get("inferenceProfileId"), profile.get("inferenceProfileArn")]
    if profile.get("type") == "APPLICATION":
        names.append(profile.get("inferenceProfileName"))
    return wanted in names


def profile_target(profile: dict[str, Any]) -> tuple[str, str]:
    """
    (ID to call, ARN) for an inference profile. An application inference profile is called by its ARN, the ID
    Bedrock's runtime takes for one.
    """
    arn = profile["inferenceProfileArn"]
    if profile.get("type") == "APPLICATION":
        return arn, arn
    return profile["inferenceProfileId"], arn


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


def parse_profiles(
    profiles: list[dict[str, Any]],
    region: str = "",
    model_prices: dict[str, tuple[float, float]] | None = None,
) -> list[InferenceProfile]:
    """
    ListInferenceProfiles summaries -> every inference profile you can pass as model=: the ones your account made
    first, then this region's geography ('us.' in us-east-1), then the global ones, then the rest, each by name.
    """
    geo = _GEOGRAPHY_PROFILES.get(region.split("-")[0], "")
    ordered = sorted(profiles, key=lambda profile: (profile.get("type") != "APPLICATION",
                                                    _profile_preference(profile, geo)[0],
                                                    profile.get("inferenceProfileName", "").lower(),
                                                    profile.get("inferenceProfileId", "")))
    return [_profile_info(profile, model_prices) for profile in ordered]


def _profile_info(profile: dict[str, Any], model_prices: dict[str, tuple[float, float]] | None) -> InferenceProfile:
    """
    One inference profile. A cross-region one is priced by its own ID (a global profile can cost less), and one
    your account made as the model it serves.
    """
    profile_id = profile.get("inferenceProfileId", "")
    is_yours = profile.get("type") == "APPLICATION"
    model_arns = [served.get("modelArn", "") for served in profile.get("models") or []]
    models = list(dict.fromkeys(_model_id(arn) for arn in model_arns if arn))
    priced_as = models[0] if is_yours and models else profile_id
    price = model_price(priced_as, model_prices)
    return InferenceProfile(
        id=profile_id,
        name=profile.get("inferenceProfileName", ""),
        arn=profile.get("inferenceProfileArn", ""),
        invoke_id=profile_target(profile)[0],
        is_yours=is_yours,
        geography="" if is_yours else _GEOGRAPHY_NAMES.get(profile_id.split(".", 1)[0], ""),
        models=models,
        regions=_arn_regions(model_arns),
        description=profile.get("description", ""),
        status=profile.get("status", "ACTIVE"),
        price_in=price[0] if price else None,
        price_out=price[1] if price else None,
    )


def _arn_regions(arns: list[str]) -> list[str]:
    """
    The regions named in a list of ARNs, each once, in order ('arn:aws:bedrock:us-east-1::...' -> 'us-east-1').
    """
    regions = [arn.split(":")[3] for arn in arns if arn.count(":") >= 4]
    return list(dict.fromkeys(region for region in regions if region))

"""Product business selections over the existing, exact preset entry point."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterator

from .contracts import DataVersionRefV2
from .errors import ConfirmationConflictError
from .settings_types import ExecutableModelDefinition
from .workflow_selection import selected_model


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_ROLES = {"admin", "researcher"}
_REFS = ("profileRef", "modelRef", "workflowRef", "resultContractRef")
_PROFILE_CHOICE = re.compile(
    r"(?P<negation>暂不|不要|不想|不|别|禁止|\bdo\s+not\s+|\bdon't\s+|\bnever\s+)?\s*"
    r"(?:选用|选择|使用|采用|\buse|\bselect|\bchoose)\s*(?:档位|\bprofile\b)"
    r"\s*[:=]?\s*[`'\"「“‘]?(?P<id>[a-z](?:[a-z0-9_.-]*[a-z0-9])?)"
    r"(?:(?P<pin>@)(?P<version>[a-z0-9](?:[a-z0-9_.+-]*[a-z0-9])?)?)?"
)
_QUOTED_TEXT = re.compile(r"""```[\s\S]*?```|`[^`\n]*`|"[^"\n]*"|(?<!\w)'[^'\n]*'(?!\w)|“[^”]*”|‘[^’]*’|「[^」]*」""")
_REPLY_SEPARATORS = " \t\r\n，,；;。.!！"
_ATTACHMENT_REPLY = re.compile(
    r"(?:请\s*)?(?:用|使用|采用|上传了|补充)?\s*(?:这份|这个|该)?\s*(?:附件|资料|数据)"
    r"(?:已上传)?|请处理已附加的数据"
    r"|(?:please\s+)?(?:use\s+)?(?:this|the\s+attached)\s+(?:file|data|dataset)",
    re.IGNORECASE,
)


def _valid_ref(ref: object) -> bool:
    return (
        isinstance(ref, dict)
        and set(ref) == {"id", "version", "checksum"}
        and all(isinstance(value, str) and value for value in ref.values())
        and _DIGEST.fullmatch(ref["checksum"]) is not None
    )


def _execution_identity(option: dict) -> dict:
    return {
        "model_id": option["modelRef"]["id"],
        "model_version": option["modelRef"]["version"],
        "model_checksum": option["modelRef"]["checksum"],
        "workflow_id": option["workflowRef"]["id"],
        "workflow_version": option["workflowRef"]["version"],
        "workflow_checksum": option["workflowRef"]["checksum"],
    }


def parse_business_capabilities(
    value: object, models: tuple[ExecutableModelDefinition, ...], services: set[str],
) -> tuple[dict, ...]:
    """Load the validated Assembler projection and independently verify installed targets."""
    error_code = "KERNEL_BUSINESS_CAPABILITY_INVALID"
    if not isinstance(value, list):
        raise ValueError(error_code)
    identities = set()
    try:
        for capability in value:
            ref = capability["ref"]
            roles = set(capability["roles"])
            if (
                not _valid_ref(ref) or (ref["id"], ref["version"]) in identities
                or not roles or not roles <= _ROLES
                or type(capability["defaultVisible"]) is not bool
                or not set(capability["requiredServices"]) <= services
                or not isinstance(capability["profiles"], list) or not capability["profiles"]
            ):
                raise ValueError(error_code)
            identities.add((ref["id"], ref["version"]))
            for option in capability["profiles"]:
                if (
                    not all(_valid_ref(option[key]) for key in _REFS)
                    or not option["roles"] or not set(option["roles"]) <= roles
                ):
                    raise ValueError(error_code)
                model = selected_model(_execution_identity(option), models)
                properties = model.input_schema.get("properties", {})
                inputs = {role["modelParameter"] for role in option["inputRoles"].values()}
                if (
                    inputs != set(model.data_input_parameters)
                    or (option["modelDefaults"].keys() | option["fixedParameters"].keys()) - properties.keys()
                    or option["modelDefaults"] != {
                        name: default for name, default in model.default_parameters.items()
                        if name not in option["fixedParameters"]
                    }
                ):
                    raise ValueError(error_code)
    except (AttributeError, ConfirmationConflictError, KeyError, TypeError) as error:
        raise ValueError(error_code) from error
    return tuple(value)


def _matches(text: str, groups: list[list[str]]) -> bool:
    return all(any(term.casefold() in text for term in group) for group in groups)


def _value(specification: dict, rule: dict, matched: re.Match) -> str | int | float:
    if "value" in rule:
        return rule["value"]
    text = matched.group("value")
    if "values" in rule:
        translations = {key.casefold(): value for key, value in rule["values"].items()}
        return translations[text.casefold()]
    if specification["type"] == "integer":
        return int(text)
    if specification["type"] == "number":
        number = float(text)
        if not math.isfinite(number):
            raise ValueError("BUSINESS_CAPABILITY_PARAMETER_INVALID")
        return number
    return text


def _parameter_matches(pattern: str, message: str) -> Iterator[re.Match[str]]:
    for match in re.finditer(pattern, message, re.IGNORECASE):
        start = match.start()
        # A configured alias must not start inside another ASCII parameter name.
        if start and re.fullmatch(r"[A-Za-z0-9_]{2}", message[start - 1:start + 1]):
            continue
        yield match


def _interpret_parameters(option: dict, message: str, *, reply: bool = False) -> tuple[dict, dict, list[str], str | None]:
    parameters = {**option["modelDefaults"], **option["fixedParameters"]}
    sources = {
        **dict.fromkeys(option["modelDefaults"], "MODEL_DEFAULT"),
        **dict.fromkeys(option["fixedParameters"], "WORKFLOW_PROFILE"),
    }
    conflicts: list[str] = []
    threshold_unit = None
    for spec in option["textParameters"]:
        name = spec["name"]
        values = set()
        units = set()
        rules = spec["patterns"] + (spec.get("replyPatterns", []) if reply else [])
        for rule in rules:
            for match in _parameter_matches(rule["pattern"], message):
                try:
                    values.add(_value(spec, rule, match))
                except (KeyError, TypeError, ValueError):
                    if name not in conflicts:
                        conflicts.append(name)
                    continue
                unit = match.groupdict().get("unit")
                if unit:
                    units.add(unit.casefold())
        if any(re.search(pattern, message, re.IGNORECASE) for pattern in spec.get("unresolvedPatterns", [])):
            values.clear()
            units.clear()
            parameters.pop(name, None)
            sources.pop(name, None)
        if len(values) > 1 or len(units) > 1 or name in conflicts:
            if name not in conflicts:
                conflicts.append(name)
            parameters.pop(name, None)
            sources.pop(name, None)
        elif values:
            parameters[name] = values.pop()
            sources[name] = "USER"
            if name == option.get("thresholdParameter") and units:
                threshold_unit = units.pop()
    return parameters, sources, conflicts, threshold_unit


def _input_bindings(roles: dict, references: list[dict]) -> dict[str, int]:
    result = {}
    for specification in roles.values():
        compatible = [
            index for index, ref in enumerate(references)
            if {"format": ref["format"], "mediaType": ref["mediaType"]} in specification["formatPairs"]
        ]
        if len(compatible) == 1:
            result[specification["modelParameter"]] = compatible[0]
    # Never implicitly assign one attachment to several independent roles.
    counts = Counter(result.values())
    return {name: index for name, index in result.items() if counts[index] == 1}


def _eligible(capability: dict, actor_roles: frozenset[str]) -> bool:
    return (
        capability["tier"] == "CORE" and capability["defaultVisible"]
        and capability["validationStatus"] == "TECHNICALLY_VALIDATED"
        and bool(actor_roles.intersection(capability["roles"]))
    )


def business_capability_candidates(
    registry: tuple[dict, ...], message: str, actor_roles: frozenset[str],
) -> tuple[tuple[dict, dict], ...]:
    """Discover eligible options. The semantic route determines whether they apply."""
    if not registry or len(message) > 12000:
        return ()
    folded = unicodedata.normalize("NFKC", message).casefold()
    quoted = tuple(match.span() for match in _QUOTED_TEXT.finditer(folded))
    requested = set()
    for match in _PROFILE_CHOICE.finditer(folded):
        if any(start <= match.start() < end for start, end in quoted):
            continue
        if match["negation"] or (match["pin"] and not match["version"]):
            return ()
        requested.add((match["id"], match["version"]))
    matches = tuple(
        (capability, option)
        for capability in registry
        if _eligible(capability, actor_roles)
        and _matches(folded, capability["matchTerms"])
        and not any(term.casefold() in folded for term in capability["excludeTerms"])
        for option in capability["profiles"]
        if actor_roles.intersection(option["roles"]) and _matches(folded, option["matchTerms"])
    )
    if not requested:
        return matches
    available = {
        choice for _, option in matches
        for choice in ((option["profileRef"]["id"], None), (option["profileRef"]["id"], option["profileRef"]["version"]))
    }
    if not requested <= available:
        return ()
    return tuple(
        (capability, option) for capability, option in matches
        if (option["profileRef"]["id"], None) in requested
        or (option["profileRef"]["id"], option["profileRef"]["version"]) in requested
    )


def select_business_capability(
    registry: tuple[dict, ...], message: str, actor_roles: frozenset[str], references: list[dict],
) -> dict | None:
    matches = business_capability_candidates(registry, message, actor_roles)
    if len(matches) != 1:
        return None
    return business_workflow_selection(*matches[0], message, references)


def business_workflow_selection(
    capability: dict, option: dict, message: str, references: list[dict],
) -> dict:
    normalized = unicodedata.normalize("NFKC", message)
    parameters, sources, conflicts, threshold_unit = _interpret_parameters(option, normalized)
    return {
        **_execution_identity(option),
        "input_bindings": _input_bindings(option["inputRoles"], references),
        "parameters": parameters,
        "continuation": None,
        "business_capability": {
            "capabilityRef": capability["ref"],
            **{key: option[key] for key in _REFS},
            "title": capability["title"], "description": capability["description"],
            "expectedOutputs": option.get("expectedOutputs", []),
            **{key: option[key] for key in (
                "requiredParameters", "metadataParameters", "requiredChecks", "thresholdParameter",
                "nonOverlappingTimeRanges", "limitations",
            )},
            "parameterConflicts": conflicts, "thresholdUnit": threshold_unit, "argumentSources": sources,
        },
    }


def _installed_option(registry: tuple[dict, ...], selection: dict) -> tuple[dict, dict] | None:
    business = selection["business_capability"]
    return next((
        (capability, option)
        for capability in registry for option in capability["profiles"]
        if capability["ref"] == business["capabilityRef"]
        and all(option[key] == business[key] for key in _REFS)
        and all(selection.get(key) == value for key, value in _execution_identity(option).items())
    ), None)


def _is_parameter_reply(option: dict, message: str) -> bool:
    """Keep cross-turn binding limited to replies entirely described by Profile patterns."""
    spans = sorted(
        match.span()
        for spec in option["textParameters"]
        for rule in spec["patterns"] + spec.get("replyPatterns", [])
        for match in _parameter_matches(rule["pattern"], message)
    )
    end = 0
    for start, stop in spans:
        if start > end and message[end:start].strip(_REPLY_SEPARATORS):
            return False
        end = max(end, stop)
    return bool(spans) and not message[end:].strip(_REPLY_SEPARATORS)


def continue_business_capability(
    registry: tuple[dict, ...], previous: dict, issue: dict, message: str, actor_roles: frozenset[str],
    references: list[dict],
) -> tuple[dict, list[dict]] | None:
    """Accept an answer to the immediately preceding blocked preparation."""
    if len(message) > 12000 or previous.get("business_capability") is None:
        return None
    installed = _installed_option(registry, previous)
    if installed is None:
        return None
    capability, option = installed
    normalized = unicodedata.normalize("NFKC", message)
    if (
        not _eligible(capability, actor_roles)
        or not actor_roles.intersection(option["roles"])
        or any(term.casefold() in normalized.casefold() for term in capability["excludeTerms"])
    ):
        return None
    parameters, sources, conflicts, threshold_unit = _interpret_parameters(option, normalized, reply=True)
    supplied = {name: parameters[name] for name, source in sources.items() if source == "USER"}
    fresh_bindings = _input_bindings(option["inputRoles"], references)
    if issue.get("kind") == "MISSING_DATA":
        reply = normalized.strip(_REPLY_SEPARATORS)
        addressed = issue.get("input") in fresh_bindings and (
            not reply or _ATTACHMENT_REPLY.fullmatch(reply) is not None
            or _is_parameter_reply(option, normalized)
        )
    else:
        parameter = issue.get("parameter")
        addressed = (parameter in supplied or parameter in conflicts) and _is_parameter_reply(option, normalized)
    if not addressed:
        return None
    previous_business = previous["business_capability"]
    if not references:
        inputs = sorted(previous["inputs"].items())
        references = [DataVersionRefV2.from_storage_dict(ref).as_dict() for _, ref in inputs]
        fresh_bindings = {name: index for index, (name, _) in enumerate(inputs)}
    effective_parameters = {**previous["parameters"], **supplied}
    effective_sources = {**previous_business["argumentSources"], **dict.fromkeys(supplied, "USER")}
    all_conflicts = list(dict.fromkeys([
        *(key for key in previous_business["parameterConflicts"] if key not in supplied),
        *conflicts,
    ]))
    for name in all_conflicts:
        effective_parameters.pop(name, None)
        effective_sources.pop(name, None)
    return {
        **previous,
        "input_bindings": fresh_bindings,
        "parameters": effective_parameters,
        "business_capability": {
            **previous_business,
            "argumentSources": effective_sources, "parameterConflicts": all_conflicts,
            "thresholdUnit": (
                threshold_unit if option.get("thresholdParameter") in supplied else previous_business["thresholdUnit"]
            ),
            "continuesPreparation": True,
        },
    }, references


def require_installed_business_capability(registry: tuple[dict, ...], selection: dict | None) -> None:
    business = selection.get("business_capability") if selection is not None else None
    if business is None:
        return
    if _installed_option(registry, selection) is None:
        raise ValueError("BUSINESS_CAPABILITY_SELECTION_STALE")


def public_business_capability_refs(selection: dict | None) -> dict | None:
    business = selection.get("business_capability") if selection is not None else None
    if business is None:
        return None
    return {key: business[key] for key in ("capabilityRef", *_REFS)}


def visible_business_capability_refs(
    registry: tuple[dict, ...], selection: dict | None, actor_roles: frozenset[str],
) -> dict | None:
    if selection is None or selection.get("business_capability") is None:
        return None
    installed = _installed_option(registry, selection)
    if installed is None:
        return None
    capability, option = installed
    if not actor_roles.intersection(capability["roles"]) or not actor_roles.intersection(option["roles"]):
        return None
    return public_business_capability_refs(selection)

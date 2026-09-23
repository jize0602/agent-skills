"""Pure GitHub authorization and continuation state transitions.

This module consumes caller-supplied evidence only. It performs no network or
credential access and never executes the requested repository operation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


OPERATIONS = frozenset({"READ", "SYNC", "BACKUP"})
STATUSES = frozenset(
    {
        "AUTHORIZED",
        "AUTH_REQUIRED",
        "WRONG_ACCOUNT",
        "NO_GITHUB_INTEGRATION",
        "FORBIDDEN",
        "OFFLINE",
        "REPO_NOT_FOUND",
        "UNKNOWN_REPOSITORY_ACCESS",
        "UNSUPPORTED_ENVIRONMENT",
    }
)

_INTENT_REQUIRED = frozenset({"operation", "owner", "repository"})
_OBSERVATION_FIELDS = frozenset(
    {
        "integration",
        "online",
        "identity",
        "expected_owner",
        "repo_http",
        "repo_access",
        "owner_repository_listing_complete",
        "owner_repository_present",
        "repo_absence_authoritative",
        "auth_denied",
    }
)


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _validate_intent(intent: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(intent, Mapping):
        raise ValueError("intent must be an object")
    keys = set(intent)
    if not _INTENT_REQUIRED <= keys or keys - (_INTENT_REQUIRED | {"branch"}):
        raise ValueError("intent must contain operation, owner, repository and optional branch")
    if not isinstance(intent["operation"], str) or intent["operation"] not in OPERATIONS:
        raise ValueError("operation must be READ, SYNC or BACKUP")
    for field in ("owner", "repository"):
        if not _non_empty_string(intent[field]):
            raise ValueError(f"{field} must be a non-empty string")
    result = {
        "operation": intent["operation"],
        "owner": intent["owner"],
        "repository": intent["repository"],
    }
    if "branch" in intent:
        if not _non_empty_string(intent["branch"]):
            raise ValueError("branch must be a non-empty string when supplied")
        result["branch"] = intent["branch"]
    return result


def _validate_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(observation, Mapping):
        raise ValueError("observation must be an object")
    if set(observation) != _OBSERVATION_FIELDS:
        raise ValueError("observation must contain exactly the contract fields")

    for field in (
        "integration",
        "online",
        "owner_repository_listing_complete",
        "repo_absence_authoritative",
        "auth_denied",
    ):
        if not isinstance(observation[field], bool):
            raise ValueError(f"{field} must be a bool")

    identity = observation["identity"]
    if identity is not None and not _non_empty_string(identity):
        raise ValueError("identity must be a non-empty string or null")
    expected_owner = observation["expected_owner"]
    if not _non_empty_string(expected_owner):
        raise ValueError("expected_owner must be a non-empty string")

    repo_http = observation["repo_http"]
    if repo_http is not None and (
        isinstance(repo_http, bool)
        or not isinstance(repo_http, int)
        or not 100 <= repo_http <= 599
    ):
        raise ValueError("repo_http must be an HTTP status integer or null")

    for field in ("repo_access", "owner_repository_present"):
        if observation[field] is not None and not isinstance(observation[field], bool):
            raise ValueError(f"{field} must be a bool or null")

    return dict(observation)


def _result(
    status: str,
    intent: dict[str, str],
    next_action: str,
    *,
    resumable: bool,
) -> dict[str, Any]:
    return {
        "status": status,
        "intent": dict(intent),
        "next_action": next_action,
        "can_continue": status == "AUTHORIZED",
        "resumable": resumable,
        "may_request_private_creation": status == "REPO_NOT_FOUND",
    }


def unsupported_environment(intent: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve an intent when this runtime cannot supply a supported observation."""

    return _result(
        "UNSUPPORTED_ENVIRONMENT",
        _validate_intent(intent),
        "use_supported_environment",
        resumable=True,
    )


def evaluate(
    intent: Mapping[str, Any], observation: Mapping[str, Any]
) -> dict[str, Any]:
    """Classify current evidence and return a resumable, intent-preserving result."""

    pending = _validate_intent(intent)
    evidence = _validate_observation(observation)
    identity = evidence["identity"]
    repo_http = evidence["repo_http"]
    repo_access = evidence["repo_access"]

    if evidence["auth_denied"]:
        return _result("AUTH_REQUIRED", pending, "stop", resumable=False)
    if not evidence["integration"]:
        return _result(
            "NO_GITHUB_INTEGRATION",
            pending,
            "connect_official_platform_github",
            resumable=True,
        )
    if not evidence["online"]:
        return _result("OFFLINE", pending, "retry_when_online", resumable=True)
    if identity is not None and identity != evidence["expected_owner"]:
        return _result(
            "WRONG_ACCOUNT",
            pending,
            "select_expected_account_with_official_platform_github",
            resumable=True,
        )
    if repo_http == 403:
        return _result("FORBIDDEN", pending, "stop", resumable=False)
    if repo_http == 401:
        return _result(
            "AUTH_REQUIRED",
            pending,
            "authorize_with_official_platform_github",
            resumable=True,
        )
    if identity is None:
        return _result(
            "AUTH_REQUIRED",
            pending,
            "authorize_with_official_platform_github",
            resumable=True,
        )
    successful_http = repo_http is not None and 200 <= repo_http < 300
    conflicting_access = (
        (repo_access is True and repo_http is not None and not successful_http)
        or (repo_access is False and successful_http)
    )
    if conflicting_access:
        return _result(
            "UNKNOWN_REPOSITORY_ACCESS", pending, "verify_repository_access", resumable=True
        )

    proven_absent = (
        identity == evidence["expected_owner"] == pending["owner"]
        and evidence["repo_absence_authoritative"]
    )
    if (
        proven_absent
        and repo_http == 404
        and repo_access is not True
        and not successful_http
        and evidence["owner_repository_present"] is not True
    ):
        return _result(
            "REPO_NOT_FOUND",
            pending,
            "request_private_creation_confirmation",
            resumable=True,
        )

    if repo_http == 404:
        return _result(
            "UNKNOWN_REPOSITORY_ACCESS", pending, "verify_repository_access", resumable=True
        )
    if successful_http or (repo_access is True and repo_http is None):
        return _result("AUTHORIZED", pending, "continue_operation", resumable=False)
    return _result(
        "UNKNOWN_REPOSITORY_ACCESS", pending, "verify_repository_access", resumable=True
    )


def resume(state: Mapping[str, Any], observation: Mapping[str, Any]) -> dict[str, Any]:
    """Re-evaluate fresh evidence for a pending state without changing its intent."""

    status = state.get("status") if isinstance(state, Mapping) else None
    if not isinstance(status, str) or status not in STATUSES:
        raise ValueError("state must be a result from this state machine")
    if state.get("resumable") is not True:
        raise ValueError("this state cannot be resumed")
    return evaluate(state.get("intent"), observation)

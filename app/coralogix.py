"""Coralogix Rule Groups API client for parsing rules export and import."""

import copy
import httpx
from typing import Any, Optional

CORALOGIX_DOMAINS = {
    "us1": "api.us1.coralogix.com",
    "us2": "api.us2.coralogix.com",
    "eu1": "api.eu1.coralogix.com",
    "eu2": "api.eu2.coralogix.com",
    "ap1": "api.ap1.coralogix.com",
    "ap2": "api.ap2.coralogix.com",
    "ap3": "api.ap3.coralogix.com",
}


def validate_domain(domain: str) -> str:
    """Validate domain is an allowed Coralogix region. Returns normalized domain or raises ValueError."""
    domain_lower = domain.lower().strip()
    if domain_lower not in CORALOGIX_DOMAINS:
        raise ValueError(
            f"Invalid domain: {domain!r}. Must be one of: {', '.join(CORALOGIX_DOMAINS.keys())}"
        )
    return domain_lower


def _get_base_url(domain: str) -> str:
    """Resolve domain key to full API base URL. Domain must be pre-validated."""
    domain_lower = validate_domain(domain)
    return f"https://{CORALOGIX_DOMAINS[domain_lower]}"


# Keys to strip from rule groups, subgroups, and rules before import
_STRIP_KEYS = frozenset({"id", "createdAt", "updatedAt", "created_at", "updated_at"})


def _strip_ids_recursive(obj: Any) -> Any:
    """Recursively remove ID-like keys from dicts. Returns copy."""
    if isinstance(obj, dict):
        return {
            k: _strip_ids_recursive(v)
            for k, v in obj.items()
            if k not in _STRIP_KEYS
        }
    if isinstance(obj, list):
        return [_strip_ids_recursive(item) for item in obj]
    return obj


def filter_rule_groups_by_names(
    rule_groups: list[dict[str, Any]], names: Optional[list[str]]
) -> list[dict[str, Any]]:
    """
    If names is non-empty, return only groups whose name is in the set (case-insensitive exact match).
    If names is empty/None, return all groups.
    """
    if not names:
        return rule_groups
    name_set = {n.strip().lower() for n in names if n and str(n).strip()}
    if not name_set:
        return rule_groups
    return [
        g
        for g in rule_groups
        if isinstance(g, dict) and (g.get("name") or "").strip().lower() in name_set
    ]


def extract_source_names(rule_groups: list[dict[str, Any]]) -> set[str]:
    """Return set of rule group names from source."""
    return {
        str(g.get("name", "")).strip()
        for g in rule_groups
        if isinstance(g, dict) and g.get("name")
    }


def prepare_for_import(rule_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Recursively strip id, createdAt, updatedAt from groups, subgroups, and rules.
    Return list of create-ready payloads.
    """
    result = []
    for g in rule_groups:
        if not isinstance(g, dict):
            continue
        payload = _strip_ids_recursive(copy.deepcopy(g))
        result.append(payload)
    return result


async def fetch_rule_groups(domain: str, api_key: str) -> list[dict[str, Any]]:
    """
    Fetch all rule groups from a Coralogix team.
    GET https://api.<domain>/mgmt/openapi/latest/parsing-rules/rule-groups/v1
    """
    base_url = _get_base_url(domain)
    url = f"{base_url}/mgmt/openapi/latest/parsing-rules/rule-groups/v1"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data.get("ruleGroups") or data.get("rule_groups") or []


async def fetch_dest_rule_group_names(domain: str, api_key: str) -> set[str]:
    """List destination rule groups and return set of names for duplicate check."""
    try:
        groups = await fetch_rule_groups(domain, api_key)
        return extract_source_names(groups)
    except Exception:
        return set()


async def bulk_import_rule_groups(
    domain: str, api_key: str, payloads: list[dict[str, Any]]
) -> dict[str, Any]:
    """
    Import rule groups into a Coralogix team.
    POST each payload to Create endpoint.
    Returns { created, total, errors }.
    """
    base_url = _get_base_url(domain)
    url = f"{base_url}/mgmt/openapi/latest/parsing-rules/rule-groups/v1"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    created = 0
    errors: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for i, payload in enumerate(payloads):
            try:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code in (200, 201):
                    created += 1
                else:
                    name = payload.get("name", "")
                    errors.append(
                        {
                            "index": i,
                            "name": name,
                            "status": resp.status_code,
                            "body": resp.text[:200],
                        }
                    )
            except Exception as e:
                name = payload.get("name", "")
                errors.append({"index": i, "name": name, "error": str(e)})
    return {"created": created, "total": len(payloads), "errors": errors}

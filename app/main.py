import os
from typing import Optional

import httpx
from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from pathlib import Path

from app.coralogix import (
    CORALOGIX_DOMAINS,
    validate_domain,
    fetch_rule_groups,
    bulk_import_rule_groups,
    prepare_for_import,
    fetch_dest_rule_group_names,
    extract_source_names,
    filter_rule_groups_by_names,
)

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Coralogix Parsing Rules Migrator")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS: same-origin only by default; set CORS_ORIGINS env (comma-separated) for allowed origins
_cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

# Optional API key auth: if APP_API_KEY env is set, require X-API-Key header
APP_API_KEY = os.environ.get("APP_API_KEY")


async def verify_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")):
    """If APP_API_KEY is set, require matching X-API-Key header."""
    if APP_API_KEY and x_api_key != APP_API_KEY:
        raise HTTPException(401, "Invalid or missing API key")


class ExportRequest(BaseModel):
    source_domain: str = Field(..., min_length=1, max_length=32, description="Source team domain (e.g. us1, eu1)")
    source_api_key: str = Field(..., min_length=1, max_length=512, description="Source team API key")
    dest_domain: str = Field(..., min_length=1, max_length=32, description="Destination team domain")
    dest_api_key: str = Field(..., min_length=1, max_length=512, description="Destination team API key")
    group_names_filter: Optional[str] = Field(
        default=None,
        description="Optional comma-separated group names. If provided, only matching rule groups are exported.",
    )


def _domain_choices() -> list[dict[str, str]]:
    return [
        {"value": k, "label": f"{k.upper()} ({v})"}
        for k, v in CORALOGIX_DOMAINS.items()
    ]


def _parse_group_names_filter(value: Optional[str]) -> Optional[list[str]]:
    """Parse comma-separated group names, return None if empty."""
    if not value or not str(value).strip():
        return None
    names = [n.strip() for n in str(value).split(",") if n.strip()]
    return names if names else None


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "domains": _domain_choices(), "auth_required": bool(APP_API_KEY)},
    )


@app.post("/export")
@limiter.limit("10/minute")
async def export_rule_groups(
    request: Request,
    body: ExportRequest,
    _: None = Depends(verify_api_key),
):
    # Validate domains to prevent SSRF
    try:
        validate_domain(body.source_domain)
        validate_domain(body.dest_domain)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Fetch rule groups from source
    try:
        rule_groups = await fetch_rule_groups(body.source_domain, body.source_api_key)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise HTTPException(401, "Invalid or expired source API key")
        if e.response.status_code == 403:
            raise HTTPException(
                403,
                "Access denied for source team. Ensure your API key has PARSINGRULES permission and your IP is allowed.",
            )
        raise HTTPException(e.response.status_code, "Source request failed. Check your API key and permissions.")
    except Exception:
        raise HTTPException(500, "An unexpected error occurred. Please try again.")

    # Apply selective filter if provided
    group_names = _parse_group_names_filter(body.group_names_filter)
    rule_groups = filter_rule_groups_by_names(rule_groups, group_names)

    if not rule_groups:
        if group_names:
            return {
                "success": True,
                "message": "No rule groups matched the filter. Check the group names and try again.",
                "count": 0,
            }
        return {
            "success": True,
            "message": "No rule groups to export. Source team has no rule groups.",
            "count": 0,
        }

    # Duplicate check: if destination already has all source group names, skip
    source_names = extract_source_names(rule_groups)
    if source_names:
        try:
            dest_names = await fetch_dest_rule_group_names(body.dest_domain, body.dest_api_key)
            if source_names <= dest_names:
                matched = len(source_names)
                return {
                    "success": True,
                    "already_exported": True,
                    "message": f"Rule groups appear to have already been exported. Destination has {matched} of {matched} group(s) with matching names. Skipping to avoid duplicates.",
                    "count": 0,
                }
        except Exception:
            pass  # Proceed with import if dest check fails

    # Prepare payloads (strip IDs)
    payloads = prepare_for_import(rule_groups)

    # Bulk import to destination
    try:
        bulk_resp = await bulk_import_rule_groups(body.dest_domain, body.dest_api_key, payloads)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise HTTPException(401, "Invalid or expired destination API key")
        if e.response.status_code == 403:
            raise HTTPException(
                403,
                "Access denied for destination team. Ensure your API key has PARSINGRULES permission and your IP is allowed.",
            )
        raise HTTPException(e.response.status_code, "Bulk import failed. Check your API key and permissions.")
    except Exception:
        raise HTTPException(500, "An unexpected error occurred. Please try again.")

    created = bulk_resp.get("created", 0)
    errs = bulk_resp.get("errors", [])
    msg = f"Created {created} rule group(s) in destination team."
    if errs:
        msg += f" {len(errs)} failed."
    return {"success": True, "message": msg, "count": created}

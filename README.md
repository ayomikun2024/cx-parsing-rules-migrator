# Coralogix Parsing Rules Migrator

A web application that exports parsing rule groups from a source Coralogix team and imports them into a destination team.

## Supported API

Uses the **Rule Groups Service** (OpenAPI mgmt API):

- **List**: `GET /mgmt/openapi/latest/parsing-rules/rule-groups/v1`
- **Create**: `POST /mgmt/openapi/latest/parsing-rules/rule-groups/v1`

See the [Parsing Rules API docs](https://coralogix.com/docs/developer-portal/apis/data-management/parsing-rules-api/) for details.

## Setup

### Prerequisites

- Python 3.10+

### Installation

```bash
cd coralogix-parsing-rules-migrator
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Run

```bash
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.

If you get `Address already in use`, either stop the existing process or use another port:

```bash
uvicorn app.main:app --reload --port 8001
```

## Usage

1. **Source Team**: Select the region/domain and enter the API key for the team you want to export rule groups from.
2. **Destination Team**: Select the region/domain and enter the API key for the team you want to import rule groups into.
3. **Group names filter** (optional): Enter comma-separated group names to export only specific rule groups. Leave empty to export all.
4. Click **Export Rule Groups**.

**Flow:**
1. Fetches rule groups from the source via `GET /parsing-rules/rule-groups/v1`
2. Optionally filters by group names (case-insensitive exact match)
3. Strips IDs and timestamps from payloads
4. Imports into the destination via `POST /parsing-rules/rule-groups/v1` for each group

## API Key Permissions

Create API keys in **Data Flow > API Keys** for both source and destination teams. Use the **PARSINGRULES** role preset.

If you get "Access denied" (403), verify the API key has the PARSINGRULES preset and that your IP is allowed under **Account Settings > IP Access Control**.

## Supported Regions

| Region | API Domain |
|--------|------------|
| US1 | api.us1.coralogix.com |
| US2 | api.us2.coralogix.com |
| EU1 | api.eu1.coralogix.com |
| EU2 | api.eu2.coralogix.com |
| AP1 | api.ap1.coralogix.com |
| AP2 | api.ap2.coralogix.com |
| AP3 | api.ap3.coralogix.com |

## Notes

- **Rule group order** is preserved during export and import.
- **Rule matchers** (application name, subsystem name, severity) are copied as-is.
- **Selective export**: Use the group names filter to export only specific rule groups. Names are matched case-insensitively (exact match).
- **Empty source**: If the source has no rule groups (or none match the filter), the operation completes successfully with a count of 0.
- **Duplicate check**: If the destination already has all source group names, the import is skipped to avoid duplicates.
- **Security**: API keys are sent only in the request body and are not logged or persisted. Run behind HTTPS in production.

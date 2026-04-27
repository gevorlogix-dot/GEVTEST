# Oregon API Test Suite

Automated API regression tests for the Oregon reporting service.  
Tests are data-driven: all payloads, expected statuses, and schema rules live in `tests/test_cases.json` — no hardcoded values in test code.

---

## Project layout

```
CLOUD_AUTOMATION_ORMT/
├── .env.example                     # Safe template — copy to .env and fill in values
├── .github/
│   └── workflows/
│       └── test.yml                 # GitHub Actions CI/CD pipeline
├── assets/
│   └── style.css                    # Styles for the HTML test report
├── tests/
│   ├── conftest.py                  # Session-scoped fixtures (auth, base_url, headers)
│   ├── test_cases.json              # All test data: payloads, expected statuses, schemas
│   └── test_oregon_api.py           # Test functions — load everything from test_cases.json
├── Oregon report.postman_collection.json  # Postman collection for manual exploration
├── pytest.ini                       # pytest configuration
└── requirements.txt                 # Pinned Python dependencies
```

---

## Quick start

### 1. Clone and create environment

```bash
git clone <repo-url>
cd CLOUD_AUTOMATION_ORMT

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Required | Description |
|---|---|---|
| `OREGON_BASE_URL` | Yes | API base URL, e.g. `https://api-oregon-report.example.com` |
| `OREGON_AUTH_EMAIL` | Yes | Login email |
| `OREGON_AUTH_PASSWORD` | Yes | Login password |
| `OREGON_AUTH_TOKEN` | No | Bearer token — auto-populated after first run |
| `REPORT_ID` | No | Auto-populated by `test_report_create` |
| `VEHICLE_ID` | No | Auto-populated by `test_vehicle_update` |

> `OREGON_BASE_URL` may include or omit a trailing `/api` — the fixture normalises both forms.

### 3. Run the suite

```bash
# Full suite
pytest

# Specific scenario
pytest -k "authentication"
pytest -k "report"

# Stop on first failure
pytest -x

# Dry-run: collect but don't execute
pytest --collect-only
```

After each run an HTML report is written to `report.html`.

---

## Test scenarios

| Scenario | Cases | Description |
|---|---|---|
| authentication | 7 | Login (positive + negative), registration validation |
| authorization | 8 | Protected endpoints, weight groups, filing options, logout |
| report_management | 11 | Create, show, list, filtered count |
| vehicle_operations | 7 | Update single/multiple, remove |
| payment_processing | 4 | Pay with valid/invalid/expired card |
| data_retrieval | 7 | Zip info, states, USDOT lookup, user vehicles |
| calculation_services | 5 | Use-tax calculation: normal, boundary, invalid inputs |

Total: **49 JSON-driven cases** + 7 robustness tests (malformed JSON, large payloads, unicode, edge-case pagination).

### Test execution order

Tests run in file order, which matters because some tests produce IDs consumed by later ones:

```
login → create report (→ REPORT_ID) → vehicle update (→ VEHICLE_ID) → pay report → logout
```

---

## Adding a test case

No Python changes needed for parametrized groups.  
Open `tests/test_cases.json` and add an entry under the correct scenario:

```json
"negative_login_locked_account": {
  "description": "Login attempt on a locked account",
  "endpoint": "/api/auth/login",
  "method": "POST",
  "content_type": "form",
  "auth_required": false,
  "requires_env": [],
  "payload": { "email": "locked@example.com", "password": "password", "remember_me": "0" },
  "expected_status": [401, 403],
  "expected_response_keys": ["errors", "message"],
  "schema": null,
  "schema_validation": false
}
```

Keys prefixed `negative_login` are automatically picked up by `test_login_negative`.

### Placeholder substitution

Use `{ENV_VAR}` in `endpoint` paths or `payload` values to inject runtime env vars:

```json
"endpoint": "/api/report/show/{REPORT_ID}",
"payload": { "report_id": "{REPORT_ID}" }
```

---

## CI/CD — GitHub Actions

The workflow at `.github/workflows/test.yml` runs on every push/PR to `main`/`master` and on a daily schedule.

### Required secrets

Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret name | Value |
|---|---|
| `OREGON_BASE_URL` | API base URL |
| `OREGON_AUTH_EMAIL` | Login email |
| `OREGON_AUTH_PASSWORD` | Login password |

`OREGON_AUTH_TOKEN` is intentionally omitted from CI — the suite always does a fresh login.

### Manual trigger

Go to **Actions → Oregon API Tests → Run workflow** to trigger a run on demand.

### Artifacts

The HTML report is uploaded as `test-report-<run-number>` and kept for 30 days.  
Download it from the **Summary** tab of any workflow run.

---

## Environment variables reference

```
OREGON_BASE_URL          Base URL of the API (required)
OREGON_AUTH_EMAIL        Login email (required for auth tests)
OREGON_AUTH_PASSWORD     Login password (required for auth tests)
OREGON_AUTH_TOKEN        Bearer token — auto-saved after login, auto-validated on reuse
REPORT_ID                Persisted after test_report_create succeeds
VEHICLE_ID               Persisted after test_vehicle_update succeeds
```

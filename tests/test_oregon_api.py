import json
import os
import random
import re
import time
import uuid
from pathlib import Path

import pytest
import requests

# ─── Constants ────────────────────────────────────────────────────────────────

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
TC_PATH = Path(__file__).parent / "test_cases.json"
SESSION_STATE: dict = {}

RATE_LIMIT_SECONDS = 2
_last_request_time: float = 0

# ─── Test-case loader ─────────────────────────────────────────────────────────

_TC: dict = json.loads(TC_PATH.read_text(encoding="utf-8"))


def tc(scenario: str, name: str) -> dict:
    """Return a single test-case definition from test_cases.json."""
    return _TC["test_scenarios"][scenario]["test_cases"][name]


def _cases(scenario: str, prefix: str = "") -> list:
    """Return pytest.param objects for every case whose key starts with *prefix*."""
    return [
        pytest.param(v, id=k)
        for k, v in _TC["test_scenarios"][scenario]["test_cases"].items()
        if not prefix or k.startswith(prefix)
    ]


# ─── Environment helpers ──────────────────────────────────────────────────────

def env_var(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is not None:
        value = value.strip()
    return default if (value is not None and value == "") else value


def _persist_env_variable(name: str, value) -> None:
    if value is None:
        return
    value_str = str(value).strip()
    if not value_str:
        return
    os.environ[name] = value_str
    if not ENV_PATH.exists():
        ENV_PATH.write_text("\n")
    lines = ENV_PATH.read_text().splitlines()
    updated, found = [], False
    for line in lines:
        if line.strip().startswith(f"{name}="):
            updated.append(f"{name}={value_str}")
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(f"{name}={value_str}")
    ENV_PATH.write_text("\n".join(updated).rstrip("\n") + "\n")


def _set_dynamic_env(name: str, value) -> None:
    if value is None:
        return
    value_str = str(value)
    if os.getenv(name) == value_str:
        return
    SESSION_STATE[name] = value_str
    _persist_env_variable(name, value_str)


# ─── Rate limiting ────────────────────────────────────────────────────────────

def enforce_rate_limit() -> None:
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < RATE_LIMIT_SECONDS:
        time.sleep(RATE_LIMIT_SECONDS - elapsed)
    _last_request_time = time.time()


def make_request(method: str, url: str, **kwargs) -> requests.Response:
    enforce_rate_limit()
    return requests.request(method.upper(), url, **kwargs)


# ─── Assertion helpers ────────────────────────────────────────────────────────

def assert_valid_json_response(response: requests.Response) -> dict | list:
    assert response.headers.get("Content-Type", "").startswith("application/json"), \
        "Expected JSON Content-Type"
    try:
        body = response.json()
    except ValueError:
        pytest.fail("Response body is not valid JSON")
    assert isinstance(body, (dict, list)), "Expected JSON object or array"
    return body


def _body_contains_any_key(body, keys) -> bool:
    if isinstance(body, dict):
        if any(k in body for k in keys):
            return True
        return any(_body_contains_any_key(val, keys) for val in body.values())
    if isinstance(body, list):
        return any(_body_contains_any_key(item, keys) for item in body)
    return False


def assert_contains_any_keys(body, keys) -> None:
    if isinstance(body, (dict, list)):
        assert _body_contains_any_key(body, keys), \
            f"Expected one of {keys} in response, got: {body}"
    else:
        assert body, "Expected non-empty response"


def _json_type_matches(value, expected_type: str) -> bool:
    checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    return checks.get(expected_type, lambda v: False)(value)


def assert_matches_schema(instance, schema: dict | None, path: str = "body") -> None:
    if not schema:
        return
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        assert any(_json_type_matches(instance, t) for t in types), \
            f"{path} expected type {types}, got {type(instance).__name__}"
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            assert key in instance, f"{path} missing required key '{key}'"
        for key, sub in schema.get("properties", {}).items():
            if key in instance:
                assert_matches_schema(instance[key], sub, f"{path}.{key}")
    if isinstance(instance, list):
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(instance):
                assert_matches_schema(item, item_schema, f"{path}[{i}]")
    if "enum" in schema:
        assert instance in schema["enum"], f"{path} must be one of {schema['enum']}"


# ─── Response schemas ─────────────────────────────────────────────────────────

LOGIN_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "message": {"type": "string"},
        "data": {"type": ["object", "null"]},
        "token": {"type": "string"},
        "access_token": {"type": "string"},
    },
}

GENERAL_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "message": {"type": "string"},
        "data": {"type": ["object", "array", "null"]},
    },
}

LIST_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "message": {"type": "string"},
        "data": {"type": "array"},
    },
}

ZIP_INFO_SCHEMA = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "message": {"type": "string"},
        "data": {"type": ["object", "array", "null"]},
    },
}

_SCHEMA_MAP = {
    "login": LOGIN_RESPONSE_SCHEMA,
    "general": GENERAL_RESPONSE_SCHEMA,
    "list": LIST_RESPONSE_SCHEMA,
    "zip": ZIP_INFO_SCHEMA,
}

# ─── Response value extractors ────────────────────────────────────────────────

def _find_value(body, keys):
    if isinstance(body, dict):
        for k in keys:
            if k in body and body[k] not in (None, ""):
                return body[k]
        for v in body.values():
            result = _find_value(v, keys)
            if result is not None:
                return result
    elif isinstance(body, list):
        for item in body:
            result = _find_value(item, keys)
            if result is not None:
                return result
    return None


def _extract_report_id(body: dict):
    rid = _find_value(body, ["report_id"])
    if rid is not None:
        return rid
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, dict):
        return _find_value(data, ["report_id", "id"])
    return _find_value(body, ["id"])


def _extract_vehicle_id(body: dict):
    vid = _find_value(body, ["vehicle_id"])
    if vid is not None:
        return vid
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, dict):
        vehicles = data.get("vehicles")
        if isinstance(vehicles, list):
            for v in vehicles:
                if isinstance(v, dict) and v.get("id") not in (None, ""):
                    return v["id"]
    return None


# ─── TC request / assertion helpers ──────────────────────────────────────────

def _resolve(obj):
    """Recursively substitute {ENV_VAR} placeholders with values from os.environ."""
    if isinstance(obj, dict):
        return {k: _resolve(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve(item) for item in obj]
    if isinstance(obj, str):
        m = re.fullmatch(r"\{([A-Z_]+)\}", obj)
        if m:
            return env_var(m.group(1))
    return obj


def _do_request(base_url: str, case: dict, hdrs: dict) -> requests.Response:
    """Build and fire an HTTP request described by a test-case dict."""
    endpoint = re.sub(
        r"\{([A-Z_]+)\}",
        lambda m: env_var(m.group(1)) or m.group(0),
        case["endpoint"],
    )
    url = f"{base_url}{endpoint}"
    payload = _resolve(case.get("payload"))
    params = case.get("params")
    use_json = case.get("content_type") == "json"

    req_hdrs = hdrs.copy()
    if use_json:
        req_hdrs["Content-Type"] = "application/json"

    kwargs: dict = {"params": params, "timeout": 30}
    if payload is not None:
        kwargs["json" if use_json else "data"] = payload

    return make_request(case["method"], url, headers=req_hdrs, **kwargs)


def _assert_case(response: requests.Response, case: dict) -> dict | list:
    """Assert status, response keys, and optional schema from a test-case dict."""
    desc = case.get("description", "")
    assert response.status_code in case["expected_status"], (
        f"[{desc}] Expected {case['expected_status']}, got {response.status_code}"
    )
    body = assert_valid_json_response(response)
    if case.get("expected_response_keys"):
        assert_contains_any_keys(body, case["expected_response_keys"])
    if case.get("schema_validation") and case.get("schema") in _SCHEMA_MAP:
        assert_matches_schema(body, _SCHEMA_MAP[case["schema"]])
    return body


def _skip_if_missing_env(case: dict) -> None:
    for name in case.get("requires_env", []):
        if not env_var(name):
            pytest.skip(f"Set {name} to run this test")


# =============================================================================
# AUTHENTICATION TESTS
# =============================================================================

def test_login(base_url, headers):
    case = tc("authentication", "positive_login")
    _skip_if_missing_env(case)
    response = _do_request(base_url, case, headers)
    _assert_case(response, case)


@pytest.mark.parametrize("case", _cases("authentication", "negative_login"))
def test_login_negative(base_url, headers, case):
    response = _do_request(base_url, case, headers)
    _assert_case(response, case)


def test_register_positive(base_url, headers):
    case = tc("authentication", "positive_register")
    uid = uuid.uuid4().hex[:10]
    area = random.randint(200, 999)
    payload = {
        "full_name": f"Test User {uid}",
        "usdot": str(random.randint(1000000, 9999999)),
        "email": f"testuser_{uid}@mailinator.com",
        "cdd_account": f"TEST{uid.upper()}",
        "phone": f"({area}) {random.randint(100, 999)}-{random.randint(1000, 9999)}",
        "password": f"TestPass{uid}!",
        "password_confirmation": f"TestPass{uid}!",
    }
    response = make_request("POST", f"{base_url}{case['endpoint']}",
                            headers=headers, data=payload, timeout=30)
    _assert_case(response, case)


@pytest.mark.parametrize("case", _cases("authentication", "negative_register"))
def test_register_negative(base_url, headers, case):
    response = _do_request(base_url, case, headers)
    _assert_case(response, case)


# =============================================================================
# AUTHORIZATION TESTS
# =============================================================================

def test_get_me_info(base_url, auth_headers):
    case = tc("authorization", "positive_get_me_info")
    response = _do_request(base_url, case, auth_headers)
    _assert_case(response, case)


def test_get_me_info_unauthorized(base_url, headers):
    case = tc("authorization", "negative_get_me_info_unauthorized")
    response = _do_request(base_url, case, headers)
    _assert_case(response, case)


def test_get_customer_cards(base_url, auth_headers):
    case = tc("authorization", "positive_get_customer_cards")
    response = _do_request(base_url, case, auth_headers)
    _assert_case(response, case)


def test_get_weight_groups(base_url, auth_headers):
    case = tc("authorization", "positive_get_weight_groups")
    response = _do_request(base_url, case, auth_headers)
    _assert_case(response, case)


def test_get_weight_groups_unauthorized(base_url, headers):
    case = tc("authorization", "negative_get_weight_groups_unauthorized")
    response = _do_request(base_url, case, headers)
    _assert_case(response, case)


def test_get_filing_options(base_url, auth_headers):
    case = tc("authorization", "positive_get_filing_options")
    response = _do_request(base_url, case, auth_headers)
    _assert_case(response, case)


def test_get_filing_options_invalid_id(base_url, auth_headers):
    case = tc("authorization", "negative_get_filing_options_invalid_id")
    response = _do_request(base_url, case, auth_headers)
    _assert_case(response, case)


def test_unauthorized_access_protected_endpoints(base_url, headers):
    protected = [
        ("/api/auth/me", "GET"),
        ("/api/report", "GET"),
        ("/api/report/vehicles/update", "POST"),
        ("/api/report/pay", "POST"),
        ("/api/auth/customer-cards", "GET"),
        ("/api/user-vehicles", "GET"),
        ("/api/weight-groups", "GET"),
        ("/api/report/filtered-info", "GET"),
    ]
    for endpoint, method in protected:
        response = make_request(method, f"{base_url}{endpoint}", headers=headers, timeout=30)
        assert response.status_code in (200, 401, 403), \
            f"Unexpected status for {endpoint}: {response.status_code}"
        if response.status_code in (401, 403):
            body = assert_valid_json_response(response)
            assert_contains_any_keys(body, ["errors", "message"])


# =============================================================================
# REPORT MANAGEMENT TESTS
# =============================================================================

def test_report_create(base_url, auth_headers):
    case = tc("report_management", "positive_report_create")
    response = _do_request(base_url, case, auth_headers)
    body = _assert_case(response, case)
    if response.status_code == 200 and isinstance(body, dict):
        report_id = _extract_report_id(body)
        if report_id:
            _set_dynamic_env("REPORT_ID", report_id)
        vehicle_id = _extract_vehicle_id(body)
        if vehicle_id:
            _set_dynamic_env("VEHICLE_ID", vehicle_id)


@pytest.mark.parametrize("case", _cases("report_management", "negative_report_create"))
def test_report_create_negative(base_url, auth_headers, case):
    response = _do_request(base_url, case, auth_headers)
    _assert_case(response, case)


def test_report_show(base_url, auth_headers):
    case = tc("report_management", "positive_report_show")
    _skip_if_missing_env(case)
    response = _do_request(base_url, case, auth_headers)
    body = _assert_case(response, case)
    if isinstance(body, dict):
        vehicle_id = _extract_vehicle_id(body)
        if vehicle_id:
            _set_dynamic_env("VEHICLE_ID", vehicle_id)


def test_report_show_invalid_id(base_url, auth_headers):
    case = tc("report_management", "negative_report_show_invalid_id")
    response = _do_request(base_url, case, auth_headers)
    _assert_case(response, case)


def test_report_show_nonexistent_ids(base_url, auth_headers):
    for report_id in ("0", "-1", "999999999", "abc"):
        url = f"{base_url}/api/report/show/{report_id}"
        response = make_request("GET", url, headers=auth_headers, timeout=30)
        assert response.status_code in (400, 403, 404, 422), \
            f"Expected error for ID '{report_id}', got {response.status_code}"
        body = assert_valid_json_response(response)
        assert_contains_any_keys(body, ["errors", "message"])


def test_get_reports(base_url, auth_headers):
    case = tc("report_management", "positive_get_reports")
    response = _do_request(base_url, case, auth_headers)
    _assert_case(response, case)


def test_get_reports_invalid_params(base_url, auth_headers):
    case = tc("report_management", "negative_get_reports_invalid_params")
    response = _do_request(base_url, case, auth_headers)
    _assert_case(response, case)


def test_get_reports_pagination_edge_cases(base_url, auth_headers):
    edge_cases = [
        {"page": "1"},
        {"per_page": "10"},
        {"per_page": "1000"},
        {"sort_by": ""},
        {"sort_dir": ""},
    ]
    for params in edge_cases:
        response = make_request("GET", f"{base_url}/api/report",
                                headers=auth_headers, params=params, timeout=30)
        assert response.status_code == 200, \
            f"Unexpected status for params {params}: {response.status_code}"
        body = assert_valid_json_response(response)
        assert_matches_schema(body, LIST_RESPONSE_SCHEMA)


def test_report_filtered_count(base_url, auth_headers):
    case = tc("report_management", "positive_report_filtered_count")
    response = _do_request(base_url, case, auth_headers)
    _assert_case(response, case)


def test_report_filtered_count_unauthorized(base_url, headers):
    case = tc("report_management", "negative_report_filtered_count_unauthorized")
    response = _do_request(base_url, case, headers)
    _assert_case(response, case)


# =============================================================================
# VEHICLE OPERATIONS TESTS
# =============================================================================

def test_vehicle_update(base_url, auth_headers):
    case = tc("vehicle_operations", "positive_vehicle_update")
    _skip_if_missing_env(case)
    response = _do_request(base_url, case, auth_headers)
    body = _assert_case(response, case)
    if response.status_code == 200 and isinstance(body, dict):
        vehicle_id = _extract_vehicle_id(body)
        if vehicle_id:
            _set_dynamic_env("VEHICLE_ID", vehicle_id)


def test_vehicle_update_multiple(base_url, auth_headers):
    case = tc("vehicle_operations", "positive_vehicle_update_multiple")
    _skip_if_missing_env(case)
    response = _do_request(base_url, case, auth_headers)
    body = _assert_case(response, case)
    if response.status_code == 200 and isinstance(body, dict):
        vehicle_id = _extract_vehicle_id(body)
        if vehicle_id:
            _set_dynamic_env("VEHICLE_ID", vehicle_id)


@pytest.mark.parametrize("case", _cases("vehicle_operations", "negative_vehicle"))
def test_vehicle_negative(base_url, auth_headers, case):
    _skip_if_missing_env(case)
    response = _do_request(base_url, case, auth_headers)
    _assert_case(response, case)


def test_vehicle_remove(base_url, auth_headers):
    case = tc("vehicle_operations", "positive_vehicle_remove")
    _skip_if_missing_env(case)
    response = _do_request(base_url, case, auth_headers)
    _assert_case(response, case)


def test_vehicle_remove_nonexistent_ids(base_url, auth_headers):
    for vehicle_id in ("0", "-1", "999999999", "abc"):
        url = f"{base_url}/api/report/vehicles/remove/{vehicle_id}"
        response = make_request("POST", url, headers=auth_headers, timeout=30)
        assert response.status_code in (400, 403, 404, 422), \
            f"Expected error for ID '{vehicle_id}', got {response.status_code}"
        body = assert_valid_json_response(response)
        assert_contains_any_keys(body, ["errors", "message"])


# =============================================================================
# PAYMENT PROCESSING TESTS
# =============================================================================

def test_report_pay(base_url, auth_headers):
    case = tc("payment_processing", "positive_report_pay")
    _skip_if_missing_env(case)
    payload = _resolve(case["payload"])
    # This endpoint requires report_id as integer
    if payload and payload.get("report_id") is not None:
        try:
            payload["report_id"] = int(payload["report_id"])
        except (TypeError, ValueError):
            pass
    hdrs = {**auth_headers, "Content-Type": "application/json"}
    response = make_request("POST", f"{base_url}{case['endpoint']}",
                            headers=hdrs, json=payload, timeout=30)
    _assert_case(response, case)


@pytest.mark.parametrize("case", _cases("payment_processing", "negative_"))
def test_report_pay_negative(base_url, auth_headers, case):
    _skip_if_missing_env(case)
    response = _do_request(base_url, case, auth_headers)
    _assert_case(response, case)


# =============================================================================
# DATA RETRIEVAL TESTS
# =============================================================================

@pytest.mark.parametrize("case", _cases("data_retrieval", "positive_"))
def test_data_retrieval_positive(base_url, headers, auth_headers, case):
    hdrs = auth_headers if case.get("auth_required") else headers
    response = _do_request(base_url, case, hdrs)
    _assert_case(response, case)


@pytest.mark.parametrize("case", _cases("data_retrieval", "negative_"))
def test_data_retrieval_negative(base_url, headers, case):
    response = _do_request(base_url, case, headers)
    _assert_case(response, case)


def test_get_zip_info_malformed(base_url, headers):
    for zip_code in ("", "abc", "123", "123456789", "12-345", "12 34"):
        url = f"{base_url}/api/get-zip-info"
        response = make_request("GET", url, headers=headers,
                                params={"zip": zip_code}, timeout=30)
        assert response.status_code in (200, 400, 404, 422), \
            f"Unexpected status for zip '{zip_code}': {response.status_code}"
        body = assert_valid_json_response(response)
        if response.status_code != 200:
            assert_contains_any_keys(body, ["errors", "message"])


# =============================================================================
# CALCULATION SERVICES TESTS
# =============================================================================

@pytest.mark.parametrize("case", _cases("calculation_services", "positive_"))
def test_calculate_positive(base_url, auth_headers, case):
    response = _do_request(base_url, case, auth_headers)
    _assert_case(response, case)


@pytest.mark.parametrize("case", _cases("calculation_services", "negative_"))
def test_calculate_negative(base_url, auth_headers, case):
    response = _do_request(base_url, case, auth_headers)
    _assert_case(response, case)


# =============================================================================
# ROBUSTNESS / ADVANCED TESTS
# =============================================================================

def test_malformed_json_requests(base_url, auth_headers):
    endpoints = ["/api/report", "/api/report/vehicles/update", "/api/report/pay"]
    malformed = ["{invalid json", '{"incomplete": "json"', "null", "[]", '"string"', "123"]
    for endpoint in endpoints:
        for body_str in malformed:
            hdrs = {**auth_headers, "Content-Type": "application/json"}
            try:
                response = make_request("POST", f"{base_url}{endpoint}",
                                        headers=hdrs, data=body_str, timeout=30)
                assert response.status_code in (400, 422, 429), \
                    f"Expected 400/422 for malformed JSON at {endpoint}, got {response.status_code}"
            except requests.exceptions.RequestException:
                pass  # connection-level errors are acceptable for malformed input


def test_large_request_payload(base_url, auth_headers):
    vehicles = [
        {
            "license_plate": f"PLATE{i:03d}",
            "vin": f"VIN{i:017d}",
            "state": 1,
            "unit_number": f"UNIT{i:03d}",
            "make": "TEST",
            "declared_weight": 5,
            "taxable_miles": 1000,
            "axles_count": 2,
        }
        for i in range(100)
    ]
    payload = {
        "business_name": "Test Business",
        "phone": "(555) 123-4567",
        "mailing_address": "123 Test St",
        "mailing_city": "Test City",
        "mailing_state": 1,
        "mailing_zip_code": "12345-6789",
        "physical_address": "456 Physical St",
        "physical_city": "Physical City",
        "physical_state": 1,
        "physical_zip_code": "98765-4321",
        "filing_type": "monthly",
        "filing_month": 1,
        "filing_year": "2024",
        "vehicles": vehicles,
    }
    hdrs = {**auth_headers, "Content-Type": "application/json"}
    response = make_request("POST", f"{base_url}/api/report",
                            headers=hdrs, json=payload, timeout=60)
    assert response.status_code in (200, 413, 422), \
        f"Unexpected status for large payload: {response.status_code}"
    if response.status_code != 413:
        body = assert_valid_json_response(response)
        if response.status_code == 200:
            assert_matches_schema(body, GENERAL_RESPONSE_SCHEMA)
        else:
            assert_contains_any_keys(body, ["errors", "message"])


def test_special_characters_and_unicode(base_url, auth_headers):
    payload = {
        "business_name": "Test Business ñáéíóú",
        "phone": "(555) 123-4567",
        "mailing_address": "123 Test St & Ave #5",
        "mailing_city": "Test City",
        "mailing_state": 1,
        "mailing_zip_code": "12345-6789",
        "physical_address": "456 Physical St",
        "physical_city": "Physical City",
        "physical_state": 1,
        "physical_zip_code": "98765-4321",
        "filing_type": "monthly",
        "filing_month": 1,
        "filing_year": "2024",
        "vehicles": [
            {
                "license_plate": "ABC-123",
                "vin": "1HGCM82633A123456",
                "state": 1,
                "unit_number": "UNIT/001",
                "make": "TOYOTA",
                "declared_weight": 5,
                "taxable_miles": 1000,
                "axles_count": 2,
            }
        ],
    }
    hdrs = {**auth_headers, "Content-Type": "application/json"}
    response = make_request("POST", f"{base_url}/api/report",
                            headers=hdrs, json=payload, timeout=30)
    assert response.status_code in (200, 422), \
        f"Unexpected status code: {response.status_code}"
    body = assert_valid_json_response(response)
    if response.status_code == 200:
        assert_matches_schema(body, GENERAL_RESPONSE_SCHEMA)
    else:
        assert_contains_any_keys(body, ["errors", "message"])


def test_session_persistence(base_url, headers):
    for i in range(3):
        response = make_request("GET", f"{base_url}/api/states",
                                headers=headers, timeout=30)
        assert response.status_code == 200, \
            f"Session persistence failed on request {i + 1}"
        body = assert_valid_json_response(response)
        assert_matches_schema(body, LIST_RESPONSE_SCHEMA)
        assert len(body.get("data", [])) > 0, "Expected non-empty states list"


def test_data_consistency_across_endpoints(base_url, auth_headers):
    states_resp = make_request("GET", f"{base_url}/api/states",
                               headers=auth_headers, timeout=30)
    assert states_resp.status_code == 200
    states_data = assert_valid_json_response(states_resp).get("data", [])
    valid_state_ids = {s.get("id") for s in states_data if isinstance(s, dict)}

    reports_resp = make_request("GET", f"{base_url}/api/report",
                                headers=auth_headers, params={"page": "1"}, timeout=30)
    if reports_resp.status_code != 200:
        return
    reports_data = assert_valid_json_response(reports_resp).get("data", [])
    state_ids_in_reports = {
        report[key]
        for report in reports_data if isinstance(report, dict)
        for key in report
        if "state" in key.lower() and isinstance(report[key], int)
    }
    if state_ids_in_reports and valid_state_ids:
        invalid = state_ids_in_reports - valid_state_ids
        assert not invalid, f"Found invalid state IDs in reports: {invalid}"


# =============================================================================
# LOGOUT TESTS — kept last so the token remains valid for all preceding tests
# =============================================================================

def test_logout_unauthorized(base_url, headers):
    case = tc("authorization", "negative_logout_unauthorized")
    response = _do_request(base_url, case, headers)
    _assert_case(response, case)


def test_logout(base_url, auth_headers):
    case = tc("authorization", "positive_logout")
    response = _do_request(base_url, case, auth_headers)
    _assert_case(response, case)

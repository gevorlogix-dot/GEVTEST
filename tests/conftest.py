import json
import os
from pathlib import Path

import pytest
import requests

TC_PATH = Path(__file__).parent / "test_cases.json"


def _load_dotenv_file():
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = map(str.strip, line.split("=", 1))
        if key and value and key not in os.environ:
            os.environ[key] = value


def _save_env_variable(key, value):
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        env_path.write_text("\n")

    lines = env_path.read_text().splitlines()
    normalized_lines = []
    found = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            normalized_lines.append(f"{key}={value}")
            found = True
        else:
            normalized_lines.append(line)

    if not found:
        normalized_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(normalized_lines).rstrip("\n") + "\n")


def _validate_token(base_url, headers, token):
    if not token:
        return False

    response = requests.get(
        f"{base_url}/api/auth/me",
        headers={**headers, "Authorization": f"Bearer {token}"},
        timeout=20,
    )
    return response.status_code == 200


_load_dotenv_file()


@pytest.fixture(scope="session")
def test_cases() -> dict:
    return json.loads(TC_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def base_url():
    url = os.getenv("OREGON_BASE_URL")
    if not url:
        pytest.skip("Set OREGON_BASE_URL to run Oregon API tests")

    normalized = url.rstrip("/")
    if normalized.endswith("/api"):
        normalized = normalized[: -len("/api")]
    return normalized


@pytest.fixture(scope="session")
def headers():
    return {
        "Accept": "application/json",
    }


@pytest.fixture(scope="session")
def auth_token(base_url, headers):
    token = os.getenv("OREGON_AUTH_TOKEN")
    if token and _validate_token(base_url, headers, token):
        return token

    if token:
        os.environ.pop("OREGON_AUTH_TOKEN", None)

    email = os.getenv("OREGON_AUTH_EMAIL")
    password = os.getenv("OREGON_AUTH_PASSWORD")
    if email and password:
        response = requests.post(
            f"{base_url}/api/auth/login",
            headers=headers,
            data={"email": email, "password": password, "remember_me": "0"},
            timeout=20,
        )
        if response.status_code == 200:
            data = response.json()
            token = data.get("token") or data.get("access_token") or data.get("data", {}).get("token")
            if token:
                os.environ["OREGON_AUTH_TOKEN"] = token
                _save_env_variable("OREGON_AUTH_TOKEN", token)
                return token

    pytest.skip("Set OREGON_AUTH_TOKEN or OREGON_AUTH_EMAIL and OREGON_AUTH_PASSWORD to run authenticated tests")


@pytest.fixture(scope="session")
def auth_headers(auth_token, headers):
    auth = headers.copy()
    auth["Authorization"] = f"Bearer {auth_token}"
    return auth

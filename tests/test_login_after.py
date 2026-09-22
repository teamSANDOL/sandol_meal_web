"""Security and callback tests for post-login return destinations."""

from typing import Any
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

import main
from app.routers import auth
from app.services.session_service import (
    create_login_state,
    pop_login_state,
    safe_login_after,
)


def test_login_stores_only_safe_internal_return_path(monkeypatch: Any) -> None:
    """Login state keeps a same-app target and discards redirect payloads."""
    captured: dict[str, str] = {}

    def fake_authorization_url(**kwargs: str) -> str:
        captured["state"] = kwargs["state"]
        return "https://keycloak.example/authorize"

    monkeypatch.setattr(auth, "build_authorization_url", fake_authorization_url)
    with TestClient(main.app, follow_redirects=False) as client:
        response = client.get(
            "/auth/login?login_after=/meal-web/owner/restaurants%3Ftab%3Dmenus"
        )

    assert response.status_code == 302
    login_state = pop_login_state(captured["state"])
    assert login_state is not None
    assert login_state["login_after"] == "/meal-web/owner/restaurants?tab=menus"


def test_login_discards_unsafe_return_paths(monkeypatch: Any) -> None:
    """External, protocol-relative, encoded, and backslash paths are rejected."""
    monkeypatch.setattr(
        auth,
        "build_authorization_url",
        lambda **kwargs: f"https://keycloak.example/?state={kwargs['state']}",
    )
    unsafe_values = [
        "https://attacker.example/meal-web/admin",
        "//attacker.example/meal-web/admin",
        "/meal-web/%2e%2e/admin",
        "/meal-web/%252e%252e/admin",
        "/meal-web%5c%5cattacker.example",
        "/meal-web/admin%0d%0aLocation:%20https://attacker.example",
        "/meal-web/auth/login",
    ]

    with TestClient(main.app, follow_redirects=False) as client:
        for value in unsafe_values:
            response = client.get("/auth/login", params={"login_after": value})
            state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
            login_state = pop_login_state(state)
            assert login_state is not None
            assert login_state["login_after"] is None


def test_callback_returns_to_allowed_destination(monkeypatch: Any) -> None:
    """Successful login consumes the state and returns to its safe destination."""
    state = create_login_state(
        nonce="nonce",
        code_verifier="verifier",
        login_after="/meal-web/owner/restaurants?tab=menus",
    )
    _mock_callback(monkeypatch, roles=[])

    with TestClient(main.app, follow_redirects=False) as client:
        response = client.get(f"/auth/callback?code=code&state={state}")

    assert response.status_code == 302
    assert response.headers["location"] == "/meal-web/owner/restaurants?tab=menus"


def test_callback_falls_back_when_return_path_requires_missing_role(
    monkeypatch: Any,
) -> None:
    """A non-admin cannot be redirected into an admin forbidden-page loop."""
    state = create_login_state(
        nonce="nonce",
        code_verifier="verifier",
        login_after="/meal-web/admin/requests",
    )
    _mock_callback(monkeypatch, roles=[])

    with TestClient(main.app, follow_redirects=False) as client:
        response = client.get(f"/auth/callback?code=code&state={state}")

    assert response.status_code == 302
    assert response.headers["location"].endswith("/meal-web/owner/restaurants")


def test_callback_checks_roles_against_encoded_destination(monkeypatch: Any) -> None:
    """Encoded admin paths cannot bypass the role-compatible landing fallback."""
    state = create_login_state(
        nonce="nonce",
        code_verifier="verifier",
        login_after="/meal-web/%61dmin/requests",
    )
    _mock_callback(monkeypatch, roles=[])

    with TestClient(main.app, follow_redirects=False) as client:
        response = client.get(f"/auth/callback?code=code&state={state}")

    assert response.status_code == 302
    assert response.headers["location"].endswith("/meal-web/owner/restaurants")


def test_safe_login_after_rejects_external_and_path_escape_values() -> None:
    """The validator defends the callback even if cached state is tampered with."""
    assert safe_login_after("/meal-web/admin?tab=users") == "/meal-web/admin?tab=users"
    assert safe_login_after("https://attacker.example") is None
    assert safe_login_after("//attacker.example") is None
    assert safe_login_after("/meal-web/%2e%2e/admin") is None
    assert safe_login_after("/meal-web/%252e%252e/admin") is None
    assert safe_login_after("/meal-web\\attacker.example") is None
    assert safe_login_after("/meal-web/auth/callback") is None


def _mock_callback(monkeypatch: Any, *, roles: list[str]) -> None:
    """Replace Keycloak I/O while retaining the callback's session logic."""
    monkeypatch.setattr(auth, "exchange_code_for_token", lambda **_kwargs: {})
    monkeypatch.setattr(
        auth,
        "claims_from_token_response",
        lambda *_args, **_kwargs: {"sub": "test-user"},
    )
    monkeypatch.setattr(auth, "extract_roles", lambda _claims: roles)
    monkeypatch.setattr(auth, "session_expiry_from_token", lambda _token: 2_000_000_000)
    monkeypatch.setattr(auth, "now_ts", lambda: 1_900_000_000)

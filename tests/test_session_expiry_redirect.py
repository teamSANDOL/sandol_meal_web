"""Regression tests for expired browser sessions."""

from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

import main


def test_expired_htmx_session_redirects_the_whole_browser() -> None:
    """Protected HTMX navigation must use the HTMX full-page redirect header."""
    with TestClient(main.app, follow_redirects=False) as client:
        response = client.get(
            "/admin",
            headers={"Accept": "text/html", "HX-Request": "true"},
        )

    assert response.status_code == 200
    login_url = urlsplit(response.headers["HX-Redirect"])
    assert login_url.path.endswith("/meal-web/auth/login")
    assert parse_qs(login_url.query) == {"login_after": ["/meal-web/admin"]}
    assert "location" not in response.headers


def test_expired_standard_navigation_uses_http_redirect() -> None:
    """Non-HTMX page navigation must retain normal browser redirects."""
    with TestClient(main.app, follow_redirects=False) as client:
        response = client.get("/admin", headers={"Accept": "text/html"})

    assert response.status_code == 302
    login_url = urlsplit(response.headers["location"])
    assert login_url.path.endswith("/meal-web/auth/login")
    assert parse_qs(login_url.query) == {"login_after": ["/meal-web/admin"]}


def test_expired_session_preserves_the_original_query_in_login_after() -> None:
    """A login round trip must retain the protected page's query string."""
    with TestClient(main.app, follow_redirects=False) as client:
        response = client.get(
            "/admin/requests?status=pending&page=2",
            headers={"Accept": "text/html"},
        )

    login_url = urlsplit(response.headers["location"])
    assert parse_qs(login_url.query) == {
        "login_after": ["/meal-web/admin/requests?status=pending&page=2"]
    }

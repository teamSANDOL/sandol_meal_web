"""Regression tests for expired browser sessions."""

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
    assert response.headers["HX-Redirect"].endswith("/meal-web/auth/login")
    assert "location" not in response.headers


def test_expired_standard_navigation_uses_http_redirect() -> None:
    """Non-HTMX page navigation must retain normal browser redirects."""
    with TestClient(main.app, follow_redirects=False) as client:
        response = client.get("/admin", headers={"Accept": "text/html"})

    assert response.status_code == 302
    assert response.headers["location"].endswith("/meal-web/auth/login")

"""Server-side session and CSRF helpers for sandol_meal_web."""

import secrets
from typing import Annotated, Any, Literal, TypedDict, cast

from diskcache import FanoutCache
from fastapi import Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.config import Config
from app.services.keycloak_service import now_ts


class LoginState(TypedDict):
    """Transient OIDC login state."""

    nonce: str
    code_verifier: str
    ts: int


class SessionData(TypedDict):
    """Server-side authenticated browser session."""

    user_id: str
    roles: list[str]
    csrf_token: str
    expires_at: int
    token_metadata: dict[str, Any]


_CACHE = FanoutCache(directory=Config.SESSION_CACHE_DIR, shards=8)
_COOKIE_SAMESITE_VALUES = {"lax", "strict", "none"}


def _cookie_samesite() -> Literal["lax", "strict", "none"]:
    """Return a Starlette-compatible SameSite cookie value."""
    value = Config.COOKIE_SAMESITE.lower()
    if value in _COOKIE_SAMESITE_VALUES:
        return cast(Literal["lax", "strict", "none"], value)
    return "lax"


def create_login_state(*, nonce: str, code_verifier: str) -> str:
    """Store transient OIDC state and return the random state id."""
    state = secrets.token_urlsafe(24)
    data: LoginState = {
        "nonce": nonce,
        "code_verifier": code_verifier,
        "ts": now_ts(),
    }
    _ = _CACHE.set(f"state:{state}", data, expire=Config.STATE_TTL_SECONDS)
    return state


def pop_login_state(state: str) -> LoginState | None:
    """Consume a login state so callback replay fails."""
    data = _CACHE.pop(f"state:{state}", default=None)
    if not isinstance(data, dict):
        return None
    ts = data.get("ts")
    if not isinstance(ts, int) or now_ts() - ts > Config.STATE_TTL_SECONDS:
        return None
    nonce = data.get("nonce")
    code_verifier = data.get("code_verifier")
    if not isinstance(nonce, str) or not isinstance(code_verifier, str):
        return None
    return {"nonce": nonce, "code_verifier": code_verifier, "ts": ts}


def create_session(
    *,
    user_id: str,
    roles: list[str],
    expires_at: int,
    token_metadata: dict[str, Any],
) -> tuple[str, SessionData]:
    """Create a server-side session and return its id plus data."""
    session_id = secrets.token_urlsafe(32)
    session_data: SessionData = {
        "user_id": user_id,
        "roles": roles,
        "csrf_token": secrets.token_urlsafe(32),
        "expires_at": expires_at,
        "token_metadata": token_metadata,
    }
    ttl = max(1, expires_at - now_ts())
    _ = _CACHE.set(f"session:{session_id}", session_data, expire=ttl)
    return session_id, session_data


def get_session(session_id: str | None) -> SessionData | None:
    """Read an unexpired server-side session by cookie id."""
    if not session_id:
        return None
    data = _CACHE.get(f"session:{session_id}", default=None)
    if not isinstance(data, dict):
        return None
    expires_at = data.get("expires_at")
    if not isinstance(expires_at, int) or expires_at <= now_ts():
        _ = _CACHE.delete(f"session:{session_id}")
        return None
    user_id = data.get("user_id")
    roles = data.get("roles")
    csrf_token = data.get("csrf_token")
    token_metadata = data.get("token_metadata")
    if (
        not isinstance(user_id, str)
        or not isinstance(roles, list)
        or not isinstance(csrf_token, str)
        or not isinstance(token_metadata, dict)
    ):
        return None
    return {
        "user_id": user_id,
        "roles": [str(role) for role in roles],
        "csrf_token": csrf_token,
        "expires_at": expires_at,
        "token_metadata": token_metadata,
    }


def delete_session(session_id: str | None) -> None:
    """Delete a server-side session if present."""
    if session_id:
        _ = _CACHE.delete(f"session:{session_id}")


def set_session_cookie(response: Response, session_id: str, max_age: int) -> None:
    """Attach the HttpOnly session-id cookie to a response."""
    response.set_cookie(
        Config.SESSION_COOKIE_NAME,
        session_id,
        max_age=max_age,
        httponly=True,
        secure=Config.COOKIE_SECURE,
        samesite=_cookie_samesite(),
    )


def clear_session_cookie(response: Response) -> None:
    """Clear the browser session cookie."""
    response.delete_cookie(
        Config.SESSION_COOKIE_NAME,
        httponly=True,
        secure=Config.COOKIE_SECURE,
        samesite=_cookie_samesite(),
    )


def get_optional_session(request: Request) -> SessionData | None:
    """Return the current session or None for anonymous requests."""
    session_id = request.cookies.get(Config.SESSION_COOKIE_NAME)
    return get_session(session_id)


def require_session(request: Request) -> SessionData:
    """Require an authenticated session for protected page handlers."""
    session = get_optional_session(request)
    if session is None:
        login_url = request.url_for("login")
        raise HTTPException(
            status_code=Config.HttpStatus.UNAUTHORIZED,
            detail=f"login_required:{login_url}",
        )
    return session


def csrf_token_for_template(session: SessionData | None) -> str:
    """Return the CSRF token value for rendering form templates."""
    if session is None:
        return ""
    return session["csrf_token"]


def has_admin_role(session: SessionData) -> bool:
    """Return whether the current session has a configured admin role."""
    roles = set(session["roles"])
    return bool(
        {Config.REALM_GLOBAL_ADMIN_ROLE, Config.MEAL_CLIENT_ADMIN_ROLE} & roles
    )


def require_admin_session(request: Request) -> SessionData:
    """Require an authenticated session with a configured admin role."""
    session = require_session(request)
    if not has_admin_role(session):
        raise HTTPException(Config.HttpStatus.FORBIDDEN, "admin_role_required")
    return session


def require_csrf(
    request: Request,
    csrf_token: Annotated[str, Form()],
) -> SessionData:
    """Require a valid session-bound CSRF token for POST handlers."""
    session = require_session(request)
    if not secrets.compare_digest(csrf_token, session["csrf_token"]):
        raise HTTPException(Config.HttpStatus.FORBIDDEN, "invalid_csrf_token")
    return session


def redirect_to_login() -> RedirectResponse:
    """Create a standard redirect response to the auth login route."""
    return RedirectResponse("/auth/login", status_code=Config.HttpStatus.FOUND)

"""Authentication routes for sandol_meal_web."""

import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import Config, logger
from app.services.keycloak_service import (
    build_logout_url,
    build_authorization_url,
    claims_from_token_response,
    code_challenge_s256,
    decode_token_claims,
    exchange_code_for_token,
    extract_roles,
    generate_code_verifier,
    now_ts,
    session_expiry_from_token,
    validate_token_claims,
)
from app.services.session_service import (
    clear_session_cookie,
    create_login_state,
    create_session,
    delete_session,
    get_session,
    pop_login_state,
    set_session_cookie,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.get("/login", name="login")
async def login() -> RedirectResponse:
    """Start the Keycloak Authorization Code + PKCE login flow."""
    nonce = secrets.token_urlsafe(24)
    code_verifier = generate_code_verifier()
    code_challenge = code_challenge_s256(code_verifier)
    state = create_login_state(nonce=nonce, code_verifier=code_verifier)
    try:
        authorization_url = build_authorization_url(
            state=state,
            nonce=nonce,
            code_challenge=code_challenge,
        )
    except Exception as exc:
        logger.exception("auth_login: failed to build authorization URL")
        raise HTTPException(
            Config.HttpStatus.INTERNAL_SERVER_ERROR, "keycloak_discovery_failed"
        ) from exc
    logger.info("auth_login: redirecting to Keycloak authorization endpoint")
    return RedirectResponse(authorization_url, status_code=Config.HttpStatus.FOUND)


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    """Handle Keycloak callback, create a server-side session, and redirect home."""
    if not code or not state:
        raise HTTPException(Config.HttpStatus.BAD_REQUEST, "missing_code_or_state")

    login_state = pop_login_state(state)
    if login_state is None:
        raise HTTPException(Config.HttpStatus.BAD_REQUEST, "invalid_or_expired_state")

    try:
        token = exchange_code_for_token(
            code=code,
            code_verifier=login_state["code_verifier"],
        )
        claims = claims_from_token_response(
            token, expected_nonce=login_state["nonce"]
        )
    except Exception as exc:
        logger.warning("auth_callback: token exchange or decoding failed")
        raise HTTPException(
            Config.HttpStatus.BAD_REQUEST, "token_exchange_failed"
        ) from exc

    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(Config.HttpStatus.BAD_REQUEST, "missing_subject")

    role_claims = claims
    access_token = token.get("access_token")
    if isinstance(access_token, str):
        role_claims = validate_token_claims(decode_token_claims(access_token))

    expires_at = session_expiry_from_token(token)
    session_id, _ = create_session(
        user_id=user_id,
        roles=extract_roles(role_claims),
        expires_at=expires_at,
        token_metadata={
            "expires_at": expires_at,
            "id_token": token.get("id_token"),
            "scope": token.get("scope"),
            "token_type": token.get("token_type"),
        },
    )
    response = RedirectResponse(
        str(request.url_for("root")),
        status_code=Config.HttpStatus.FOUND,
    )
    set_session_cookie(response, session_id, max_age=max(1, expires_at - now_ts()))
    logger.info("auth_callback: session created for Keycloak subject")
    return response


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Delete the local session and redirect through Keycloak logout."""
    session_id = request.cookies.get(Config.SESSION_COOKIE_NAME)
    session = get_session(session_id) if session_id else None
    id_token_hint = session["token_metadata"].get("id_token") if session else None
    delete_session(session_id)
    try:
        logout_target = build_logout_url(
            id_token_hint=id_token_hint if isinstance(id_token_hint, str) else None
        )
    except Exception:
        logger.warning("auth_logout: failed to build Keycloak logout URL")
        logout_target = str(request.url_for("root"))
    response = RedirectResponse(logout_target, status_code=Config.HttpStatus.FOUND)
    clear_session_cookie(response)
    return response

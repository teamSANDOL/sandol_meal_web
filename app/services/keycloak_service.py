"""Keycloak OIDC helpers for sandol_meal_web."""

import base64
import hashlib
import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from keycloak import KeycloakOpenID

from app.config import Config


def build_keycloak_client() -> KeycloakOpenID:
    """Create a KeycloakOpenID client from environment configuration."""
    return KeycloakOpenID(
        server_url=Config.KC_SERVER_URL,
        realm_name=Config.KC_REALM,
        client_id=Config.KC_CLIENT_ID,
        client_secret_key=Config.KC_CLIENT_SECRET,
        timeout=10,
    )


def redirect_uri() -> str:
    """Return the public OIDC callback URI registered in Keycloak."""
    return f"{Config.PUBLIC_BASE_URL}{Config.KC_REDIRECT_PATH}"


def post_logout_redirect_uri() -> str:
    """Return the public post-logout URI for Keycloak session logout."""
    return f"{Config.PUBLIC_BASE_URL}/"


def now_ts() -> int:
    """Return current epoch seconds."""
    return int(time.time())


def generate_code_verifier(byte_length: int = 64) -> str:
    """Generate a PKCE code verifier."""
    return base64.urlsafe_b64encode(secrets.token_bytes(byte_length)).decode().rstrip("=")


def code_challenge_s256(code_verifier: str) -> str:
    """Generate the S256 PKCE code challenge for a verifier."""
    return (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )


def build_authorization_url(
    *, state: str, nonce: str, code_challenge: str
) -> str:
    """Build the Keycloak authorization URL with PKCE parameters."""
    keycloak_client = build_keycloak_client()
    well_known = keycloak_client.well_known()
    authorization_endpoint = well_known["authorization_endpoint"]
    params = {
        "client_id": Config.KC_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": Config.KC_SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{authorization_endpoint}?{urlencode(params)}"


def exchange_code_for_token(*, code: str, code_verifier: str) -> dict[str, Any]:
    """Exchange an authorization code for a Keycloak token response."""
    keycloak_client = build_keycloak_client()
    token: dict[str, Any] = keycloak_client.token(
        grant_type="authorization_code",
        code=code,
        redirect_uri=redirect_uri(),
        scope=Config.KC_SCOPE,
        code_verifier=code_verifier,
    )
    return token


def build_logout_url(*, id_token_hint: str | None) -> str:
    """Build the Keycloak end-session URL for RP-initiated logout."""
    keycloak_client = build_keycloak_client()
    well_known = keycloak_client.well_known()
    logout_endpoint = well_known["end_session_endpoint"]
    params = {
        "post_logout_redirect_uri": post_logout_redirect_uri(),
        "client_id": Config.KC_CLIENT_ID,
    }
    if id_token_hint:
        params["id_token_hint"] = id_token_hint
    return f"{logout_endpoint}?{urlencode(params)}"


def decode_token_claims(token: str) -> dict[str, Any]:
    """Decode JWT claims without exposing or logging the token value."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("invalid_token_format")
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    decoded = base64.urlsafe_b64decode(f"{payload}{padding}")
    claims: dict[str, Any] = json.loads(decoded)
    if not isinstance(claims, dict):
        raise ValueError("invalid_token_payload")
    return claims


def _audience_matches_client(audience: Any) -> bool:
    """Return whether an aud claim includes the configured Keycloak client."""
    if isinstance(audience, str):
        return audience == Config.KC_CLIENT_ID
    if isinstance(audience, list):
        return Config.KC_CLIENT_ID in [str(value) for value in audience]
    return False


def validate_token_claims(
    claims: dict[str, Any], *, expected_nonce: str | None = None
) -> dict[str, Any]:
    """Validate decoded OIDC claims enough for the v1 browser session flow."""
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise ValueError("missing_subject")

    expires_at = claims.get("exp")
    if not isinstance(expires_at, int) or expires_at <= now_ts():
        raise ValueError("expired_token")

    issuer = claims.get("iss")
    if Config.KC_ISSUER and issuer != Config.KC_ISSUER:
        raise ValueError("invalid_issuer")

    audience = claims.get("aud")
    authorized_party = claims.get("azp")
    has_client_claim = audience is not None or authorized_party is not None
    if has_client_claim and not (
        _audience_matches_client(audience) or authorized_party == Config.KC_CLIENT_ID
    ):
        raise ValueError("invalid_audience")

    nonce = claims.get("nonce")
    if nonce is not None:
        if expected_nonce is None or nonce != expected_nonce:
            raise ValueError("invalid_nonce")

    return claims


def claims_from_token_response(
    token: dict[str, Any], *, expected_nonce: str
) -> dict[str, Any]:
    """Return validated ID-token claims when present, otherwise access-token claims."""
    raw_token = token.get("id_token") or token.get("access_token")
    if not isinstance(raw_token, str):
        raise ValueError("missing_token")
    return validate_token_claims(
        decode_token_claims(raw_token), expected_nonce=expected_nonce
    )


def extract_roles(claims: dict[str, Any]) -> list[str]:
    """Extract normalized realm and meal-web client roles from token claims."""
    roles: set[str] = set()
    realm_access = claims.get("realm_access", {})
    if isinstance(realm_access, dict):
        realm_roles = realm_access.get("roles", [])
        if isinstance(realm_roles, list):
            roles.update(str(role) for role in realm_roles)

    resource_access = claims.get("resource_access", {})
    if isinstance(resource_access, dict):
        client_access = resource_access.get(Config.KC_CLIENT_ID, {})
        if isinstance(client_access, dict):
            client_roles = client_access.get("roles", [])
            if isinstance(client_roles, list):
                roles.update(str(role) for role in client_roles)

    return sorted(roles)


def session_expiry_from_token(token: dict[str, Any]) -> int:
    """Calculate server-side session expiry from token and configured TTL."""
    raw_token = token.get("id_token") or token.get("access_token")
    if isinstance(raw_token, str):
        claims = decode_token_claims(raw_token)
        expires_at = claims.get("exp")
        if isinstance(expires_at, int):
            return min(expires_at, now_ts() + Config.SESSION_TTL_SECONDS)

    expires_in = token.get("expires_in")
    token_expiry = now_ts() + Config.SESSION_TTL_SECONDS
    if isinstance(expires_in, int) and expires_in > 0:
        token_expiry = now_ts() + expires_in
    return min(token_expiry, now_ts() + Config.SESSION_TTL_SECONDS)

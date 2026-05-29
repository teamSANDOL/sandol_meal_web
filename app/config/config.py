"""Configuration and logging for sandol_meal_web."""

import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

_ = load_dotenv()

CONFIG_DIR = Path(__file__).resolve().parent
SERVICE_DIR = CONFIG_DIR.parent.parent
TEMPLATE_DIR = SERVICE_DIR / "app" / "templates"
STATIC_DIR = SERVICE_DIR / "app" / "static"
TMP_DIR = SERVICE_DIR / "tmp"
_ = TMP_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("sandol_meal_web")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    console_handler = logging.StreamHandler()
    if os.getenv("DEBUG", "False").lower() == "true":
        console_handler.setLevel(logging.DEBUG)
    else:
        console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(console_handler)


class Config:
    """Application configuration values."""

    debug: bool = os.getenv("DEBUG", "False").lower() == "true"
    TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Seoul")
    TZ: ZoneInfo = ZoneInfo(TIMEZONE)

    SERVICE_DIR: Path = SERVICE_DIR
    TEMPLATE_DIR: Path = TEMPLATE_DIR
    STATIC_DIR: Path = STATIC_DIR
    TMP_DIR: Path = TMP_DIR

    PUBLIC_BASE_URL: str = os.getenv(
        "MEAL_WEB_PUBLIC_BASE_URL", "https://sandori.kr/meal-web"
    ).rstrip("/")
    SESSION_COOKIE_NAME: str = os.getenv(
        "MEAL_WEB_SESSION_COOKIE_NAME", "meal_web_session"
    )
    SESSION_TTL_SECONDS: int = int(
        os.getenv("MEAL_WEB_SESSION_TTL_SECONDS", "3600")
    )
    STATE_TTL_SECONDS: int = int(os.getenv("MEAL_WEB_STATE_TTL_SECONDS", "600"))
    SESSION_CACHE_DIR: str = os.getenv(
        "MEAL_WEB_SESSION_CACHE_DIR", str(SERVICE_DIR / ".cache" / "sessions")
    )
    COOKIE_SECURE: bool = (
        os.getenv("MEAL_WEB_COOKIE_SECURE", "False").lower() == "true"
    )
    COOKIE_SAMESITE: str = os.getenv("MEAL_WEB_COOKIE_SAMESITE", "lax")

    MEAL_SERVICE_BASE_URL: str = os.getenv(
        "MEAL_SERVICE_BASE_URL", "http://meal-service:80"
    ).rstrip("/")
    MEAL_SERVICE_TIMEOUT_SECONDS: float = float(
        os.getenv("MEAL_SERVICE_TIMEOUT_SECONDS", "10")
    )

    KC_SERVER_URL: str = os.getenv("KC_SERVER_URL", "https://sandori.kr/auth/")
    KC_REALM: str = os.getenv("KC_REALM", "Sandori")
    KC_CLIENT_ID: str = os.getenv("KC_CLIENT_ID", "sandol-meal-service")
    KC_CLIENT_SECRET: str = os.getenv(
        "KC_CLIENT_SECRET", "your-meal-service-client-secret"
    )
    KC_ISSUER: str = os.getenv(
        "KC_ISSUER", f"{KC_SERVER_URL.rstrip('/')}/realms/{KC_REALM}"
    ).rstrip("/")
    KC_REDIRECT_PATH: str = os.getenv("KC_REDIRECT_PATH", "/auth/callback")
    KC_SCOPE: str = os.getenv("KC_SCOPE", "openid profile email")

    REALM_GLOBAL_ADMIN_ROLE: str = os.getenv(
        "REALM_GLOBAL_ADMIN_ROLE", "global_admin"
    )
    MEAL_CLIENT_ADMIN_ROLE: str = os.getenv("MEAL_CLIENT_ADMIN_ROLE", "meal_admin")

    class HttpStatus:
        """HTTP status codes used in this service."""

        OK: int = 200
        FOUND: int = 302
        BAD_REQUEST: int = 400
        UNAUTHORIZED: int = 401
        FORBIDDEN: int = 403
        NOT_FOUND: int = 404
        INTERNAL_SERVER_ERROR: int = 500

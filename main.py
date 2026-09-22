"""sandol_meal_web FastAPI entrypoint."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import Config, logger
from app.routers import admin_router, auth_router, owner_router, uploader_router
from app.services.session_service import (
    csrf_token_for_template,
    get_optional_session,
    navigation_context,
)

app = FastAPI(root_path="/meal-web")
templates = Jinja2Templates(directory=str(Config.TEMPLATE_DIR))

app.mount(
    "/static",
    StaticFiles(directory=str(Config.STATIC_DIR)),
    name="static",
)

app.include_router(auth_router)
app.include_router(owner_router)
app.include_router(admin_router)
app.include_router(uploader_router)

#: Titles and copy for the error page, keyed by status code.
_ERROR_COPY: dict[int, tuple[str, str]] = {
    Config.HttpStatus.BAD_REQUEST: (
        "요청을 처리할 수 없어요",
        "입력값을 다시 확인한 뒤 시도해주세요.",
    ),
    Config.HttpStatus.FORBIDDEN: (
        "접근 권한이 없습니다",
        "이 기능을 사용할 수 있는 권한이 없습니다.",
    ),
    Config.HttpStatus.NOT_FOUND: (
        "찾을 수 없는 페이지예요",
        "주소가 바뀌었거나 삭제된 항목일 수 있어요.",
    ),
    Config.HttpStatus.INTERNAL_SERVER_ERROR: (
        "문제가 생겼어요",
        "잠시 후 다시 시도해주세요. 계속되면 운영진에게 알려주세요.",
    ),
}


def _prefers_html(request: Request) -> bool:
    """Return whether this request expects an HTML response."""
    accept = request.headers.get("accept", "")
    return (
        "text/html" in accept or request.headers.get("hx-request", "").lower() == "true"
    )


def _public_context(request: Request) -> dict[str, object]:
    """Build template context that works with or without a session."""
    session = get_optional_session(request)
    return {
        "request": request,
        "session": session,
        "csrf_token": csrf_token_for_template(session),
        **navigation_context(request, session),
    }


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Render browser-friendly error pages, falling back to JSON for API calls."""
    if not _prefers_html(request):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    detail = exc.detail if isinstance(exc.detail, str) else ""
    if exc.status_code == Config.HttpStatus.UNAUTHORIZED and detail.startswith(
        "login_required:"
    ):
        login_url = detail.removeprefix("login_required:")
        if request.headers.get("hx-request", "").lower() == "true":
            # A normal 302 is followed inside HTMX's XMLHttpRequest, which does
            # not navigate the full browser page to the Keycloak login flow.
            return Response(
                status_code=Config.HttpStatus.OK,
                headers={"HX-Redirect": login_url},
            )
        return RedirectResponse(
            login_url,
            status_code=Config.HttpStatus.FOUND,
        )

    title, message = _ERROR_COPY.get(
        exc.status_code,
        ("문제가 생겼어요", "요청을 처리하지 못했어요. 잠시 후 다시 시도해주세요."),
    )
    if detail == "invalid_csrf_token":
        title = "요청을 다시 시도해주세요"
        message = "보안 검증에 실패했습니다. 페이지를 새로고침한 뒤 다시 시도해주세요."

    return templates.TemplateResponse(
        request,
        "error.html",
        {
            **_public_context(request),
            "error_code": exc.status_code,
            "error_title": title,
            "error_message": message,
        },
        status_code=exc.status_code,
    )


@app.get("/", response_class=HTMLResponse, name="root")
async def root(request: Request) -> HTMLResponse:
    """Render the public landing page."""
    logger.info("Meal web root endpoint accessed")
    return templates.TemplateResponse(request, "root.html", _public_context(request))


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Return health information."""
    return {"status": "ok"}

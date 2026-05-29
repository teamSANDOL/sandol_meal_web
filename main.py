"""sandol_meal_web FastAPI entrypoint."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import Config, logger
from app.routers import admin_router, auth_router, owner_router
from app.services.session_service import get_optional_session

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


def _prefers_html(request: Request) -> bool:
    """Return whether this request expects an HTML response."""
    accept = request.headers.get("accept", "")
    return (
        "text/html" in accept
        or request.headers.get("hx-request", "").lower() == "true"
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Return HTML-friendly auth/authorization responses for browser requests."""
    if not _prefers_html(request):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    detail = exc.detail if isinstance(exc.detail, str) else ""
    if exc.status_code == Config.HttpStatus.UNAUTHORIZED and detail.startswith(
        "login_required:"
    ):
        return RedirectResponse(
            detail.removeprefix("login_required:"),
            status_code=Config.HttpStatus.FOUND,
        )

    if exc.status_code == Config.HttpStatus.FORBIDDEN:
        title = "접근 권한이 없습니다"
        message = "이 기능을 사용할 수 있는 권한이 없습니다."
        if detail == "invalid_csrf_token":
            title = "요청을 다시 시도해주세요"
            message = "보안 검증에 실패했습니다. 페이지를 새로고침한 뒤 다시 시도해주세요."
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "request": request,
                "session": get_optional_session(request),
                "error_code": exc.status_code,
                "error_title": title,
                "error_message": message,
            },
            status_code=exc.status_code,
        )

    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/", response_class=HTMLResponse, name="root")
async def root(request: Request) -> HTMLResponse:
    """Render a landing page with owner/admin shortcuts."""
    logger.info("Meal web root endpoint accessed")
    session = get_optional_session(request)
    return templates.TemplateResponse(
        request,
        "root.html",
        {"request": request, "session": session},
    )


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Return health information."""
    return {"status": "ok"}

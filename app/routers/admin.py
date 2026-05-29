"""Admin workflow routes for sandol_meal_web."""

import secrets
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import Config
from app.services.meal_client import MealServiceError, meal_service_client
from app.services.session_service import (
    SessionData,
    csrf_token_for_template,
    get_optional_session,
    has_admin_role,
    require_admin_session,
)

router = APIRouter(prefix="/admin", tags=["Admin"])
templates = Jinja2Templates(directory=str(Config.TEMPLATE_DIR))


def _response_data(data: dict[str, Any]) -> dict[str, Any]:
    """Unwrap meal-service response envelopes when present."""
    nested_data = data.get("data")
    if isinstance(nested_data, dict):
        return nested_data
    return data


def _request_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract request objects from common meal-service list response shapes."""
    response_data = _response_data(data)
    items = response_data.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    data_items = data.get("data")
    if isinstance(data_items, list):
        return [item for item in data_items if isinstance(item, dict)]
    if "id" in response_data or "request_id" in response_data:
        return [response_data]
    return []


def _template_context(
    request: Request,
    session: SessionData,
    **extra: Any,
) -> dict[str, Any]:
    """Build common template context for authenticated admin pages."""
    context: dict[str, Any] = {
        "request": request,
        "session": session,
        "csrf_token": csrf_token_for_template(session),
    }
    context.update(extra)
    return context


def _render(
    request: Request,
    session: SessionData,
    template_name: str,
    status_code: int = Config.HttpStatus.OK,
    **extra: Any,
) -> HTMLResponse:
    """Render an admin template with shared context."""
    return templates.TemplateResponse(
        request,
        template_name,
        _template_context(request, session, **extra),
        status_code=status_code,
    )


def _url_with_message(
    request: Request,
    route_name: str,
    message: str,
    **path_params: Any,
) -> str:
    """Build a root-path-aware URL with a plain status message."""
    url = str(request.url_for(route_name, **path_params))
    return f"{url}?{urlencode({'message': message})}"


def _get_admin_page_session(request: Request) -> SessionData | RedirectResponse:
    """Return an admin session for GET pages or redirect anonymous users to login."""
    session = get_optional_session(request)
    if session is None:
        return RedirectResponse(
            str(request.url_for("login")),
            status_code=Config.HttpStatus.FOUND,
        )
    if not has_admin_role(session):
        raise HTTPException(Config.HttpStatus.FORBIDDEN, "admin_role_required")
    return session


async def _require_admin_post_session(
    request: Request,
) -> tuple[SessionData, dict[str, Any]]:
    """Require an admin session and valid CSRF token for admin POST forms."""
    session = require_admin_session(request)
    form = await request.form()
    csrf_token = form.get("csrf_token")
    if not isinstance(csrf_token, str) or not secrets.compare_digest(
        csrf_token,
        session["csrf_token"],
    ):
        raise HTTPException(Config.HttpStatus.FORBIDDEN, "invalid_csrf_token")
    return session, dict(form)


@router.get("/requests", response_class=HTMLResponse, name="admin_requests_page")
async def admin_requests_page(request: Request, message: str | None = None) -> Response:
    """Render all restaurant submission requests visible to an admin."""
    session_or_redirect = _get_admin_page_session(request)
    if isinstance(session_or_redirect, RedirectResponse):
        return session_or_redirect

    session = session_or_redirect
    error_message = None
    requests: list[dict[str, Any]] = []
    try:
        data = await meal_service_client.list_requests(user_id=session["user_id"])
        requests = _request_items(data)
    except MealServiceError as exc:
        error_message = exc.message

    return _render(
        request,
        session,
        "admin/requests.html",
        requests=requests,
        error_message=error_message,
        success_message=message,
    )


@router.get(
    "/requests/{request_id}",
    response_class=HTMLResponse,
    name="admin_request_detail_page",
)
async def admin_request_detail_page(
    request: Request,
    request_id: int,
    message: str | None = None,
) -> Response:
    """Render a single restaurant submission request for admin review."""
    session_or_redirect = _get_admin_page_session(request)
    if isinstance(session_or_redirect, RedirectResponse):
        return session_or_redirect

    session = session_or_redirect
    error_message = None
    restaurant_request: dict[str, Any] | None = None
    try:
        data = await meal_service_client.get_request_detail(
            user_id=session["user_id"],
            request_id=request_id,
        )
        restaurant_request = _response_data(data)
    except MealServiceError as exc:
        error_message = exc.message

    return _render(
        request,
        session,
        "admin/request_detail.html",
        restaurant_request=restaurant_request,
        request_id=request_id,
        error_message=error_message,
        success_message=message,
    )


@router.post(
    "/requests/{request_id}/approve",
    response_class=HTMLResponse,
    name="approve_admin_request",
)
async def approve_admin_request(request: Request, request_id: int) -> Response:
    """Approve a restaurant submission request after admin and CSRF checks."""
    session, _ = await _require_admin_post_session(request)
    try:
        _ = await meal_service_client.approve_request(
            user_id=session["user_id"],
            request_id=request_id,
        )
    except MealServiceError as exc:
        return _render(
            request,
            session,
            "admin/request_detail.html",
            status_code=exc.status_code,
            restaurant_request=None,
            request_id=request_id,
            error_message=exc.message,
            success_message=None,
        )
    return RedirectResponse(
        _url_with_message(
            request,
            "admin_request_detail_page",
            "등록 요청을 승인했습니다.",
            request_id=request_id,
        ),
        status_code=Config.HttpStatus.FOUND,
    )


@router.post(
    "/requests/{request_id}/reject",
    response_class=HTMLResponse,
    name="reject_admin_request",
)
async def reject_admin_request(request: Request, request_id: int) -> Response:
    """Reject a restaurant submission request after admin and CSRF checks."""
    session, form_values = await _require_admin_post_session(request)
    rejection_message = form_values.get("message")
    if not isinstance(rejection_message, str) or not rejection_message.strip():
        return _render(
            request,
            session,
            "admin/request_detail.html",
            status_code=Config.HttpStatus.BAD_REQUEST,
            restaurant_request=None,
            request_id=request_id,
            error_message="거부 사유는 필수 입력 사항입니다.",
            success_message=None,
        )
    try:
        await meal_service_client.reject_request(
            user_id=session["user_id"],
            request_id=request_id,
            message=rejection_message,
        )
    except MealServiceError as exc:
        return _render(
            request,
            session,
            "admin/request_detail.html",
            status_code=exc.status_code,
            restaurant_request=None,
            request_id=request_id,
            error_message=exc.message,
            success_message=None,
        )
    return RedirectResponse(
        _url_with_message(
            request,
            "admin_request_detail_page",
            "등록 요청을 거부했습니다.",
            request_id=request_id,
        ),
        status_code=Config.HttpStatus.FOUND,
    )

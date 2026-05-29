"""Owner workflow routes for sandol_meal_web."""

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
)

router = APIRouter(prefix="/owner", tags=["Owner"])
templates = Jinja2Templates(directory=str(Config.TEMPLATE_DIR))


def _response_data(data: dict[str, Any]) -> dict[str, Any]:
    """Unwrap meal-service response envelopes when present."""
    nested_data = data.get("data")
    if isinstance(nested_data, dict):
        return nested_data
    return data


def _request_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract a list of request objects from common API response shapes."""
    response_data = _response_data(data)
    items = response_data.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    data_items = data.get("data")
    if isinstance(data_items, list):
        return [item for item in data_items if isinstance(item, dict)]
    if "id" in response_data:
        return [response_data]
    return []


def _template_context(
    request: Request,
    session: SessionData,
    **extra: Any,
) -> dict[str, Any]:
    """Build common template context for authenticated owner pages."""
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
    """Render an owner template with shared context."""
    return templates.TemplateResponse(
        request,
        template_name,
        _template_context(request, session, **extra),
        status_code=status_code,
    )


def _get_page_session(request: Request) -> SessionData | RedirectResponse:
    """Return a session for GET pages or redirect anonymous users to login."""
    session = get_optional_session(request)
    if session is None:
        return RedirectResponse(
            str(request.url_for("login")),
            status_code=Config.HttpStatus.FOUND,
        )
    return session


def _url_with_message(
    request: Request,
    route_name: str,
    message: str,
    **path_params: Any,
) -> str:
    """Build a root-path-aware URL with a plain success message."""
    url = str(request.url_for(route_name, **path_params))
    return f"{url}?{urlencode({'message': message})}"


async def _require_owner_post_session(request: Request) -> tuple[SessionData, dict[str, Any]]:
    """Require a valid session and CSRF token for owner POST forms."""
    session = get_optional_session(request)
    form = await request.form()
    csrf_token = form.get("csrf_token")
    if (
        session is None
        or not isinstance(csrf_token, str)
        or not secrets.compare_digest(csrf_token, session["csrf_token"])
    ):
        raise HTTPException(Config.HttpStatus.FORBIDDEN, "invalid_csrf_token")
    return session, dict(form)


@router.get("/requests", response_class=HTMLResponse, name="owner_requests_page")
async def owner_requests_page(request: Request, message: str | None = None) -> Response:
    """Render restaurant submission requests visible to the current owner."""
    session_or_redirect = _get_page_session(request)
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
        "owner/requests.html",
        requests=requests,
        error_message=error_message,
        success_message=message,
    )


@router.get("/requests/new", response_class=HTMLResponse, name="new_owner_request_page")
async def new_owner_request_page(request: Request) -> Response:
    """Render the owner restaurant request creation form."""
    session_or_redirect = _get_page_session(request)
    if isinstance(session_or_redirect, RedirectResponse):
        return session_or_redirect

    return _render(
        request,
        session_or_redirect,
        "owner/new_request.html",
        form_values={},
        error_message=None,
    )


@router.post("/requests", response_class=HTMLResponse, name="create_owner_request")
async def create_owner_request(request: Request) -> Response:
    """Create an owner restaurant submission request from form values."""
    session, form_values = await _require_owner_post_session(request)
    try:
        created = await meal_service_client.create_request_from_form(
            user_id=session["user_id"],
            form_data=form_values,
        )
    except MealServiceError as exc:
        return _render(
            request,
            session,
            "owner/new_request.html",
            status_code=exc.status_code,
            form_values=form_values,
            error_message=exc.message,
        )

    response_data = _response_data(created)
    request_id = response_data.get("request_id") or response_data.get("id")
    if isinstance(request_id, int):
        return RedirectResponse(
            _url_with_message(
                request,
                "owner_request_detail_page",
                "등록 요청이 접수되었습니다.",
                request_id=request_id,
            ),
            status_code=Config.HttpStatus.FOUND,
        )
    return RedirectResponse(
        _url_with_message(
            request,
            "owner_requests_page",
            "등록 요청이 접수되었습니다.",
        ),
        status_code=Config.HttpStatus.FOUND,
    )


@router.get(
    "/requests/{request_id}",
    response_class=HTMLResponse,
    name="owner_request_detail_page",
)
async def owner_request_detail_page(
    request: Request,
    request_id: int,
    message: str | None = None,
) -> Response:
    """Render a single owner restaurant submission request."""
    session_or_redirect = _get_page_session(request)
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
        "owner/request_detail.html",
        restaurant_request=restaurant_request,
        request_id=request_id,
        error_message=error_message,
        success_message=message,
    )


@router.post(
    "/requests/{request_id}/delete",
    response_class=HTMLResponse,
    name="delete_owner_request",
)
async def delete_owner_request(request: Request, request_id: int) -> Response:
    """Delete an accessible owner restaurant submission request."""
    session, form_values = await _require_owner_post_session(request)
    if form_values.get("confirm_delete") != "true":
        return _render(
            request,
            session,
            "owner/request_detail.html",
            status_code=Config.HttpStatus.BAD_REQUEST,
            restaurant_request=None,
            request_id=request_id,
            error_message="삭제 확인을 선택해주세요.",
            success_message=None,
        )
    try:
        await meal_service_client.delete_request(
            user_id=session["user_id"],
            request_id=request_id,
        )
    except MealServiceError as exc:
        return _render(
            request,
            session,
            "owner/request_detail.html",
            status_code=exc.status_code,
            restaurant_request=None,
            request_id=request_id,
            error_message=exc.message,
            success_message=None,
        )
    return RedirectResponse(
        _url_with_message(request, "owner_requests_page", "등록 요청이 삭제되었습니다."),
        status_code=Config.HttpStatus.FOUND,
    )

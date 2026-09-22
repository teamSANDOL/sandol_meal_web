"""Shared request/response plumbing for the owner and admin page routers."""

import secrets
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import Config
from app.services.meal_client import meal_service_client
from app.services.session_service import (
    SessionData,
    csrf_token_for_template,
    can_upload_meals,
    get_optional_session,
    has_admin_role,
    navigation_context,
)

templates = Jinja2Templates(directory=str(Config.TEMPLATE_DIR))

MEALS_PAGE_SIZE = 20

_SCAN_WINDOW_DAYS = 7


def _login_url_for_request(request: Request) -> str:
    """Build a root-path-aware login URL that preserves the current page."""
    login_url = str(request.url_for("login"))
    login_after = request.url.path
    root_path = request.scope.get("root_path", "")
    if (
        isinstance(root_path, str)
        and root_path
        and login_after != root_path
        and not login_after.startswith(f"{root_path}/")
    ):
        login_after = f"{root_path}{login_after}"
    if request.url.query:
        login_after = f"{login_after}?{request.url.query}"
    return f"{login_url}?{urlencode({'login_after': login_after})}"


def response_data(data: dict[str, Any]) -> dict[str, Any]:
    """Unwrap meal-service response envelopes when present."""
    nested_data = data.get("data")
    if isinstance(nested_data, dict):
        return nested_data
    return data


def response_meta(data: dict[str, Any]) -> dict[str, Any]:
    """Extract response meta information when present."""
    meta = data.get("meta")
    if isinstance(meta, dict):
        return meta
    nested = response_data(data)
    nested_meta = nested.get("meta") if isinstance(nested, dict) else None
    if isinstance(nested_meta, dict):
        return nested_meta
    return {}


def request_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract objects from common meal-service list response shapes."""
    nested = response_data(data)
    items = nested.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    data_items = data.get("data")
    if isinstance(data_items, list):
        return [item for item in data_items if isinstance(item, dict)]
    if "id" in nested or "request_id" in nested:
        return [nested]
    return []


def template_context(
    request: Request,
    session: SessionData,
    **extra: Any,
) -> dict[str, Any]:
    """Build the common template context for authenticated pages."""
    context: dict[str, Any] = {
        "request": request,
        "session": session,
        "csrf_token": csrf_token_for_template(session),
        **navigation_context(request, session),
    }
    context.update(extra)
    return context


def render(
    request: Request,
    session: SessionData,
    template_name: str,
    status_code: int = Config.HttpStatus.OK,
    **extra: Any,
) -> HTMLResponse:
    """Render a template with the shared authenticated context."""
    return templates.TemplateResponse(
        request,
        template_name,
        template_context(request, session, **extra),
        status_code=status_code,
    )


def url_with_message(
    request: Request,
    route_name: str,
    message: str,
    query_name: str = "message",
    extra_query: dict[str, Any] | None = None,
    **path_params: Any,
) -> str:
    """Build a root-path-aware URL carrying a plain status message."""
    url = str(request.url_for(route_name, **path_params))
    query: dict[str, Any] = {query_name: message}
    for key, value in (extra_query or {}).items():
        if value not in (None, ""):
            query[key] = value
    return f"{url}?{urlencode(query)}"


def page_session(
    request: Request,
    *,
    admin: bool = False,
    meal_uploader: bool = False,
) -> SessionData:
    """Return the session for a GET page, redirecting anonymous users to login."""
    session = get_optional_session(request)
    if session is None:
        login_url = _login_url_for_request(request)
        raise HTTPException(
            status_code=Config.HttpStatus.UNAUTHORIZED,
            detail=f"login_required:{login_url}",
        )
    if admin and not has_admin_role(session):
        raise HTTPException(Config.HttpStatus.FORBIDDEN, "admin_role_required")
    if meal_uploader and not can_upload_meals(session):
        raise HTTPException(Config.HttpStatus.FORBIDDEN, "meal_uploader_role_required")
    return session


async def post_session(
    request: Request,
    *,
    admin: bool = False,
    meal_uploader: bool = False,
) -> tuple[SessionData, dict[str, Any]]:
    """Require a valid session and CSRF token, returning the parsed form."""
    session = get_optional_session(request)
    if session is None:
        login_url = request.url_for("login")
        raise HTTPException(
            status_code=Config.HttpStatus.UNAUTHORIZED,
            detail=f"login_required:{login_url}",
        )
    if admin and not has_admin_role(session):
        raise HTTPException(Config.HttpStatus.FORBIDDEN, "admin_role_required")
    if meal_uploader and not can_upload_meals(session):
        raise HTTPException(Config.HttpStatus.FORBIDDEN, "meal_uploader_role_required")
    form = await request.form()
    csrf_token = form.get("csrf_token")
    if not isinstance(csrf_token, str) or not secrets.compare_digest(
        csrf_token,
        session["csrf_token"],
    ):
        raise HTTPException(Config.HttpStatus.FORBIDDEN, "invalid_csrf_token")
    return session, dict(form)


def optional_positive_int(value: str | None) -> int | None:
    """Return a positive integer filter value or None."""
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def normalize_date_range(start_date: str, end_date: str) -> tuple[str, str]:
    """Normalize valid date filters, preserving invalid input for API errors."""
    try:
        parsed_start = date.fromisoformat(start_date) if start_date else None
        parsed_end = date.fromisoformat(end_date) if end_date else None
    except ValueError:
        return start_date, end_date
    if parsed_start and parsed_end and parsed_start > parsed_end:
        return parsed_end.isoformat(), parsed_start.isoformat()
    return start_date, end_date


def meal_served_date(meal: dict[str, Any]) -> date | None:
    """Return a meal's provided date."""
    served_date = meal.get("date") or meal.get("served_date")
    if not isinstance(served_date, str):
        return None
    try:
        return date.fromisoformat(served_date)
    except ValueError:
        return None


def _meal_timestamp(meal: dict[str, Any], field_name: str) -> float:
    """Return a sortable timestamp from a meal response field."""
    value = meal.get(field_name)
    if not isinstance(value, str):
        return float("-inf")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("-inf")


def sort_meals(meals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort meals newest-first by meal date, then registration time."""
    return sorted(
        meals,
        key=lambda meal: (
            meal_served_date(meal) or date.min,
            _meal_timestamp(meal, "registered_at"),
            meal.get("id") if isinstance(meal.get("id"), int) else -1,
        ),
        reverse=True,
    )


def _is_within_date_range(
    meal: dict[str, Any],
    start_date: date | None,
    end_date: date | None,
) -> bool:
    """Return whether a meal belongs to an inclusive local date range."""
    served_date = meal_served_date(meal)
    if served_date is None:
        return True
    if start_date is not None and served_date < start_date:
        return False
    return end_date is None or served_date <= end_date


async def load_filtered_meals(  # noqa: C901, PLR0913
    session: SessionData,
    *,
    restaurant_id: int | None,
    start_date: str,
    end_date: str,
    page: int,
    size: int,
    restaurant_name: str = "",
    meal_type: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """Load the requested server-filtered page of meals.

    The meal API owns filtering, sorting, and pagination.  Callers must pass
    the actual UI page and page size instead of increasing ``size`` to reach
    later pages; meal-service caps the latter at 100.
    """
    if restaurant_id is None:
        data = await meal_service_client.list_meals(
            user_id=session["user_id"],
            page=page,
            size=size,
            start_date=start_date or None,
            end_date=end_date or None,
            restaurant_name=restaurant_name or None,
            meal_type=meal_type or None,
        )
    else:
        data = await meal_service_client.list_meals_by_restaurant(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
            page=page,
            size=size,
            start_date=start_date or None,
            end_date=end_date or None,
            meal_type=meal_type or None,
        )
    total = response_meta(data).get("total", 0)
    return request_items(data), total if isinstance(total, int) else 0


def pagination_context(
    meta: dict[str, Any],
    *,
    fallback_page: int,
    page_url: Any,
) -> dict[str, Any]:
    """Build pagination labels and filter-preserving navigation URLs."""
    current_page = meta.get("page", fallback_page)
    total = meta.get("total", 0)
    page_size = meta.get("size", MEALS_PAGE_SIZE)
    if not isinstance(current_page, int):
        current_page = fallback_page
    if not isinstance(total, int):
        total = 0
    if not isinstance(page_size, int) or page_size < 1:
        page_size = MEALS_PAGE_SIZE
    total_pages = max(1, (total + page_size - 1) // page_size)

    return {
        "current_page": current_page,
        "total": total,
        "total_pages": total_pages,
        "first_url": page_url(1) if current_page > 1 else None,
        "prev_url": page_url(current_page - 1) if current_page > 1 else None,
        "next_url": page_url(current_page + 1) if current_page < total_pages else None,
        "last_url": page_url(total_pages) if current_page < total_pages else None,
    }


async def load_restaurant_options(
    session: SessionData,
    *,
    owner_user_id: str | None = None,
) -> list[dict[str, str]]:
    """Load restaurant select options across paginated responses."""
    page = 1
    options: list[dict[str, str]] = []

    while True:
        data = await meal_service_client.list_restaurants(
            user_id=session["user_id"],
            page=page,
            size=100,
            owner_user_id=owner_user_id,
        )
        for restaurant in request_items(data):
            restaurant_id = restaurant.get("id")
            if isinstance(restaurant_id, int):
                options.append(
                    {
                        "id": str(restaurant_id),
                        "name": str(restaurant.get("name") or ""),
                    }
                )
        if response_meta(data).get("has_next") is not True:
            break
        page += 1

    return options


def redirect(target: str) -> RedirectResponse:
    """Return a standard 302 redirect."""
    return RedirectResponse(target, status_code=Config.HttpStatus.FOUND)

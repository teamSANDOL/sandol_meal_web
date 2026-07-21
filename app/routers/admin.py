"""Admin workflow routes for sandol_meal_web."""

import secrets
from datetime import date, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import Config
from app.services.meal_client import MealServiceError, meal_service_client
from app.services.session_service import (
    SessionData,
    csrf_token_for_template,
    get_optional_session,
    has_admin_role,
    navigation_context,
    require_admin_session,
)

router = APIRouter(prefix="/admin", tags=["Admin"])
templates = Jinja2Templates(directory=str(Config.TEMPLATE_DIR))

ESTABLISHMENT_TYPE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("student", "교내 학생식당"),
    ("fixed_menu_restaurant", "고정메뉴 일반식당"),
    ("fixed_korean_buffet", "고정메뉴형 한식뷔페"),
    ("variable_korean_buffet", "메뉴 변경형 한식뷔페"),
)
BUILDING_OPTIONS: tuple[str, ...] = ("TIP", "중앙", "E동", "산학융합관")
TIME_OPTIONS: tuple[str, ...] = tuple(
    f"{hour:02d}:{minute:02d}"
    for hour in range(24)
    for minute in (0, 30)
)
MEAL_TYPE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("breakfast", "아침"),
    ("brunch", "브런치"),
    ("lunch", "점심"),
    ("dinner", "저녁"),
)
MEALS_PAGE_SIZE = 20


def _response_data(data: dict[str, Any]) -> dict[str, Any]:
    """Unwrap meal-service response envelopes when present."""
    nested_data = data.get("data")
    if isinstance(nested_data, dict):
        return nested_data
    return data


def _response_meta(data: dict[str, Any]) -> dict[str, Any]:
    """Extract response meta information when present."""
    meta = data.get("meta")
    if isinstance(meta, dict):
        return meta
    response_data = _response_data(data)
    nested_meta = response_data.get("meta") if isinstance(response_data, dict) else None
    if isinstance(nested_meta, dict):
        return nested_meta
    return {}


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


def _string_form_value(value: Any) -> str:
    """Return a template-safe string value for HTML forms."""
    if value is None:
        return ""
    return str(value)


def _range_endpoint_value(value: Any, endpoint: str) -> str:
    """Return a start/end time string from a meal-service range object."""
    if isinstance(value, dict):
        endpoint_value = value.get(endpoint)
        if endpoint_value is None:
            return ""
        return str(endpoint_value)
    return ""


def _restaurant_form_values(restaurant: dict[str, Any] | None) -> dict[str, str]:
    """Flatten restaurant API data into form field values."""
    if restaurant is None:
        return {}

    location = restaurant.get("location")
    location_data = location if isinstance(location, dict) else {}
    map_links = location_data.get("map_links")
    map_link_data = map_links if isinstance(map_links, dict) else {}

    return {
        "name": _string_form_value(restaurant.get("name")),
        "owner_user_id": _string_form_value(restaurant.get("owner_user_id")),
        "establishment_type": _string_form_value(
            restaurant.get("establishment_type")
        ),
        "price": _string_form_value(restaurant.get("price")),
        "is_campus": "true" if location_data.get("is_campus") else "false",
        "building": _string_form_value(location_data.get("building")),
        "naver_map_link": _string_form_value(map_link_data.get("naver")),
        "kakao_map_link": _string_form_value(map_link_data.get("kakao")),
        "latitude": _string_form_value(location_data.get("latitude")),
        "longitude": _string_form_value(location_data.get("longitude")),
        "opening_time_start": _range_endpoint_value(
            restaurant.get("opening_time"), "start"
        ),
        "opening_time_end": _range_endpoint_value(
            restaurant.get("opening_time"), "end"
        ),
        "break_time_start": _range_endpoint_value(
            restaurant.get("break_time"), "start"
        ),
        "break_time_end": _range_endpoint_value(
            restaurant.get("break_time"), "end"
        ),
        "breakfast_time_start": _range_endpoint_value(
            restaurant.get("breakfast_time"), "start"
        ),
        "breakfast_time_end": _range_endpoint_value(
            restaurant.get("breakfast_time"), "end"
        ),
        "brunch_time_start": _range_endpoint_value(
            restaurant.get("brunch_time"), "start"
        ),
        "brunch_time_end": _range_endpoint_value(
            restaurant.get("brunch_time"), "end"
        ),
        "lunch_time_start": _range_endpoint_value(
            restaurant.get("lunch_time"), "start"
        ),
        "lunch_time_end": _range_endpoint_value(
            restaurant.get("lunch_time"), "end"
        ),
        "dinner_time_start": _range_endpoint_value(
            restaurant.get("dinner_time"), "start"
        ),
        "dinner_time_end": _range_endpoint_value(
            restaurant.get("dinner_time"), "end"
        ),
    }


def _meal_form_values(meal: dict[str, Any] | None) -> dict[str, str]:
    """Flatten meal API data into form field values."""
    if meal is None:
        return {}

    menu_value = meal.get("menu")
    menu_lines = ""
    if isinstance(menu_value, list):
        menu_lines = "\n".join(str(item) for item in menu_value)

    return {
        "restaurant_id": _string_form_value(meal.get("restaurant_id")),
        "meal_type": _string_form_value(meal.get("meal_type")),
        "menu": menu_lines,
    }


def _restaurant_options(restaurants: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Build select options for restaurant dropdowns."""
    options: list[dict[str, str]] = []
    for restaurant in restaurants:
        restaurant_id = restaurant.get("id")
        if isinstance(restaurant_id, int):
            options.append(
                {
                    "id": str(restaurant_id),
                    "name": _string_form_value(restaurant.get("name")),
                }
            )
    return options


async def _load_restaurant_options(session: SessionData) -> list[dict[str, str]]:
    """Load restaurant select options across paginated responses."""
    page = 1
    size = 100
    options: list[dict[str, str]] = []

    while True:
        restaurant_data = await meal_service_client.list_restaurants(
            user_id=session["user_id"],
            page=page,
            size=size,
        )
        restaurant_items = _request_items(restaurant_data)
        options.extend(_restaurant_options(restaurant_items))

        meta = _response_meta(restaurant_data)
        has_next = meta.get("has_next")
        if has_next is not True:
            break
        page += 1

    return options


def _meal_timestamp(meal: dict[str, Any], field_name: str) -> float:
    """Return a sortable timestamp from a meal response field."""
    value = meal.get(field_name)
    if not isinstance(value, str):
        return float("-inf")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("-inf")


def _normalize_date_range(start_date: str, end_date: str) -> tuple[str, str]:
    """Normalize valid date filters while preserving invalid input for API errors."""
    try:
        parsed_start = date.fromisoformat(start_date) if start_date else None
        parsed_end = date.fromisoformat(end_date) if end_date else None
    except ValueError:
        return start_date, end_date
    if parsed_start and parsed_end and parsed_start > parsed_end:
        return parsed_end.isoformat(), parsed_start.isoformat()
    return start_date, end_date


def _api_end_date(end_date: str) -> str:
    """Expand an inclusive UI end date for the existing midnight-based API."""
    if not end_date:
        return ""
    try:
        return (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
    except ValueError:
        return end_date


def _meal_updated_date(meal: dict[str, Any]) -> date | None:
    """Return a meal's updated date in the service timezone."""
    updated_at = meal.get("updated_at")
    if not isinstance(updated_at, str):
        return None
    try:
        updated_datetime = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        if updated_datetime.tzinfo is not None:
            updated_datetime = updated_datetime.astimezone(Config.TZ)
        return updated_datetime.date()
    except ValueError:
        return None


def _is_within_date_range(
    meal: dict[str, Any],
    start_date: date | None,
    end_date: date | None,
) -> bool:
    """Return whether a meal belongs to an inclusive local date range."""
    updated_date = _meal_updated_date(meal)
    if updated_date is None:
        return True
    if start_date is not None and updated_date < start_date:
        return False
    return end_date is None or updated_date <= end_date


async def _load_filtered_meals(  # noqa: C901
    session: SessionData,
    *,
    restaurant_id: int | None,
    start_date: str,
    end_date: str,
    required_count: int,
) -> tuple[list[dict[str, Any]], int]:
    """Load weekly windows until the requested meal page can be filled."""
    try:
        parsed_start_date = date.fromisoformat(start_date) if start_date else None
        parsed_end_date = date.fromisoformat(end_date) if end_date else None
    except ValueError as exc:
        raise MealServiceError(
            Config.HttpStatus.BAD_REQUEST,
            "날짜 형식이 올바르지 않습니다. (YYYY-MM-DD)",
        ) from exc
    scan_end_date = parsed_end_date or datetime.now(Config.TZ).date()
    api_end_date = _api_end_date(end_date)
    meals: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    async def request_page(
        *,
        page: int,
        size: int,
        api_start_date: str,
        api_end_date: str,
    ) -> dict[str, Any]:
        if restaurant_id is None:
            return await meal_service_client.list_meals(
                user_id=session["user_id"],
                page=page,
                size=size,
                start_date=api_start_date,
                end_date=api_end_date,
            )
        return await meal_service_client.list_meals_by_restaurant(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
            page=page,
            size=size,
            start_date=api_start_date,
            end_date=api_end_date,
        )

    count_response = await request_page(
        page=1,
        size=1,
        api_start_date=start_date,
        api_end_date=api_end_date,
    )
    total = _response_meta(count_response).get("total", 0)
    if not isinstance(total, int):
        total = 0

    if parsed_end_date is not None:
        boundary_date = (parsed_end_date + timedelta(days=1)).isoformat()
        boundary_response = await request_page(
            page=1,
            size=1,
            api_start_date=boundary_date,
            api_end_date=boundary_date,
        )
        boundary_total = _response_meta(boundary_response).get("total", 0)
        if isinstance(boundary_total, int):
            total = max(0, total - boundary_total)

    if total == 0:
        return [], 0

    target_count = min(total, required_count)
    while len(meals) < target_count:
        window_start_date = scan_end_date - timedelta(days=6)
        if parsed_start_date is not None and window_start_date < parsed_start_date:
            window_start_date = parsed_start_date

        window_page = 1
        while True:
            data = await request_page(
                page=window_page,
                size=100,
                api_start_date=window_start_date.isoformat(),
                api_end_date=(scan_end_date + timedelta(days=1)).isoformat(),
            )
            for meal in _request_items(data):
                meal_id = meal.get("id")
                if (
                    isinstance(meal_id, int)
                    and meal_id not in seen_ids
                    and _is_within_date_range(
                        meal,
                        window_start_date,
                        scan_end_date,
                    )
                ):
                    seen_ids.add(meal_id)
                    meals.append(meal)
            if _response_meta(data).get("has_next") is not True:
                break
            window_page += 1

        if parsed_start_date is not None and window_start_date <= parsed_start_date:
            break
        scan_end_date = window_start_date - timedelta(days=1)

    meals.sort(
        key=lambda meal: (
            _meal_timestamp(meal, "updated_at"),
            meal.get("id") if isinstance(meal.get("id"), int) else -1,
        ),
        reverse=True,
    )
    return meals, total


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
        **navigation_context(request, session),
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


def _render_restaurant_form(  # noqa: PLR0913
    request: Request,
    session: SessionData,
    *,
    template_name: str = "admin/restaurant_form.html",
    restaurant: dict[str, Any] | None,
    form_values: dict[str, Any],
    page_title: str,
    page_description: str,
    submit_label: str,
    action_url: str,
    back_url: str,
    restaurant_id: int | None = None,
    status_code: int = Config.HttpStatus.OK,
    error_message: str | None = None,
    success_message: str | None = None,
) -> HTMLResponse:
    """Render the shared registered-restaurant admin form."""
    return _render(
        request,
        session,
        template_name,
        status_code=status_code,
        restaurant=restaurant,
        restaurant_id=restaurant_id,
        form_values=form_values,
        page_title=page_title,
        page_description=page_description,
        submit_label=submit_label,
        action_url=action_url,
        back_url=back_url,
        establishment_type_options=ESTABLISHMENT_TYPE_OPTIONS,
        building_options=BUILDING_OPTIONS,
        time_options=TIME_OPTIONS,
        error_message=error_message,
        success_message=success_message,
    )


def _render_meal_form(  # noqa: PLR0913
    request: Request,
    session: SessionData,
    *,
    meal: dict[str, Any] | None,
    form_values: dict[str, Any],
    restaurant_options: list[dict[str, str]],
    page_title: str,
    page_description: str,
    submit_label: str,
    action_url: str,
    back_url: str,
    meal_id: int | None = None,
    status_code: int = Config.HttpStatus.OK,
    error_message: str | None = None,
    success_message: str | None = None,
) -> HTMLResponse:
    """Render the shared meal admin form."""
    return _render(
        request,
        session,
        "admin/meal_form.html",
        status_code=status_code,
        meal=meal,
        meal_id=meal_id,
        form_values=form_values,
        restaurant_options=restaurant_options,
        meal_type_options=MEAL_TYPE_OPTIONS,
        page_title=page_title,
        page_description=page_description,
        submit_label=submit_label,
        action_url=action_url,
        back_url=back_url,
        error_message=error_message,
        success_message=success_message,
    )


def _render_restaurant_manager_page(  # noqa: PLR0913
    request: Request,
    session: SessionData,
    *,
    restaurant_id: int,
    restaurant: dict[str, Any] | None,
    managers: list[dict[str, Any]],
    form_values: dict[str, Any] | None = None,
    status_code: int = Config.HttpStatus.OK,
    error_message: str | None = None,
    success_message: str | None = None,
) -> HTMLResponse:
    """Render the admin restaurant manager management page."""
    return _render(
        request,
        session,
        "admin/restaurant_managers.html",
        status_code=status_code,
        restaurant_id=restaurant_id,
        restaurant=restaurant,
        managers=managers,
        form_values=form_values or {},
        error_message=error_message,
        success_message=success_message,
    )


def _url_with_message(
    request: Request,
    route_name: str,
    message: str,
    query_name: str = "message",
    **path_params: Any,
) -> str:
    """Build a root-path-aware URL with a plain status message."""
    url = str(request.url_for(route_name, **path_params))
    return f"{url}?{urlencode({query_name: message})}"


def _optional_positive_int(value: str | None) -> int | None:
    """Return a positive integer filter value or None."""
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _meal_page_url(
    request: Request,
    *,
    page: int,
    restaurant_id: int | None,
    start_date: str,
    end_date: str,
) -> str:
    """Build a meal-list URL while preserving active filters."""
    params: dict[str, str | int] = {"page": page}
    if restaurant_id is not None:
        params["restaurant_id"] = restaurant_id
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    return f"{request.url_for('admin_meals_page')}?{urlencode(params)}"


def _meal_pagination_context(  # noqa: PLR0913
    request: Request,
    meta: dict[str, Any],
    *,
    fallback_page: int,
    restaurant_id: int | None,
    start_date: str,
    end_date: str,
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

    def page_url(target_page: int) -> str:
        return _meal_page_url(
            request,
            page=target_page,
            restaurant_id=restaurant_id,
            start_date=start_date,
            end_date=end_date,
        )

    return {
        "current_page": current_page,
        "total": total,
        "total_pages": total_pages,
        "first_url": page_url(1) if current_page > 1 else None,
        "prev_url": page_url(current_page - 1) if current_page > 1 else None,
        "next_url": page_url(current_page + 1)
        if current_page < total_pages
        else None,
        "last_url": page_url(total_pages) if current_page < total_pages else None,
    }


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
    "/restaurants",
    response_class=HTMLResponse,
    name="admin_restaurants_page",
)
async def admin_restaurants_page(
    request: Request,
    message: str | None = None,
) -> Response:
    """Render all registered restaurants for admin management."""
    session_or_redirect = _get_admin_page_session(request)
    if isinstance(session_or_redirect, RedirectResponse):
        return session_or_redirect

    session = session_or_redirect
    error_message = None
    restaurants: list[dict[str, Any]] = []
    try:
        data = await meal_service_client.list_restaurants(user_id=session["user_id"])
        restaurants = _request_items(data)
    except MealServiceError as exc:
        error_message = exc.message

    return _render(
        request,
        session,
        "admin/restaurants.html",
        restaurants=restaurants,
        error_message=error_message,
        success_message=message,
    )


@router.get("/meals", response_class=HTMLResponse, name="admin_meals_page")
async def admin_meals_page(  # noqa: PLR0913
    request: Request,
    message: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    restaurant_id: str | None = None,
    start_date: str = "",
    end_date: str = "",
) -> Response:
    """Render filtered meal records for admin management."""
    session_or_redirect = _get_admin_page_session(request)
    if isinstance(session_or_redirect, RedirectResponse):
        return session_or_redirect

    session = session_or_redirect
    error_message = None
    meals: list[dict[str, Any]] = []
    pagination_meta: dict[str, Any] = {}
    restaurant_options: list[dict[str, str]] = []
    selected_restaurant_id = _optional_positive_int(restaurant_id)
    normalized_start_date, normalized_end_date = _normalize_date_range(
        start_date.strip(),
        end_date.strip(),
    )
    try:
        all_meals, total = await _load_filtered_meals(
            session,
            restaurant_id=selected_restaurant_id,
            start_date=normalized_start_date,
            end_date=normalized_end_date,
            required_count=page * MEALS_PAGE_SIZE,
        )
        total_pages = max(1, (total + MEALS_PAGE_SIZE - 1) // MEALS_PAGE_SIZE)
        if total > 0 and page > total_pages:
            return RedirectResponse(
                _meal_page_url(
                    request,
                    page=total_pages,
                    restaurant_id=selected_restaurant_id,
                    start_date=normalized_start_date,
                    end_date=normalized_end_date,
                ),
                status_code=Config.HttpStatus.FOUND,
            )
        page_start = (page - 1) * MEALS_PAGE_SIZE
        meals = all_meals[page_start : page_start + MEALS_PAGE_SIZE]
        pagination_meta = {
            "page": page,
            "size": MEALS_PAGE_SIZE,
            "total": total,
        }
    except MealServiceError as exc:
        error_message = exc.message

    try:
        restaurant_options = await _load_restaurant_options(session)
    except MealServiceError as exc:
        if error_message is None:
            error_message = exc.message

    pagination = _meal_pagination_context(
        request,
        pagination_meta,
        fallback_page=page,
        restaurant_id=selected_restaurant_id,
        start_date=normalized_start_date,
        end_date=normalized_end_date,
    )

    return _render(
        request,
        session,
        "admin/meals.html",
        meals=meals,
        pagination_meta=pagination_meta,
        pagination=pagination,
        restaurant_options=restaurant_options,
        selected_restaurant_id=str(selected_restaurant_id or ""),
        start_date=normalized_start_date,
        end_date=normalized_end_date,
        has_filters=bool(
            selected_restaurant_id or normalized_start_date or normalized_end_date
        ),
        error_message=error_message,
        success_message=message,
    )


@router.get("/meals/new", response_class=HTMLResponse, name="admin_new_meal_page")
async def admin_new_meal_page(request: Request) -> Response:
    """Render the admin form for creating a meal record."""
    session_or_redirect = _get_admin_page_session(request)
    if isinstance(session_or_redirect, RedirectResponse):
        return session_or_redirect

    session = session_or_redirect
    restaurant_options: list[dict[str, str]] = []
    error_message = None
    try:
        restaurant_options = await _load_restaurant_options(session)
    except MealServiceError as exc:
        error_message = exc.message

    return _render_meal_form(
        request,
        session,
        meal=None,
        form_values={},
        restaurant_options=restaurant_options,
        page_title="관리자 식단 등록",
        page_description="기존 식당을 선택해 새로운 식단 기록을 등록합니다.",
        submit_label="식단 등록",
        action_url=str(request.url_for("create_admin_meal")),
        back_url=str(request.url_for("admin_meals_page")),
        error_message=error_message,
    )


@router.post("/meals", response_class=HTMLResponse, name="create_admin_meal")
async def create_admin_meal(request: Request) -> Response:
    """Create a meal from the admin form."""
    session, form_values = await _require_admin_post_session(request)
    restaurant_options: list[dict[str, str]] = []
    try:
        restaurant_options = await _load_restaurant_options(session)
        data = await meal_service_client.create_meal_from_form(
            user_id=session["user_id"],
            form_data=form_values,
        )
        meal = _response_data(data)
        meal_id = meal.get("id")
        if not isinstance(meal_id, int):
            raise MealServiceError(
                Config.HttpStatus.INTERNAL_SERVER_ERROR,
                "생성된 식단 정보를 확인할 수 없습니다.",
            )
    except MealServiceError as exc:
        return _render_meal_form(
            request,
            session,
            meal=None,
            form_values=form_values,
            restaurant_options=restaurant_options,
            page_title="관리자 식단 등록",
            page_description="기존 식당을 선택해 새로운 식단 기록을 등록합니다.",
            submit_label="식단 등록",
            action_url=str(request.url_for("create_admin_meal")),
            back_url=str(request.url_for("admin_meals_page")),
            status_code=exc.status_code,
            error_message=exc.message,
        )

    return RedirectResponse(
        _url_with_message(
            request,
            "admin_edit_meal_page",
            "식단을 등록했습니다.",
            meal_id=meal_id,
        ),
        status_code=Config.HttpStatus.FOUND,
    )


@router.get(
    "/meals/{meal_id}/edit",
    response_class=HTMLResponse,
    name="admin_edit_meal_page",
)
async def admin_edit_meal_page(
    request: Request,
    meal_id: int,
    message: str | None = None,
) -> Response:
    """Render the admin form for editing a meal record."""
    session_or_redirect = _get_admin_page_session(request)
    if isinstance(session_or_redirect, RedirectResponse):
        return session_or_redirect

    session = session_or_redirect
    error_message = None
    meal: dict[str, Any] | None = None
    form_values: dict[str, Any] = {}
    restaurant_options: list[dict[str, str]] = []
    try:
        restaurant_options = await _load_restaurant_options(session)
        data = await meal_service_client.get_meal_detail(
            user_id=session["user_id"],
            meal_id=meal_id,
        )
        meal = _response_data(data)
        form_values = _meal_form_values(meal)
    except MealServiceError as exc:
        error_message = exc.message

    return _render_meal_form(
        request,
        session,
        meal=meal,
        meal_id=meal_id,
        form_values=form_values,
        restaurant_options=restaurant_options,
        page_title="관리자 식단 수정",
        page_description="저장된 식단 기록을 수정합니다.",
        submit_label="식단 저장",
        action_url=str(request.url_for("update_admin_meal", meal_id=meal_id)),
        back_url=str(request.url_for("admin_meals_page")),
        error_message=error_message,
        success_message=message,
    )


@router.post(
    "/meals/{meal_id}/edit",
    response_class=HTMLResponse,
    name="update_admin_meal",
)
async def update_admin_meal(request: Request, meal_id: int) -> Response:
    """Update a meal from the admin form."""
    session, form_values = await _require_admin_post_session(request)
    restaurant_options: list[dict[str, str]] = []
    try:
        restaurant_options = await _load_restaurant_options(session)
        data = await meal_service_client.update_meal_from_form(
            user_id=session["user_id"],
            meal_id=meal_id,
            form_data=form_values,
        )
        meal = _response_data(data)
        rendered_form_values = _meal_form_values(meal)
    except MealServiceError as exc:
        return _render_meal_form(
            request,
            session,
            meal=None,
            meal_id=meal_id,
            form_values=form_values,
            restaurant_options=restaurant_options,
            page_title="관리자 식단 수정",
            page_description="저장된 식단 기록을 수정합니다.",
            submit_label="식단 저장",
            action_url=str(request.url_for("update_admin_meal", meal_id=meal_id)),
            back_url=str(request.url_for("admin_meals_page")),
            status_code=exc.status_code,
            error_message=exc.message,
        )

    return _render_meal_form(
        request,
        session,
        meal=meal,
        meal_id=meal_id,
        form_values=rendered_form_values,
        restaurant_options=restaurant_options,
        page_title="관리자 식단 수정",
        page_description="저장된 식단 기록을 수정합니다.",
        submit_label="식단 저장",
        action_url=str(request.url_for("update_admin_meal", meal_id=meal_id)),
        back_url=str(request.url_for("admin_meals_page")),
        success_message="식단 정보를 저장했습니다.",
    )


@router.get(
    "/restaurants/new",
    response_class=HTMLResponse,
    name="admin_new_restaurant_page",
)
async def admin_new_restaurant_page(request: Request) -> Response:
    """Render the admin form for directly creating a registered restaurant."""
    session_or_redirect = _get_admin_page_session(request)
    if isinstance(session_or_redirect, RedirectResponse):
        return session_or_redirect

    session = session_or_redirect
    return _render_restaurant_form(
        request,
        session,
        restaurant=None,
        form_values={},
        page_title="관리자 등록 식당 생성",
        page_description="등록 요청 없이 바로 식당을 만들고 owner_user_id(Keycloak 사용자 ID)를 지정합니다.",
        submit_label="식당 생성",
        action_url=str(request.url_for("create_admin_restaurant")),
        back_url=str(request.url_for("admin_restaurants_page")),
    )


@router.post(
    "/restaurants",
    response_class=HTMLResponse,
    name="create_admin_restaurant",
)
async def create_admin_restaurant(request: Request) -> Response:
    """Create a registered restaurant directly from the admin form."""
    session, form_values = await _require_admin_post_session(request)
    try:
        data = await meal_service_client.create_restaurant_from_form(
            user_id=session["user_id"],
            form_data=form_values,
        )
        restaurant = _response_data(data)
        restaurant_id = restaurant.get("id")
        if not isinstance(restaurant_id, int):
            raise MealServiceError(
                Config.HttpStatus.INTERNAL_SERVER_ERROR,
                "생성된 식당 정보를 확인할 수 없습니다.",
            )
    except MealServiceError as exc:
        return _render_restaurant_form(
            request,
            session,
            restaurant=None,
            form_values=form_values,
            page_title="관리자 등록 식당 생성",
            page_description="등록 요청 없이 바로 식당을 만들고 owner_user_id(Keycloak 사용자 ID)를 지정합니다.",
            submit_label="식당 생성",
            action_url=str(request.url_for("create_admin_restaurant")),
            back_url=str(request.url_for("admin_restaurants_page")),
            status_code=exc.status_code,
            error_message=exc.message,
        )

    return RedirectResponse(
        _url_with_message(
            request,
            "admin_edit_restaurant_page",
            "식당을 생성했습니다.",
            restaurant_id=restaurant_id,
        ),
        status_code=Config.HttpStatus.FOUND,
    )


@router.get(
    "/restaurants/{restaurant_id}/managers",
    response_class=HTMLResponse,
    name="admin_restaurant_managers_page",
)
async def admin_restaurant_managers_page(
    request: Request,
    restaurant_id: int,
    message: str | None = None,
    error: str | None = None,
) -> Response:
    """Render the admin page for managing restaurant managers."""
    session_or_redirect = _get_admin_page_session(request)
    if isinstance(session_or_redirect, RedirectResponse):
        return session_or_redirect

    session = session_or_redirect
    error_message = error
    restaurant: dict[str, Any] | None = None
    managers: list[dict[str, Any]] = []
    try:
        restaurant_data = await meal_service_client.get_restaurant_detail(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
        )
        restaurant = _response_data(restaurant_data)
        manager_data = await meal_service_client.list_restaurant_managers(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
        )
        managers = _request_items(manager_data)
    except MealServiceError as exc:
        if error_message is None:
            error_message = exc.message

    return _render_restaurant_manager_page(
        request,
        session,
        restaurant_id=restaurant_id,
        restaurant=restaurant,
        managers=managers,
        error_message=error_message,
        success_message=message,
    )


@router.post(
    "/restaurants/{restaurant_id}/managers",
    response_class=HTMLResponse,
    name="add_admin_restaurant_manager",
)
async def add_admin_restaurant_manager(
    request: Request,
    restaurant_id: int,
) -> Response:
    """Register a restaurant manager from the admin form."""
    session, form_values = await _require_admin_post_session(request)
    try:
        await meal_service_client.add_restaurant_manager_from_form(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
            form_data=form_values,
        )
    except MealServiceError as exc:
        restaurant: dict[str, Any] | None = None
        managers: list[dict[str, Any]] = []
        error_message = exc.message
        try:
            restaurant_data = await meal_service_client.get_restaurant_detail(
                user_id=session["user_id"],
                restaurant_id=restaurant_id,
            )
            restaurant = _response_data(restaurant_data)
            manager_data = await meal_service_client.list_restaurant_managers(
                user_id=session["user_id"],
                restaurant_id=restaurant_id,
            )
            managers = _request_items(manager_data)
        except MealServiceError as load_exc:
            error_message = f"{exc.message} 추가 정보 조회 실패: {load_exc.message}"
        return _render_restaurant_manager_page(
            request,
            session,
            restaurant_id=restaurant_id,
            restaurant=restaurant,
            managers=managers,
            form_values=form_values,
            status_code=exc.status_code,
            error_message=error_message,
        )

    return RedirectResponse(
        _url_with_message(
            request,
            "admin_restaurant_managers_page",
            "Manager를 등록했습니다.",
            restaurant_id=restaurant_id,
        ),
        status_code=Config.HttpStatus.FOUND,
    )


@router.post(
    "/restaurants/{restaurant_id}/managers/delete",
    response_class=HTMLResponse,
    name="delete_admin_restaurant_manager",
)
async def delete_admin_restaurant_manager(
    request: Request,
    restaurant_id: int,
) -> Response:
    """Remove a restaurant manager from the admin form."""
    session, form_values = await _require_admin_post_session(request)
    manager_user_id = form_values.get("manager_user_id")
    if not isinstance(manager_user_id, str):
        manager_user_id = ""
    try:
        await meal_service_client.remove_restaurant_manager(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
            manager_user_id=manager_user_id,
        )
    except MealServiceError as exc:
        return RedirectResponse(
            _url_with_message(
                request,
                "admin_restaurant_managers_page",
                exc.message,
                query_name="error",
                restaurant_id=restaurant_id,
            ),
            status_code=Config.HttpStatus.FOUND,
        )

    return RedirectResponse(
        _url_with_message(
            request,
            "admin_restaurant_managers_page",
            "Manager를 해제했습니다.",
            restaurant_id=restaurant_id,
        ),
        status_code=Config.HttpStatus.FOUND,
    )


@router.get(
    "/restaurants/{restaurant_id}/manager-requests",
    response_class=HTMLResponse,
    name="admin_restaurant_manager_requests_page",
)
async def admin_restaurant_manager_requests_page(
    request: Request,
    restaurant_id: int,
    message: str | None = None,
    error: str | None = None,
) -> Response:
    """Render manager registration requests for a restaurant."""
    session_or_redirect = _get_admin_page_session(request)
    if isinstance(session_or_redirect, RedirectResponse):
        return session_or_redirect

    session = session_or_redirect
    error_message = error
    restaurant: dict[str, Any] | None = None
    manager_requests: list[dict[str, Any]] = []
    try:
        restaurant_data = await meal_service_client.get_restaurant_detail(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
        )
        restaurant = _response_data(restaurant_data)
        data = await meal_service_client.list_restaurant_manager_requests(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
        )
        manager_requests = _request_items(data)
    except MealServiceError as exc:
        if error_message is None:
            error_message = exc.message

    return _render(
        request,
        session,
        "admin/restaurant_manager_requests.html",
        restaurant=restaurant,
        restaurant_id=restaurant_id,
        manager_requests=manager_requests,
        error_message=error_message,
        success_message=message,
    )


@router.post(
    "/restaurants/{restaurant_id}/manager-requests/{request_id}/approve",
    response_class=HTMLResponse,
    name="approve_admin_restaurant_manager_request",
)
async def approve_admin_restaurant_manager_request(
    request: Request,
    restaurant_id: int,
    request_id: int,
) -> Response:
    """Approve a manager registration request from the admin workflow."""
    session, _ = await _require_admin_post_session(request)
    try:
        await meal_service_client.approve_restaurant_manager_request(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
            request_id=request_id,
        )
    except MealServiceError as exc:
        return RedirectResponse(
            _url_with_message(
                request,
                "admin_restaurant_manager_requests_page",
                exc.message,
                query_name="error",
                restaurant_id=restaurant_id,
            ),
            status_code=Config.HttpStatus.FOUND,
        )

    return RedirectResponse(
        _url_with_message(
            request,
            "admin_restaurant_manager_requests_page",
            "Manager 신청을 승인했습니다.",
            restaurant_id=restaurant_id,
        ),
        status_code=Config.HttpStatus.FOUND,
    )


@router.get(
    "/restaurants/{restaurant_id}/edit",
    response_class=HTMLResponse,
    name="admin_edit_restaurant_page",
)
async def admin_edit_restaurant_page(
    request: Request,
    restaurant_id: int,
    message: str | None = None,
    error: str | None = None,
) -> Response:
    """Render the admin form for editing a registered restaurant."""
    session_or_redirect = _get_admin_page_session(request)
    if isinstance(session_or_redirect, RedirectResponse):
        return session_or_redirect

    session = session_or_redirect
    error_message = error
    restaurant: dict[str, Any] | None = None
    form_values: dict[str, Any] = {}
    try:
        data = await meal_service_client.get_restaurant_detail(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
        )
        restaurant = _response_data(data)
        form_values = _restaurant_form_values(restaurant)
    except MealServiceError as exc:
        if error_message is None:
            error_message = exc.message

    return _render_restaurant_form(
        request,
        session,
        restaurant=restaurant,
        restaurant_id=restaurant_id,
        form_values=form_values,
        page_title="관리자 등록 식당 수정",
        page_description="저장된 식당 정보를 Web UI에서 수정하고 삭제할 수 있습니다.",
        submit_label="변경 저장",
        action_url=str(
            request.url_for("update_admin_restaurant", restaurant_id=restaurant_id)
        ),
        back_url=str(request.url_for("admin_restaurants_page")),
        error_message=error_message,
        success_message=message,
    )


@router.post(
    "/restaurants/{restaurant_id}/edit",
    response_class=HTMLResponse,
    name="update_admin_restaurant",
)
async def update_admin_restaurant(request: Request, restaurant_id: int) -> Response:
    """Update a registered restaurant from the admin form."""
    session, form_values = await _require_admin_post_session(request)
    try:
        data = await meal_service_client.update_restaurant_from_form(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
            form_data=form_values,
        )
        restaurant = _response_data(data)
        rendered_form_values = _restaurant_form_values(restaurant)
    except MealServiceError as exc:
        return _render_restaurant_form(
            request,
            session,
            restaurant=None,
            restaurant_id=restaurant_id,
            form_values=form_values,
            page_title="관리자 등록 식당 수정",
            page_description="저장된 식당 정보를 Web UI에서 수정하고 삭제할 수 있습니다.",
            submit_label="변경 저장",
            action_url=str(
                request.url_for("update_admin_restaurant", restaurant_id=restaurant_id)
            ),
            back_url=str(request.url_for("admin_restaurants_page")),
            status_code=exc.status_code,
            error_message=exc.message,
        )

    return _render_restaurant_form(
        request,
        session,
        restaurant=restaurant,
        restaurant_id=restaurant_id,
        form_values=rendered_form_values,
        page_title="관리자 등록 식당 수정",
        page_description="저장된 식당 정보를 Web UI에서 수정하고 삭제할 수 있습니다.",
        submit_label="변경 저장",
        action_url=str(
            request.url_for("update_admin_restaurant", restaurant_id=restaurant_id)
        ),
        back_url=str(request.url_for("admin_restaurants_page")),
        success_message="식당 정보를 저장했습니다.",
    )


@router.post(
    "/restaurants/{restaurant_id}/delete",
    response_class=HTMLResponse,
    name="delete_admin_restaurant",
)
async def delete_admin_restaurant(request: Request, restaurant_id: int) -> Response:
    """Delete a registered restaurant from the admin workflow."""
    session, form_values = await _require_admin_post_session(request)
    if form_values.get("confirm_delete") != "true":
        return RedirectResponse(
            _url_with_message(
                request,
                "admin_edit_restaurant_page",
                "삭제 확인을 선택해주세요.",
                query_name="error",
                restaurant_id=restaurant_id,
            ),
            status_code=Config.HttpStatus.FOUND,
        )
    try:
        await meal_service_client.delete_restaurant(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
        )
    except MealServiceError as exc:
        return RedirectResponse(
            _url_with_message(
                request,
                "admin_edit_restaurant_page",
                exc.message,
                query_name="error",
                restaurant_id=restaurant_id,
            ),
            status_code=Config.HttpStatus.FOUND,
        )

    return RedirectResponse(
        _url_with_message(
            request,
            "admin_restaurants_page",
            "식당을 삭제했습니다.",
        ),
        status_code=Config.HttpStatus.FOUND,
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

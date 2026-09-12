"""Admin workflow routes for sandol_meal_web."""

import asyncio
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, Response

from app.config import Config
from app.services import page_helpers as ph
from app.services import view_models as vm
from app.services.meal_client import MealServiceError, meal_service_client
from app.services.session_service import SessionData

from app.routers.owner import MANAGER_REJECT_UNSUPPORTED

router = APIRouter(prefix="/admin", tags=["Admin"])

ESTABLISHMENT_TYPE_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    vm.ESTABLISHMENT_TYPE_LABELS.items()
)
BUILDING_OPTIONS: tuple[str, ...] = ("TIP", "중앙", "E동", "산학융합관")
MEAL_TYPE_OPTIONS: tuple[tuple[str, str], ...] = tuple(vm.MEAL_TYPE_LABELS.items())

#: Latest-meal cards shown on the dashboard.
DASHBOARD_LATEST_LIMIT = 12


def _restaurant_form_values(restaurant: dict[str, Any] | None) -> dict[str, Any]:
    """Flatten restaurant API data into admin form field values."""
    if restaurant is None:
        return {}
    decorated = vm.decorate_restaurant(restaurant)
    decorated["is_campus"] = "true" if decorated.get("is_campus") else "false"
    return decorated


def _meal_form_values(meal: dict[str, Any] | None) -> dict[str, str]:
    """Flatten meal API data into form field values."""
    if meal is None:
        return {}
    menu_value = meal.get("menu")
    menu_lines = ""
    if isinstance(menu_value, list):
        menu_lines = "\n".join(str(item) for item in menu_value)
    return {
        "restaurant_id": str(meal.get("restaurant_id") or ""),
        "meal_type": str(meal.get("meal_type") or ""),
        "date": str(meal.get("date") or meal.get("served_date") or ""),
        "menu": menu_lines,
    }


def _meal_page_url(  # noqa: PLR0913
    request: Request,
    *,
    page: int,
    restaurant_id: int | None,
    start_date: str,
    end_date: str,
    restaurant_name: str,
    meal_type: str,
) -> str:
    """Build a meal-list URL while preserving active filters."""
    params: dict[str, str | int] = {"page": page}
    if restaurant_id is not None:
        params["restaurant_id"] = restaurant_id
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if restaurant_name:
        params["restaurant_name"] = restaurant_name
    if meal_type:
        params["meal_type"] = meal_type
    return f"{request.url_for('admin_meals_page')}?{urlencode(params)}"


async def _manager_request_count(session: SessionData, restaurant_id: int) -> int:
    """Count pending manager applications, treating failures as zero."""
    try:
        data = await meal_service_client.list_restaurant_manager_requests(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
            status="pending",
        )
    except MealServiceError:
        return 0
    return len(ph.request_items(data))


# --------------------------------------------------------------------------- #
# 대시보드 (A7 / A8)
# --------------------------------------------------------------------------- #


@router.get("", response_class=HTMLResponse, name="admin_dashboard_page")
async def admin_dashboard_page(
    request: Request,
    message: str | None = None,
    error: str | None = None,
) -> Response:
    """Render the admin operations dashboard."""
    session = ph.page_session(request, admin=True)
    error_message = error
    restaurants: list[dict[str, Any]] = []
    latest_meals: list[dict[str, Any]] = []
    pending_request_count = 0
    pending_manager_request_count = 0
    today_meal_count = 0
    iso_today = vm.today_iso()

    try:
        restaurant_data = await meal_service_client.list_restaurants(
            user_id=session["user_id"],
            size=100,
        )
        restaurants = ph.request_items(restaurant_data)

        request_data = await meal_service_client.list_requests(
            user_id=session["user_id"]
        )
        pending_request_count = len(
            vm.filter_by_status(ph.request_items(request_data), "pending")
        )

        counts = await asyncio.gather(
            *(
                _manager_request_count(session, restaurant["id"])
                for restaurant in restaurants
                if isinstance(restaurant.get("id"), int)
            )
        )
        pending_manager_request_count = sum(counts)

        latest_data = await meal_service_client.list_latest_meals(
            user_id=session["user_id"],
            size=100,
        )
        latest_meals = _latest_meal_cards(ph.request_items(latest_data), iso_today)
        today_meal_count = sum(1 for item in latest_meals if item["is_today"])
    except MealServiceError as exc:
        if error_message is None:
            error_message = exc.message

    return ph.render(
        request,
        session,
        "admin/dashboard.html",
        today_label=vm.today_label(),
        pending_request_count=pending_request_count,
        pending_manager_request_count=pending_manager_request_count,
        restaurant_count=len(restaurants),
        today_meal_count=today_meal_count,
        latest_meals=latest_meals[:DASHBOARD_LATEST_LIMIT],
        error_message=error_message,
        success_message=message,
    )


def _latest_meal_cards(
    meals: list[dict[str, Any]],
    iso_today: str,
) -> list[dict[str, Any]]:
    """Reduce per-meal-type latest records to one card per restaurant."""
    by_restaurant: dict[Any, dict[str, Any]] = {}
    for meal in ph.sort_meals(meals):
        restaurant_id = meal.get("restaurant_id")
        served_date = str(meal.get("date") or meal.get("served_date") or "")
        existing = by_restaurant.get(restaurant_id)
        # Prefer a record served today; otherwise keep the most recent one.
        if existing is None or (served_date == iso_today and not existing["is_today"]):
            decorated = vm.decorate_meal(meal)
            decorated["is_today"] = served_date == iso_today
            decorated["stale_label"] = "오늘 미등록"
            decorated["meta"] = f"최종 수정 {decorated.get('updated_at', '')}".strip()
            by_restaurant[restaurant_id] = decorated
    return sorted(
        by_restaurant.values(),
        key=lambda item: (item["is_today"], str(item.get("restaurant_name") or "")),
    )


@router.post("/meals/sync", response_class=HTMLResponse, name="admin_meal_sync")
async def admin_meal_sync(request: Request) -> Response:
    """Trigger a forced meal synchronization (A8)."""
    session, _ = await ph.post_session(request, admin=True)
    try:
        await meal_service_client.sync_meals(user_id=session["user_id"])
    except MealServiceError as exc:
        return ph.redirect(
            ph.url_with_message(
                request,
                "admin_dashboard_page",
                exc.message,
                query_name="error",
            )
        )
    return ph.redirect(
        ph.url_with_message(
            request,
            "admin_dashboard_page",
            "식단 동기화를 요청했습니다.",
        )
    )


# --------------------------------------------------------------------------- #
# 등록 요청
# --------------------------------------------------------------------------- #


@router.get("/requests", response_class=HTMLResponse, name="admin_requests_page")
async def admin_requests_page(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    message: str | None = None,
) -> Response:
    """Render all restaurant submission requests visible to an admin."""
    session = ph.page_session(request, admin=True)
    error_message = None
    requests: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    status_filter = status if status in {"pending", "approved", "rejected"} else ""
    try:
        data = await meal_service_client.list_requests(user_id=session["user_id"])
        all_requests = [vm.decorate_request(item) for item in ph.request_items(data)]
        counts = vm.status_counts(all_requests)
        requests = vm.search_requests(
            vm.filter_by_status(all_requests, status_filter),
            q,
        )
    except MealServiceError as exc:
        error_message = exc.message

    return ph.render(
        request,
        session,
        "admin/requests.html",
        requests=requests,
        status_filter=status_filter,
        status_counts=counts,
        query=q or "",
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
    error: str | None = None,
) -> Response:
    """Render a single restaurant submission request for admin review."""
    session = ph.page_session(request, admin=True)
    error_message = error
    restaurant_request: dict[str, Any] = {"id": request_id}
    try:
        data = await meal_service_client.get_request_detail(
            user_id=session["user_id"],
            request_id=request_id,
        )
        restaurant_request = vm.decorate_request(ph.response_data(data))
    except MealServiceError as exc:
        if error_message is None:
            error_message = exc.message

    return ph.render(
        request,
        session,
        "admin/request_detail.html",
        req=restaurant_request,
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
    session, _ = await ph.post_session(request, admin=True)
    try:
        _ = await meal_service_client.approve_request(
            user_id=session["user_id"],
            request_id=request_id,
        )
    except MealServiceError as exc:
        return ph.redirect(
            ph.url_with_message(
                request,
                "admin_request_detail_page",
                exc.message,
                query_name="error",
                request_id=request_id,
            )
        )
    return ph.redirect(
        ph.url_with_message(
            request,
            "admin_request_detail_page",
            "등록 요청을 승인했습니다.",
            request_id=request_id,
        )
    )


@router.post(
    "/requests/{request_id}/reject",
    response_class=HTMLResponse,
    name="reject_admin_request",
)
async def reject_admin_request(request: Request, request_id: int) -> Response:
    """Reject a restaurant submission request after admin and CSRF checks."""
    session, form_values = await ph.post_session(request, admin=True)
    # The redesigned form posts `reason`; older forms posted `message`.
    rejection_message = form_values.get("reason") or form_values.get("message")
    if not isinstance(rejection_message, str) or not rejection_message.strip():
        return ph.redirect(
            ph.url_with_message(
                request,
                "admin_request_detail_page",
                "거부 사유는 필수 입력 사항입니다.",
                query_name="error",
                request_id=request_id,
            )
        )
    try:
        await meal_service_client.reject_request(
            user_id=session["user_id"],
            request_id=request_id,
            message=rejection_message,
        )
    except MealServiceError as exc:
        return ph.redirect(
            ph.url_with_message(
                request,
                "admin_request_detail_page",
                exc.message,
                query_name="error",
                request_id=request_id,
            )
        )
    return ph.redirect(
        ph.url_with_message(
            request,
            "admin_request_detail_page",
            "등록 요청을 거부했습니다.",
            request_id=request_id,
        )
    )


# --------------------------------------------------------------------------- #
# 등록 식당
# --------------------------------------------------------------------------- #


@router.get("/restaurants", response_class=HTMLResponse, name="admin_restaurants_page")
async def admin_restaurants_page(  # noqa: PLR0913
    request: Request,
    name: str = "",
    establishment_type: str = "",
    is_campus: str = "",
    message: str | None = None,
) -> Response:
    """Render all registered restaurants for admin management."""
    session = ph.page_session(request, admin=True)
    error_message = None
    restaurants: list[dict[str, Any]] = []
    name_filter = name.strip()
    type_filter = (
        establishment_type if establishment_type in vm.ESTABLISHMENT_TYPE_LABELS else ""
    )
    campus_filter = is_campus if is_campus in {"true", "false"} else ""
    try:
        data = await meal_service_client.list_restaurants(
            user_id=session["user_id"],
            size=100,
            name=name_filter or None,
            establishment_type=type_filter or None,
            is_campus=campus_filter or None,
        )
        restaurants = [vm.decorate_restaurant(item) for item in ph.request_items(data)]
    except MealServiceError as exc:
        error_message = exc.message

    return ph.render(
        request,
        session,
        "admin/restaurants.html",
        restaurants=restaurants,
        name_filter=name_filter,
        establishment_type_filter=type_filter,
        is_campus_filter=campus_filter,
        has_filters=bool(name_filter or type_filter or campus_filter),
        error_message=error_message,
        success_message=message,
    )


@router.get(
    "/restaurants/new",
    response_class=HTMLResponse,
    name="admin_new_restaurant_page",
)
async def admin_new_restaurant_page(request: Request) -> Response:
    """Render the admin form for directly creating a registered restaurant."""
    session = ph.page_session(request, admin=True)
    return ph.render(
        request,
        session,
        "admin/restaurant_form.html",
        restaurant=None,
        form_values={},
        establishment_type_options=ESTABLISHMENT_TYPE_OPTIONS,
        building_options=BUILDING_OPTIONS,
        error_message=None,
    )


@router.post(
    "/restaurants", response_class=HTMLResponse, name="create_admin_restaurant"
)
async def create_admin_restaurant(request: Request) -> Response:
    """Create a registered restaurant directly from the admin form."""
    session, form_values = await ph.post_session(request, admin=True)
    try:
        data = await meal_service_client.create_restaurant_from_form(
            user_id=session["user_id"],
            form_data=form_values,
        )
        restaurant_id = ph.response_data(data).get("id")
        if not isinstance(restaurant_id, int):
            raise MealServiceError(
                Config.HttpStatus.INTERNAL_SERVER_ERROR,
                "생성된 식당 정보를 확인할 수 없습니다.",
            )
    except MealServiceError as exc:
        return ph.render(
            request,
            session,
            "admin/restaurant_form.html",
            status_code=exc.status_code,
            restaurant=None,
            form_values=form_values,
            establishment_type_options=ESTABLISHMENT_TYPE_OPTIONS,
            building_options=BUILDING_OPTIONS,
            error_message=exc.message,
        )

    return ph.redirect(
        ph.url_with_message(
            request,
            "admin_restaurant_detail_page",
            "식당을 생성했습니다.",
            restaurant_id=restaurant_id,
        )
    )


@router.get(
    "/restaurants/{restaurant_id}",
    response_class=HTMLResponse,
    name="admin_restaurant_detail_page",
)
async def admin_restaurant_detail_page(  # noqa: PLR0913
    request: Request,
    restaurant_id: int,
    tab: str = "info",
    status: str = "pending",
    message: str | None = None,
    error: str | None = None,
) -> Response:
    """Render restaurant info, managers, and manager applications in tabs."""
    session = ph.page_session(request, admin=True)
    error_message = error
    restaurant: dict[str, Any] = {"id": restaurant_id}
    managers: list[dict[str, Any]] = []
    manager_requests: list[dict[str, Any]] = []
    pending_count = 0
    if tab not in {"info", "managers", "requests"}:
        tab = "info"
    if status not in {"pending", "approved", "rejected", "all"}:
        status = "pending"

    try:
        detail = await meal_service_client.get_restaurant_detail(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
        )
        restaurant = vm.decorate_restaurant(ph.response_data(detail))
        pending_count = await _manager_request_count(session, restaurant_id)

        if tab == "managers":
            manager_data = await meal_service_client.list_restaurant_managers(
                user_id=session["user_id"],
                restaurant_id=restaurant_id,
            )
            managers = [
                vm.decorate_manager(item) for item in ph.request_items(manager_data)
            ]
        elif tab == "requests":
            request_data = await meal_service_client.list_restaurant_manager_requests(
                user_id=session["user_id"],
                restaurant_id=restaurant_id,
                status=status,
            )
            manager_requests = [
                vm.decorate_manager_request(item)
                for item in ph.request_items(request_data)
            ]
    except MealServiceError as exc:
        if error_message is None:
            error_message = exc.message

    return ph.render(
        request,
        session,
        "admin/restaurant_detail.html",
        restaurant=restaurant,
        managers=managers,
        manager_requests=manager_requests,
        tab=tab,
        request_status_filter=status,
        pending_request_count=pending_count,
        error_message=error_message,
        success_message=message,
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
    session = ph.page_session(request, admin=True)
    error_message = error
    restaurant: dict[str, Any] | None = None
    form_values: dict[str, Any] = {}
    try:
        data = await meal_service_client.get_restaurant_detail(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
        )
        restaurant = ph.response_data(data)
        form_values = _restaurant_form_values(restaurant)
    except MealServiceError as exc:
        if error_message is None:
            error_message = exc.message

    return ph.render(
        request,
        session,
        "admin/restaurant_form.html",
        restaurant=restaurant,
        restaurant_id=restaurant_id,
        form_values=form_values,
        establishment_type_options=ESTABLISHMENT_TYPE_OPTIONS,
        building_options=BUILDING_OPTIONS,
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
    session, form_values = await ph.post_session(request, admin=True)
    if _is_owner_only_change(form_values):
        return await _transfer_restaurant_owner(
            request, session, restaurant_id, form_values
        )
    try:
        data = await meal_service_client.update_restaurant_from_form(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
            form_data=form_values,
        )
        restaurant = ph.response_data(data)
    except MealServiceError as exc:
        return ph.render(
            request,
            session,
            "admin/restaurant_form.html",
            status_code=exc.status_code,
            restaurant=None,
            restaurant_id=restaurant_id,
            form_values=form_values,
            establishment_type_options=ESTABLISHMENT_TYPE_OPTIONS,
            building_options=BUILDING_OPTIONS,
            error_message=exc.message,
        )

    return ph.render(
        request,
        session,
        "admin/restaurant_form.html",
        restaurant=restaurant,
        restaurant_id=restaurant_id,
        form_values=_restaurant_form_values(restaurant),
        establishment_type_options=ESTABLISHMENT_TYPE_OPTIONS,
        building_options=BUILDING_OPTIONS,
        success_message="식당 정보를 저장했습니다.",
    )


def _is_owner_only_change(form_values: dict[str, Any]) -> bool:
    """Detect the detail page's owner-transfer form, which posts owner_user_id only."""
    return bool(form_values.get("owner_user_id")) and not form_values.get("name")


async def _transfer_restaurant_owner(
    request: Request,
    session: SessionData,
    restaurant_id: int,
    form_values: dict[str, Any],
) -> Response:
    """Change only the owner, preserving every other stored restaurant field."""
    try:
        detail = await meal_service_client.get_restaurant_detail(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
        )
        merged = _restaurant_form_values(ph.response_data(detail))
        merged["owner_user_id"] = form_values["owner_user_id"]
        await meal_service_client.update_restaurant_from_form(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
            form_data=merged,
        )
    except MealServiceError as exc:
        return ph.redirect(
            ph.url_with_message(
                request,
                "admin_restaurant_detail_page",
                exc.message,
                query_name="error",
                restaurant_id=restaurant_id,
            )
        )
    return ph.redirect(
        ph.url_with_message(
            request,
            "admin_restaurant_detail_page",
            "소유자를 변경했습니다.",
            restaurant_id=restaurant_id,
        )
    )


@router.post(
    "/restaurants/{restaurant_id}/delete",
    response_class=HTMLResponse,
    name="delete_admin_restaurant",
)
async def delete_admin_restaurant(request: Request, restaurant_id: int) -> Response:
    """Delete a registered restaurant from the admin workflow."""
    session, form_values = await ph.post_session(request, admin=True)
    if form_values.get("confirm_delete") != "true":
        return ph.redirect(
            ph.url_with_message(
                request,
                "admin_restaurant_detail_page",
                "삭제 확인을 선택해주세요.",
                query_name="error",
                restaurant_id=restaurant_id,
            )
        )
    try:
        await meal_service_client.delete_restaurant(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
        )
    except MealServiceError as exc:
        return ph.redirect(
            ph.url_with_message(
                request,
                "admin_restaurant_detail_page",
                exc.message,
                query_name="error",
                restaurant_id=restaurant_id,
            )
        )
    return ph.redirect(
        ph.url_with_message(request, "admin_restaurants_page", "식당을 삭제했습니다.")
    )


# --------------------------------------------------------------------------- #
# 매니저
# --------------------------------------------------------------------------- #


@router.post(
    "/restaurants/{restaurant_id}/managers",
    response_class=HTMLResponse,
    name="add_admin_restaurant_manager",
)
async def add_admin_restaurant_manager(
    request: Request, restaurant_id: int
) -> Response:
    """Register a restaurant manager from the admin form."""
    session, form_values = await ph.post_session(request, admin=True)
    try:
        await meal_service_client.add_restaurant_manager_from_form(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
            form_data=form_values,
        )
    except MealServiceError as exc:
        return ph.redirect(
            ph.url_with_message(
                request,
                "admin_restaurant_detail_page",
                exc.message,
                query_name="error",
                extra_query={"tab": "managers"},
                restaurant_id=restaurant_id,
            )
        )
    return ph.redirect(
        ph.url_with_message(
            request,
            "admin_restaurant_detail_page",
            "Manager를 등록했습니다.",
            extra_query={"tab": "managers"},
            restaurant_id=restaurant_id,
        )
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
    session, form_values = await ph.post_session(request, admin=True)
    manager_user_id = form_values.get("manager_user_id")
    try:
        await meal_service_client.remove_restaurant_manager(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
            manager_user_id=manager_user_id if isinstance(manager_user_id, str) else "",
        )
    except MealServiceError as exc:
        return ph.redirect(
            ph.url_with_message(
                request,
                "admin_restaurant_detail_page",
                exc.message,
                query_name="error",
                extra_query={"tab": "managers"},
                restaurant_id=restaurant_id,
            )
        )
    return ph.redirect(
        ph.url_with_message(
            request,
            "admin_restaurant_detail_page",
            "Manager를 해제했습니다.",
            extra_query={"tab": "managers"},
            restaurant_id=restaurant_id,
        )
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
    session, _ = await ph.post_session(request, admin=True)
    try:
        await meal_service_client.approve_restaurant_manager_request(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
            request_id=request_id,
        )
    except MealServiceError as exc:
        return ph.redirect(
            ph.url_with_message(
                request,
                "admin_restaurant_detail_page",
                exc.message,
                query_name="error",
                extra_query={"tab": "requests"},
                restaurant_id=restaurant_id,
            )
        )
    return ph.redirect(
        ph.url_with_message(
            request,
            "admin_restaurant_detail_page",
            "Manager 신청을 승인했습니다.",
            extra_query={"tab": "requests"},
            restaurant_id=restaurant_id,
        )
    )


@router.post(
    "/restaurants/{restaurant_id}/manager-requests/{request_id}/reject",
    response_class=HTMLResponse,
    name="reject_admin_restaurant_manager_request",
)
async def reject_admin_restaurant_manager_request(
    request: Request,
    restaurant_id: int,
    request_id: int,
) -> Response:
    """Reject a manager registration request from the admin workflow."""
    session, form_values = await ph.post_session(request, admin=True)
    reason = form_values.get("reason")
    try:
        await meal_service_client.reject_restaurant_manager_request(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
            request_id=request_id,
            reason=reason if isinstance(reason, str) else "",
        )
    except MealServiceError as exc:
        message = (
            MANAGER_REJECT_UNSUPPORTED
            if exc.status_code
            in {Config.HttpStatus.NOT_FOUND, Config.HttpStatus.METHOD_NOT_ALLOWED}
            else exc.message
        )
        return ph.redirect(
            ph.url_with_message(
                request,
                "admin_restaurant_detail_page",
                message,
                query_name="error",
                extra_query={"tab": "requests"},
                restaurant_id=restaurant_id,
            )
        )
    return ph.redirect(
        ph.url_with_message(
            request,
            "admin_restaurant_detail_page",
            "Manager 신청을 거절했습니다.",
            extra_query={"tab": "requests"},
            restaurant_id=restaurant_id,
        )
    )


# --------------------------------------------------------------------------- #
# 식단
# --------------------------------------------------------------------------- #


@router.get("/meals", response_class=HTMLResponse, name="admin_meals_page")
async def admin_meals_page(  # noqa: PLR0913
    request: Request,
    message: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    restaurant_id: str | None = None,
    restaurant_name: str = "",
    meal_type: str = "",
    start_date: str = "",
    end_date: str = "",
) -> Response:
    """Render filtered meal records for admin management."""
    session = ph.page_session(request, admin=True)
    error_message = None
    meals: list[dict[str, Any]] = []
    pagination_meta: dict[str, Any] = {}
    selected_restaurant_id = ph.optional_positive_int(restaurant_id)
    name_filter = restaurant_name.strip()
    type_filter = meal_type if meal_type in vm.MEAL_TYPE_LABELS else ""
    normalized_start, normalized_end = ph.normalize_date_range(
        start_date.strip(),
        end_date.strip(),
    )

    def page_url(target_page: int) -> str:
        return _meal_page_url(
            request,
            page=target_page,
            restaurant_id=selected_restaurant_id,
            start_date=normalized_start,
            end_date=normalized_end,
            restaurant_name=name_filter,
            meal_type=type_filter,
        )

    try:
        meals_page, total = await ph.load_filtered_meals(
            session,
            restaurant_id=selected_restaurant_id,
            start_date=normalized_start,
            end_date=normalized_end,
            page=page,
            size=ph.MEALS_PAGE_SIZE,
            restaurant_name=name_filter,
            meal_type=type_filter,
        )
        total_pages = max(1, (total + ph.MEALS_PAGE_SIZE - 1) // ph.MEALS_PAGE_SIZE)
        if total > 0 and page > total_pages:
            return ph.redirect(page_url(total_pages))
        meals = [vm.decorate_meal(meal) for meal in meals_page]
        pagination_meta = {
            "page": page,
            "size": ph.MEALS_PAGE_SIZE,
            "total": total,
        }
    except MealServiceError as exc:
        error_message = exc.message

    return ph.render(
        request,
        session,
        "admin/meals.html",
        meals=meals,
        pagination=ph.pagination_context(
            pagination_meta,
            fallback_page=page,
            page_url=page_url,
        ),
        selected_restaurant_id=str(selected_restaurant_id or ""),
        restaurant_name_filter=name_filter,
        meal_type_filter=type_filter,
        start_date=normalized_start,
        end_date=normalized_end,
        has_filters=bool(
            selected_restaurant_id
            or name_filter
            or type_filter
            or normalized_start
            or normalized_end
        ),
        error_message=error_message,
        success_message=message,
    )


@router.get("/meals/new", response_class=HTMLResponse, name="admin_new_meal_page")
async def admin_new_meal_page(
    request: Request,
    restaurant_id: str | None = None,
) -> Response:
    """Render the admin form for creating a meal record."""
    session = ph.page_session(request, admin=True)
    restaurant_options: list[dict[str, str]] = []
    error_message = None
    try:
        restaurant_options = await ph.load_restaurant_options(session)
    except MealServiceError as exc:
        error_message = exc.message

    return ph.render(
        request,
        session,
        "admin/meal_form.html",
        meal=None,
        restaurant_options=restaurant_options,
        selected_restaurant_id=restaurant_id or "",
        form_values={"date": vm.today_iso()},
        meal_type_options=MEAL_TYPE_OPTIONS,
        today_iso=vm.today_iso(),
        error_message=error_message,
    )


@router.post("/meals", response_class=HTMLResponse, name="create_admin_meal")
async def create_admin_meal(request: Request) -> Response:
    """Create a meal from the admin form."""
    session, form_values = await ph.post_session(request, admin=True)
    try:
        data = await meal_service_client.create_meal_from_form(
            user_id=session["user_id"],
            form_data=form_values,
        )
        meal_id = ph.response_data(data).get("id")
        if not isinstance(meal_id, int):
            raise MealServiceError(
                Config.HttpStatus.INTERNAL_SERVER_ERROR,
                "생성된 식단 정보를 확인할 수 없습니다.",
            )
    except MealServiceError as exc:
        restaurant_options: list[dict[str, str]] = []
        try:
            restaurant_options = await ph.load_restaurant_options(session)
        except MealServiceError:
            restaurant_options = []
        return ph.render(
            request,
            session,
            "admin/meal_form.html",
            status_code=exc.status_code,
            meal=None,
            restaurant_options=restaurant_options,
            selected_restaurant_id=str(form_values.get("restaurant_id") or ""),
            form_values=form_values,
            meal_type_options=MEAL_TYPE_OPTIONS,
            today_iso=vm.today_iso(),
            error_message=exc.message,
        )

    return ph.redirect(
        ph.url_with_message(request, "admin_meals_page", "식단을 등록했습니다.")
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
    session = ph.page_session(request, admin=True)
    error_message = None
    meal: dict[str, Any] | None = None
    form_values: dict[str, Any] = {}
    restaurant_options: list[dict[str, str]] = []
    try:
        restaurant_options = await ph.load_restaurant_options(session)
        data = await meal_service_client.get_meal_detail(
            user_id=session["user_id"],
            meal_id=meal_id,
        )
        meal = ph.response_data(data)
        form_values = _meal_form_values(meal)
    except MealServiceError as exc:
        error_message = exc.message

    return ph.render(
        request,
        session,
        "admin/meal_form.html",
        meal=meal,
        meal_id=meal_id,
        restaurant_options=restaurant_options,
        selected_restaurant_id=str(form_values.get("restaurant_id") or ""),
        form_values=form_values,
        meal_type_options=MEAL_TYPE_OPTIONS,
        today_iso=vm.today_iso(),
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
    session, form_values = await ph.post_session(request, admin=True)
    try:
        data = await meal_service_client.update_meal_from_form(
            user_id=session["user_id"],
            meal_id=meal_id,
            form_data=form_values,
        )
        meal = ph.response_data(data)
    except MealServiceError as exc:
        return ph.render(
            request,
            session,
            "admin/meal_form.html",
            status_code=exc.status_code,
            meal=None,
            meal_id=meal_id,
            restaurant_options=[],
            selected_restaurant_id=str(form_values.get("restaurant_id") or ""),
            form_values=form_values,
            meal_type_options=MEAL_TYPE_OPTIONS,
            today_iso=vm.today_iso(),
            error_message=exc.message,
        )

    return ph.render(
        request,
        session,
        "admin/meal_form.html",
        meal=meal,
        meal_id=meal_id,
        restaurant_options=[],
        selected_restaurant_id=str(meal.get("restaurant_id") or ""),
        form_values=_meal_form_values(meal),
        meal_type_options=MEAL_TYPE_OPTIONS,
        today_iso=vm.today_iso(),
        success_message="식단 정보를 저장했습니다.",
    )


@router.post(
    "/meals/{meal_id}/delete",
    response_class=HTMLResponse,
    name="delete_admin_meal",
)
async def delete_admin_meal(request: Request, meal_id: int) -> Response:
    """Delete a meal record from the admin workflow (A1)."""
    session, _ = await ph.post_session(request, admin=True)
    try:
        await meal_service_client.delete_meal(
            user_id=session["user_id"],
            meal_id=meal_id,
        )
    except MealServiceError as exc:
        return ph.redirect(
            ph.url_with_message(
                request,
                "admin_meals_page",
                exc.message,
                query_name="error",
            )
        )
    return ph.redirect(
        ph.url_with_message(request, "admin_meals_page", "식단을 삭제했습니다.")
    )

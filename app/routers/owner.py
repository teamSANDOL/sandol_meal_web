"""Owner workflow routes for sandol_meal_web."""

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.config import Config
from app.services import page_helpers as ph
from app.services import view_models as vm
from app.services.meal_client import MealServiceError, meal_service_client
from app.services.session_service import SessionData, has_admin_role

router = APIRouter(prefix="/owner", tags=["Owner"])

#: meal-service has no manager-request rejection endpoint yet (spec item B1).
MANAGER_REJECT_UNSUPPORTED = (
    "매니저 신청 거절은 아직 학식 서비스에서 지원하지 않습니다. "
    "승인만 가능하며, 거절 기능은 준비 중입니다."
)

_MEAL_HISTORY_COUNT = 20


async def _owned_restaurants(session: SessionData) -> list[dict[str, Any]]:
    """List restaurants the current user owns or manages."""
    user_id = session["user_id"]
    data = await meal_service_client.list_restaurants(
        user_id=user_id,
        owner_user_id=None if has_admin_role(session) else user_id,
        manager_user_id=None if has_admin_role(session) else user_id,
        size=100,
    )
    return [vm.decorate_restaurant(item) for item in ph.request_items(data)]


async def _pending_manager_request_count(
    session: SessionData, restaurant_id: int
) -> int:
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


async def _has_today_meal(session: SessionData, restaurant_id: int) -> bool:
    """Return whether the restaurant already has a meal for today."""
    iso_today = vm.today_iso()
    try:
        data = await meal_service_client.list_meals_by_restaurant(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
            start_date=iso_today,
            end_date=iso_today,
            size=1,
        )
    except MealServiceError:
        return False
    return bool(ph.request_items(data))


async def _today_meals(
    session: SessionData, restaurant_id: int
) -> list[dict[str, Any]]:
    """Return every meal served today for one restaurant."""
    iso_today = vm.today_iso()
    data = await meal_service_client.list_meals_by_restaurant(
        user_id=session["user_id"],
        restaurant_id=restaurant_id,
        start_date=iso_today,
        end_date=iso_today,
        size=100,
    )
    return ph.request_items(data)


def _selected_restaurant(
    restaurants: list[dict[str, Any]],
    restaurant_id: int | None,
) -> dict[str, Any] | None:
    """Pick the requested restaurant, falling back to the first available one."""
    if not restaurants:
        return None
    if restaurant_id is not None:
        for restaurant in restaurants:
            if restaurant.get("id") == restaurant_id:
                return restaurant
    return restaurants[0]


# --------------------------------------------------------------------------- #
# 내 식당
# --------------------------------------------------------------------------- #


@router.get("/restaurants", response_class=HTMLResponse, name="owner_restaurants_page")
async def owner_restaurants_page(
    request: Request,
    message: str | None = None,
    error: str | None = None,
) -> Response:
    """Render restaurants the current user owns or manages."""
    session = ph.page_session(request)
    error_message = error
    restaurants: list[dict[str, Any]] = []
    manager_apply_options: list[dict[str, str]] = []

    try:
        restaurants = await _owned_restaurants(session)
        stats = await asyncio.gather(
            *(
                asyncio.gather(
                    _has_today_meal(session, restaurant["id"]),
                    _pending_manager_request_count(session, restaurant["id"]),
                )
                for restaurant in restaurants
                if isinstance(restaurant.get("id"), int)
            )
        )
        for restaurant, (has_today, pending) in zip(restaurants, stats, strict=False):
            restaurant["is_owner"] = (
                restaurant.get("owner_user_id") == session["user_id"]
            )
            restaurant["has_today_meal"] = has_today
            restaurant["pending_manager_requests"] = pending

        owned_ids = {restaurant.get("id") for restaurant in restaurants}
        manager_apply_options = [
            option
            for option in await ph.load_restaurant_options(session)
            if int(option["id"]) not in owned_ids
        ]
    except MealServiceError as exc:
        if error_message is None:
            error_message = exc.message

    return ph.render(
        request,
        session,
        "owner/restaurants.html",
        restaurants=restaurants,
        manager_apply_options=manager_apply_options,
        error_message=error_message,
        success_message=message,
    )


@router.get(
    "/restaurants/{restaurant_id}",
    response_class=HTMLResponse,
    name="owner_restaurant_detail_page",
)
async def owner_restaurant_detail_page(  # noqa: PLR0913
    request: Request,
    restaurant_id: int,
    tab: str = "info",
    status: str = "pending",
    message: str | None = None,
    error: str | None = None,
) -> Response:
    """Render restaurant info, managers, and manager applications in tabs."""
    session = ph.page_session(request)
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
        pending_count = await _pending_manager_request_count(session, restaurant_id)

        if tab == "managers":
            # Listing managers is admin-only in meal-service today (spec item B3).
            try:
                manager_data = await meal_service_client.list_restaurant_managers(
                    user_id=session["user_id"],
                    restaurant_id=restaurant_id,
                )
                managers = [
                    vm.decorate_manager(item) for item in ph.request_items(manager_data)
                ]
            except MealServiceError as exc:
                error_message = error_message or exc.message
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
        "owner/restaurant_detail.html",
        restaurant=restaurant,
        managers=managers,
        manager_requests=manager_requests,
        tab=tab,
        request_status_filter=status,
        pending_request_count=pending_count,
        # meal-service PATCH /restaurants/{id} is admin-only (spec item B2).
        can_edit=False,
        error_message=error_message,
        success_message=message,
    )


@router.post(
    "/restaurants/{restaurant_id}/managers",
    response_class=HTMLResponse,
    name="add_owner_restaurant_manager",
)
async def add_owner_restaurant_manager(
    request: Request, restaurant_id: int
) -> Response:
    """Register a manager directly from the owner workflow."""
    session, form_values = await ph.post_session(request)
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
                "owner_restaurant_detail_page",
                exc.message,
                query_name="error",
                extra_query={"tab": "managers"},
                restaurant_id=restaurant_id,
            )
        )
    return ph.redirect(
        ph.url_with_message(
            request,
            "owner_restaurant_detail_page",
            "Manager를 등록했습니다.",
            extra_query={"tab": "managers"},
            restaurant_id=restaurant_id,
        )
    )


@router.post(
    "/restaurants/{restaurant_id}/managers/delete",
    response_class=HTMLResponse,
    name="delete_owner_restaurant_manager",
)
async def delete_owner_restaurant_manager(
    request: Request,
    restaurant_id: int,
) -> Response:
    """Remove a manager from the owner workflow."""
    session, form_values = await ph.post_session(request)
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
                "owner_restaurant_detail_page",
                exc.message,
                query_name="error",
                extra_query={"tab": "managers"},
                restaurant_id=restaurant_id,
            )
        )
    return ph.redirect(
        ph.url_with_message(
            request,
            "owner_restaurant_detail_page",
            "Manager를 해제했습니다.",
            extra_query={"tab": "managers"},
            restaurant_id=restaurant_id,
        )
    )


@router.post(
    "/restaurants/{restaurant_id}/manager-requests/{request_id}/approve",
    response_class=HTMLResponse,
    name="approve_owner_restaurant_manager_request",
)
async def approve_owner_restaurant_manager_request(
    request: Request,
    restaurant_id: int,
    request_id: int,
) -> Response:
    """Approve a manager request from the owner workflow."""
    session, _ = await ph.post_session(request)
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
                "owner_restaurant_detail_page",
                exc.message,
                query_name="error",
                extra_query={"tab": "requests"},
                restaurant_id=restaurant_id,
            )
        )
    return ph.redirect(
        ph.url_with_message(
            request,
            "owner_restaurant_detail_page",
            "Manager 신청을 승인했습니다.",
            extra_query={"tab": "requests"},
            restaurant_id=restaurant_id,
        )
    )


@router.post(
    "/restaurants/{restaurant_id}/manager-requests/{request_id}/reject",
    response_class=HTMLResponse,
    name="reject_owner_restaurant_manager_request",
)
async def reject_owner_restaurant_manager_request(
    request: Request,
    restaurant_id: int,
    request_id: int,
) -> Response:
    """Reject a manager request from the owner workflow."""
    session, form_values = await ph.post_session(request)
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
                "owner_restaurant_detail_page",
                message,
                query_name="error",
                extra_query={"tab": "requests"},
                restaurant_id=restaurant_id,
            )
        )
    return ph.redirect(
        ph.url_with_message(
            request,
            "owner_restaurant_detail_page",
            "Manager 신청을 거절했습니다.",
            extra_query={"tab": "requests"},
            restaurant_id=restaurant_id,
        )
    )


@router.post(
    "/manager-requests",
    response_class=HTMLResponse,
    name="create_manager_request_web",
)
async def create_manager_request_web(request: Request) -> Response:
    """Apply to become a manager of another restaurant."""
    session, form_values = await ph.post_session(request)
    restaurant_id = ph.optional_positive_int(
        str(form_values.get("restaurant_id") or "")
    )
    if restaurant_id is None:
        return ph.redirect(
            ph.url_with_message(
                request,
                "owner_restaurants_page",
                "매니저 신청할 식당을 선택해주세요.",
                query_name="error",
            )
        )
    try:
        await meal_service_client.create_restaurant_manager_request(
            user_id=session["user_id"],
            restaurant_id=restaurant_id,
        )
    except MealServiceError as exc:
        return ph.redirect(
            ph.url_with_message(
                request,
                "owner_restaurants_page",
                exc.message,
                query_name="error",
            )
        )
    return ph.redirect(
        ph.url_with_message(
            request,
            "owner_restaurants_page",
            "매니저 신청을 보냈습니다. 소유자 승인을 기다려주세요.",
        )
    )


# --------------------------------------------------------------------------- #
# 식단 관리 (A2)
# --------------------------------------------------------------------------- #


@router.get("/meals", response_class=HTMLResponse, name="owner_meals_page")
async def owner_meals_page(
    request: Request,
    restaurant_id: str | None = None,
    message: str | None = None,
    error: str | None = None,
) -> Response:
    """Render today's meal slots and recent meal history for one restaurant."""
    session = ph.page_session(request)
    error_message = error
    restaurants: list[dict[str, Any]] = []
    meals: list[dict[str, Any]] = []
    today_slots: list[dict[str, Any]] = []
    selected_id: int | None = None

    try:
        restaurants = await _owned_restaurants(session)
        selected = _selected_restaurant(
            restaurants,
            ph.optional_positive_int(restaurant_id),
        )
        candidate_id = selected.get("id") if selected is not None else None
        if isinstance(candidate_id, int):
            selected_id = candidate_id
            today_slots = vm.meal_slots(await _today_meals(session, selected_id))
            history, _total = await ph.load_filtered_meals(
                session,
                restaurant_id=selected_id,
                start_date="",
                end_date="",
                page=1,
                size=_MEAL_HISTORY_COUNT,
            )
            meals = [vm.decorate_meal(meal) for meal in history]
    except MealServiceError as exc:
        if error_message is None:
            error_message = exc.message

    return ph.render(
        request,
        session,
        "owner/meals.html",
        restaurants=restaurants,
        selected_restaurant_id=selected_id,
        today_slots=today_slots,
        meals=meals,
        today_label=vm.today_label(),
        error_message=error_message,
        success_message=message,
    )


@router.get("/meals/new", response_class=HTMLResponse, name="owner_new_meal_page")
async def owner_new_meal_page(
    request: Request,
    restaurant_id: str | None = None,
    meal_type: str | None = None,
) -> Response:
    """Render the owner form for creating a meal record."""
    session = ph.page_session(request)
    restaurants: list[dict[str, Any]] = []
    error_message = None
    selected_id: int | None = None
    try:
        restaurants = await _owned_restaurants(session)
        selected = _selected_restaurant(
            restaurants,
            ph.optional_positive_int(restaurant_id),
        )
        if selected is not None:
            selected_id = selected.get("id")
    except MealServiceError as exc:
        error_message = exc.message

    form_values: dict[str, Any] = {"date": vm.today_iso()}
    if meal_type in vm.MEAL_TYPE_LABELS:
        form_values["meal_type"] = meal_type

    return ph.render(
        request,
        session,
        "owner/meal_form.html",
        meal=None,
        restaurants=restaurants,
        selected_restaurant_id=selected_id,
        form_values=form_values,
        today_iso=vm.today_iso(),
        error_message=error_message,
    )


@router.post("/meals", response_class=HTMLResponse, name="create_owner_meal")
async def create_owner_meal(request: Request) -> Response:
    """Create a meal from the owner form."""
    session, form_values = await ph.post_session(request)
    try:
        data = await meal_service_client.create_meal_from_form(
            user_id=session["user_id"],
            form_data=form_values,
        )
    except MealServiceError as exc:
        restaurants: list[dict[str, Any]] = []
        try:
            restaurants = await _owned_restaurants(session)
        except MealServiceError:
            restaurants = []
        return ph.render(
            request,
            session,
            "owner/meal_form.html",
            status_code=exc.status_code,
            meal=None,
            restaurants=restaurants,
            selected_restaurant_id=ph.optional_positive_int(
                str(form_values.get("restaurant_id") or "")
            ),
            form_values=form_values,
            today_iso=vm.today_iso(),
            error_message=exc.message,
        )

    restaurant_id = ph.response_data(data).get("restaurant_id")
    return ph.redirect(
        ph.url_with_message(
            request,
            "owner_meals_page",
            "식단을 올렸습니다. 학생들에게 바로 보여요!",
            extra_query={"restaurant_id": restaurant_id},
        )
    )


@router.get(
    "/meals/{meal_id}/edit",
    response_class=HTMLResponse,
    name="owner_edit_meal_page",
)
async def owner_edit_meal_page(
    request: Request,
    meal_id: int,
    message: str | None = None,
) -> Response:
    """Render the owner form for editing a meal record."""
    session = ph.page_session(request)
    error_message = None
    meal: dict[str, Any] | None = None
    form_values: dict[str, Any] = {}
    try:
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
        "owner/meal_form.html",
        meal=meal,
        restaurants=[],
        selected_restaurant_id=None,
        form_values=form_values,
        today_iso=vm.today_iso(),
        error_message=error_message,
        success_message=message,
    )


@router.post(
    "/meals/{meal_id}/edit",
    response_class=HTMLResponse,
    name="update_owner_meal",
)
async def update_owner_meal(request: Request, meal_id: int) -> Response:
    """Update a meal from the owner form."""
    session, form_values = await ph.post_session(request)
    try:
        data = await meal_service_client.update_meal_from_form(
            user_id=session["user_id"],
            meal_id=meal_id,
            form_data=form_values,
        )
    except MealServiceError as exc:
        return ph.render(
            request,
            session,
            "owner/meal_form.html",
            status_code=exc.status_code,
            meal=None,
            restaurants=[],
            selected_restaurant_id=None,
            form_values=form_values,
            today_iso=vm.today_iso(),
            error_message=exc.message,
        )

    restaurant_id = ph.response_data(data).get("restaurant_id")
    return ph.redirect(
        ph.url_with_message(
            request,
            "owner_meals_page",
            "식단을 수정했습니다.",
            extra_query={"restaurant_id": restaurant_id},
        )
    )


@router.post(
    "/meals/{meal_id}/delete",
    response_class=HTMLResponse,
    name="delete_owner_meal",
)
async def delete_owner_meal(request: Request, meal_id: int) -> Response:
    """Delete a meal from the owner workflow."""
    session, _ = await ph.post_session(request)
    try:
        await meal_service_client.delete_meal(
            user_id=session["user_id"],
            meal_id=meal_id,
        )
    except MealServiceError as exc:
        return ph.redirect(
            ph.url_with_message(
                request,
                "owner_meals_page",
                exc.message,
                query_name="error",
            )
        )
    return ph.redirect(
        ph.url_with_message(request, "owner_meals_page", "식단을 삭제했습니다.")
    )


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


# --------------------------------------------------------------------------- #
# 등록 요청
# --------------------------------------------------------------------------- #


@router.get("/requests", response_class=HTMLResponse, name="owner_requests_page")
async def owner_requests_page(
    request: Request,
    status: str | None = None,
    message: str | None = None,
) -> Response:
    """Render restaurant submission requests visible to the current owner."""
    session = ph.page_session(request)
    error_message = None
    requests: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    status_filter = status if status in {"pending", "approved", "rejected"} else ""
    try:
        data = await meal_service_client.list_requests(user_id=session["user_id"])
        all_requests = [vm.decorate_request(item) for item in ph.request_items(data)]
        counts = vm.status_counts(all_requests)
        requests = vm.filter_by_status(all_requests, status_filter)
    except MealServiceError as exc:
        error_message = exc.message

    return ph.render(
        request,
        session,
        "owner/requests.html",
        requests=requests,
        status_filter=status_filter,
        status_counts=counts,
        error_message=error_message,
        success_message=message,
    )


@router.get("/requests/new", response_class=HTMLResponse, name="new_owner_request_page")
async def new_owner_request_page(request: Request) -> Response:
    """Render the owner restaurant request creation form."""
    session = ph.page_session(request)
    return ph.render(
        request,
        session,
        "owner/new_request.html",
        form_values={},
        error_message=None,
    )


@router.post("/requests", response_class=HTMLResponse, name="create_owner_request")
async def create_owner_request(request: Request) -> Response:
    """Create an owner restaurant submission request from form values."""
    session, form_values = await ph.post_session(request)
    try:
        created = await meal_service_client.create_request_from_form(
            user_id=session["user_id"],
            form_data=form_values,
        )
    except MealServiceError as exc:
        return ph.render(
            request,
            session,
            "owner/new_request.html",
            status_code=exc.status_code,
            form_values=form_values,
            error_message=exc.message,
        )

    created_data = ph.response_data(created)
    request_id = created_data.get("request_id") or created_data.get("id")
    if isinstance(request_id, int):
        return ph.redirect(
            ph.url_with_message(
                request,
                "owner_request_detail_page",
                "등록 요청이 접수되었습니다.",
                request_id=request_id,
            )
        )
    return ph.redirect(
        ph.url_with_message(
            request,
            "owner_requests_page",
            "등록 요청이 접수되었습니다.",
        )
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
    session = ph.page_session(request)
    error_message = None
    restaurant_request: dict[str, Any] = {"id": request_id}
    try:
        data = await meal_service_client.get_request_detail(
            user_id=session["user_id"],
            request_id=request_id,
        )
        restaurant_request = vm.decorate_request(ph.response_data(data))
    except MealServiceError as exc:
        error_message = exc.message

    return ph.render(
        request,
        session,
        "owner/request_detail.html",
        req=restaurant_request,
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
    session, form_values = await ph.post_session(request)
    if form_values.get("confirm_delete") != "true":
        return ph.redirect(
            ph.url_with_message(
                request,
                "owner_request_detail_page",
                "삭제 확인을 선택해주세요.",
                query_name="error",
                request_id=request_id,
            )
        )
    try:
        await meal_service_client.delete_request(
            user_id=session["user_id"],
            request_id=request_id,
        )
    except MealServiceError as exc:
        return ph.redirect(
            ph.url_with_message(
                request,
                "owner_request_detail_page",
                exc.message,
                query_name="error",
                request_id=request_id,
            )
        )
    return ph.redirect(
        ph.url_with_message(
            request,
            "owner_requests_page",
            "등록 요청이 삭제되었습니다.",
        )
    )

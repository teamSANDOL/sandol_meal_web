"""Meal-service API client for sandol_meal_web."""

from collections.abc import Mapping
from typing import Any, Literal, TypedDict, cast

import httpx

from app.config import Config


EstablishmentType = Literal[
    "student",
    "fixed_menu_restaurant",
    "fixed_korean_buffet",
    "variable_korean_buffet",
]


class TimeRangePayload(TypedDict):
    """Meal-service time range payload."""

    start: str
    end: str


class LocationPayload(TypedDict, total=False):
    """Meal-service restaurant location payload."""

    is_campus: bool
    building: str | None
    map_links: dict[str, str] | None
    latitude: float | None
    longitude: float | None


class RestaurantRequestPayload(TypedDict, total=False):
    """Meal-service RestaurantRequest JSON payload."""

    name: str
    establishment_type: EstablishmentType
    price: int | None
    location: LocationPayload
    opening_time: TimeRangePayload
    break_time: TimeRangePayload | None
    breakfast_time: TimeRangePayload | None
    brunch_time: TimeRangePayload | None
    lunch_time: TimeRangePayload | None
    dinner_time: TimeRangePayload | None


class MealServiceError(Exception):
    """Controlled local exception for meal-service failures."""

    status_code: int
    message: str

    def __init__(self, status_code: int, message: str) -> None:
        """Store a template-safe status code and message."""
        self.status_code = status_code
        self.message = message
        super().__init__(message)


def _blank_to_none(value: Any) -> str | None:
    """Convert blank form values to None."""
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized


def _optional_float(value: Any) -> float | None:
    """Convert optional numeric form values to float."""
    normalized = _blank_to_none(value)
    if normalized is None:
        return None
    try:
        return float(normalized)
    except ValueError as exc:
        raise MealServiceError(
            Config.HttpStatus.BAD_REQUEST,
            "숫자 형식의 위치 값을 확인해주세요.",
        ) from exc


def _optional_int(value: Any) -> int | None:
    """Convert optional numeric form values to int."""
    normalized = _blank_to_none(value)
    if normalized is None:
        return None
    try:
        parsed = int(normalized)
    except ValueError as exc:
        raise MealServiceError(
            Config.HttpStatus.BAD_REQUEST,
            "가격은 숫자로 입력해주세요.",
        ) from exc
    if parsed <= 0:
        raise MealServiceError(
            Config.HttpStatus.BAD_REQUEST,
            "가격은 1원 이상의 양수여야 합니다.",
        )
    return parsed


def _form_bool(value: Any) -> bool:
    """Convert common HTML form boolean values."""
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "on", "yes", "y"}


def _time_range(start: Any, end: Any) -> TimeRangePayload | None:
    """Return a time range only when both endpoints are present."""
    start_value = _blank_to_none(start)
    end_value = _blank_to_none(end)
    if start_value is None and end_value is None:
        return None
    if start_value is None or end_value is None:
        raise MealServiceError(
            Config.HttpStatus.BAD_REQUEST,
            "시간 범위는 시작과 종료 시간을 모두 입력해주세요.",
        )
    return {"start": start_value, "end": end_value}


def build_restaurant_request_payload(
    form_data: Mapping[str, Any],
) -> RestaurantRequestPayload:
    """Map owner create form values to meal-service RestaurantRequest JSON."""
    name = _blank_to_none(form_data.get("name"))
    establishment_type = _blank_to_none(form_data.get("establishment_type"))
    opening_time = _time_range(
        form_data.get("opening_time_start"),
        form_data.get("opening_time_end"),
    )

    if name is None or establishment_type is None or opening_time is None:
        raise MealServiceError(
            Config.HttpStatus.BAD_REQUEST,
            "식당명, 식당 유형, 운영 시간은 필수입니다.",
        )
    if establishment_type not in {
        "student",
        "fixed_menu_restaurant",
        "fixed_korean_buffet",
        "variable_korean_buffet",
    }:
        raise MealServiceError(
            Config.HttpStatus.BAD_REQUEST,
            "식당 유형 값이 올바르지 않습니다.",
        )
    typed_establishment_type = cast(EstablishmentType, establishment_type)
    price = _optional_int(form_data.get("price"))
    if typed_establishment_type in {"fixed_korean_buffet", "variable_korean_buffet"} and price is None:
        raise MealServiceError(
            Config.HttpStatus.BAD_REQUEST,
            "한식 뷔페 유형은 1인 가격을 입력해야 합니다.",
        )

    map_links = {
        key: value
        for key, value in {
            "naver": _blank_to_none(form_data.get("naver_map_link")),
            "kakao": _blank_to_none(form_data.get("kakao_map_link")),
        }.items()
        if value is not None
    }
    location: LocationPayload = {
        "is_campus": _form_bool(form_data.get("is_campus")),
        "building": _blank_to_none(form_data.get("building")),
        "map_links": map_links or None,
        "latitude": _optional_float(form_data.get("latitude")),
        "longitude": _optional_float(form_data.get("longitude")),
    }

    return {
        "name": name,
        "establishment_type": typed_establishment_type,
        "price": price,
        "location": location,
        "opening_time": opening_time,
        "break_time": _time_range(
            form_data.get("break_time_start"), form_data.get("break_time_end")
        ),
        "breakfast_time": _time_range(
            form_data.get("breakfast_time_start"),
            form_data.get("breakfast_time_end"),
        ),
        "brunch_time": _time_range(
            form_data.get("brunch_time_start"), form_data.get("brunch_time_end")
        ),
        "lunch_time": _time_range(
            form_data.get("lunch_time_start"), form_data.get("lunch_time_end")
        ),
        "dinner_time": _time_range(
            form_data.get("dinner_time_start"), form_data.get("dinner_time_end")
        ),
    }


class MealServiceClient:
    """Typed async client for meal-service restaurant request workflows."""

    def __init__(
        self,
        *,
        base_url: str = Config.MEAL_SERVICE_BASE_URL,
        timeout_seconds: float = Config.MEAL_SERVICE_TIMEOUT_SECONDS,
    ) -> None:
        """Configure meal-service base URL and timeout."""
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _headers(self, user_id: str) -> dict[str, str]:
        """Build trusted outbound headers from a server-side session user id."""
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            raise MealServiceError(
                Config.HttpStatus.UNAUTHORIZED,
                "로그인 세션의 사용자 식별자가 없습니다.",
            )
        return {"X-User-ID": normalized_user_id}

    async def list_requests(
        self,
        *,
        user_id: str,
        page: int | None = None,
        size: int | None = None,
    ) -> dict[str, Any]:
        """List restaurant submission requests visible to the current user."""
        params = self._pagination_params(page=page, size=size)
        return await self._request_json(
            "GET",
            "/restaurants/requests",
            user_id=user_id,
            params=params,
        )

    async def get_request_detail(self, *, user_id: str, request_id: int) -> dict[str, Any]:
        """Return a single restaurant submission request."""
        return await self._request_json(
            "GET",
            f"/restaurants/requests/{request_id}",
            user_id=user_id,
        )

    async def create_request(
        self,
        *,
        user_id: str,
        payload: RestaurantRequestPayload,
    ) -> dict[str, Any]:
        """Create a restaurant submission request."""
        return await self._request_json(
            "POST",
            "/restaurants/requests",
            user_id=user_id,
            json=payload,
        )

    async def create_request_from_form(
        self,
        *,
        user_id: str,
        form_data: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Map owner form values and create a restaurant submission request."""
        return await self.create_request(
            user_id=user_id,
            payload=build_restaurant_request_payload(form_data),
        )

    async def delete_request(self, *, user_id: str, request_id: int) -> None:
        """Delete a restaurant submission request."""
        await self._request_empty(
            "DELETE",
            f"/restaurants/requests/{request_id}",
            user_id=user_id,
        )

    async def approve_request(self, *, user_id: str, request_id: int) -> dict[str, Any]:
        """Approve a restaurant submission request."""
        return await self._request_json(
            "POST",
            f"/restaurants/restaurants/{request_id}/approval",
            user_id=user_id,
        )

    async def reject_request(
        self,
        *,
        user_id: str,
        request_id: int,
        message: str,
    ) -> None:
        """Reject a restaurant submission request with a message."""
        rejection_message = _blank_to_none(message)
        if rejection_message is None:
            raise MealServiceError(
                Config.HttpStatus.BAD_REQUEST,
                "거부 사유는 필수 입력 사항입니다.",
            )
        await self._request_empty(
            "POST",
            f"/restaurants/restaurants/{request_id}/rejection",
            user_id=user_id,
            json={"message": rejection_message},
        )

    def _pagination_params(
        self, *, page: int | None, size: int | None
    ) -> dict[str, int] | None:
        """Build optional fastapi-pagination query parameters."""
        params: dict[str, int] = {}
        if page is not None:
            params["page"] = page
        if size is not None:
            params["size"] = size
        return params or None

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        user_id: str,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform an HTTP request and return a JSON object."""
        response = await self._request(
            method,
            path,
            user_id=user_id,
            params=params,
            json=json,
        )
        if not response.content:
            return {}
        data = response.json()
        if not isinstance(data, dict):
            raise MealServiceError(
                Config.HttpStatus.INTERNAL_SERVER_ERROR,
                "학식 서비스 응답 형식이 올바르지 않습니다.",
            )
        return data

    async def _request_empty(
        self,
        method: str,
        path: str,
        *,
        user_id: str,
        json: Mapping[str, Any] | None = None,
    ) -> None:
        """Perform an HTTP request that does not need a response body."""
        _ = await self._request(method, path, user_id=user_id, json=json)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        user_id: str,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        """Perform an outbound meal-service request with controlled errors."""
        headers = self._headers(user_id)
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.request(
                    method,
                    path,
                    headers=headers,
                    params=params,
                    json=json,
                )
                response.raise_for_status()
                return response
        except httpx.HTTPStatusError as exc:
            raise MealServiceError(
                exc.response.status_code,
                self._error_message(exc.response),
            ) from exc
        except httpx.RequestError as exc:
            raise MealServiceError(
                Config.HttpStatus.INTERNAL_SERVER_ERROR,
                "학식 서비스에 연결할 수 없습니다.",
            ) from exc

    def _error_message(self, response: httpx.Response) -> str:
        """Extract a controlled message from a meal-service error response."""
        try:
            data = response.json()
        except ValueError:
            return "학식 서비스 요청 처리 중 오류가 발생했습니다."

        detail = data.get("detail") if isinstance(data, dict) else None
        if isinstance(detail, str) and detail:
            return detail
        if isinstance(detail, list) and detail:
            return "입력값을 확인해주세요."
        message = data.get("message") if isinstance(data, dict) else None
        if isinstance(message, str) and message:
            return message
        return "학식 서비스 요청 처리 중 오류가 발생했습니다."


meal_service_client = MealServiceClient()

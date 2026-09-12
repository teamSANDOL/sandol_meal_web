"""Presentation helpers that adapt meal-service payloads to template context."""

from datetime import date, datetime
from typing import Any

from app.config import Config

ESTABLISHMENT_TYPE_LABELS: dict[str, str] = {
    "student": "교내 학생식당",
    "fixed_menu_restaurant": "고정메뉴 일반식당",
    "fixed_korean_buffet": "고정메뉴형 한식뷔페",
    "variable_korean_buffet": "메뉴 변경형 한식뷔페",
}

MEAL_TYPE_LABELS: dict[str, str] = {
    "breakfast": "아침",
    "brunch": "브런치",
    "lunch": "점심",
    "dinner": "저녁",
}

MEAL_TYPE_EMOJI: dict[str, str] = {
    "breakfast": "🌅",
    "brunch": "🥞",
    "lunch": "🍚",
    "dinner": "🌙",
}

#: Slots always rendered on the owner meal dashboard, in serving order.
DEFAULT_MEAL_SLOT_KEYS: tuple[str, ...] = ("breakfast", "lunch", "dinner")

DOW_LABELS: tuple[str, ...] = ("월", "화", "수", "목", "금", "토", "일")

_TIME_RANGE_FIELDS: tuple[str, ...] = (
    "opening_time",
    "break_time",
    "breakfast_time",
    "brunch_time",
    "lunch_time",
    "dinner_time",
)

_HHMM_LENGTH = 5


def today() -> date:
    """Return the current service-timezone date."""
    return datetime.now(Config.TZ).date()


def today_iso() -> str:
    """Return today's date as an ISO string for date inputs."""
    return today().isoformat()


def today_label() -> str:
    """Return a human-readable label for today."""
    value = today()
    return f"{value.month}월 {value.day}일 ({DOW_LABELS[value.weekday()]})"


def dow_label(served_date: Any) -> str:
    """Return the Korean weekday label for a served-date value."""
    parsed = parse_date(served_date)
    if parsed is None:
        return ""
    return DOW_LABELS[parsed.weekday()]


def parse_date(value: Any) -> date | None:
    """Parse a meal-service date value, returning None when unusable."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def format_timestamp(value: Any) -> str:
    """Format a meal-service timestamp as 'YYYY-MM-DD HH:MM'."""
    if not isinstance(value, str) or not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.strftime("%Y-%m-%d %H:%M")


def _clock_value(value: Any) -> str:
    """Normalize a time-range endpoint to the 'HH:MM' a time input expects."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if "T" in text:
        text = text.split("T", 1)[1]
    return text[:_HHMM_LENGTH]


def _flatten_time_ranges(source: dict[str, Any], target: dict[str, Any]) -> None:
    """Copy `{field}` time ranges into flat `{field}_start` / `{field}_end` keys."""
    for field in _TIME_RANGE_FIELDS:
        value = source.get(field)
        range_data = value if isinstance(value, dict) else {}
        target[f"{field}_start"] = _clock_value(range_data.get("start"))
        target[f"{field}_end"] = _clock_value(range_data.get("end"))


def _flatten_location(source: dict[str, Any], target: dict[str, Any]) -> None:
    """Copy the nested location object into flat template/form keys."""
    location = source.get("location")
    location_data = location if isinstance(location, dict) else {}
    map_links = location_data.get("map_links")
    map_link_data = map_links if isinstance(map_links, dict) else {}

    target["is_campus"] = bool(location_data.get("is_campus"))
    target["building"] = location_data.get("building") or ""
    target["naver_map_link"] = map_link_data.get("naver") or ""
    target["kakao_map_link"] = map_link_data.get("kakao") or ""
    target["latitude"] = location_data.get("latitude")
    target["longitude"] = location_data.get("longitude")


def decorate_restaurant(restaurant: dict[str, Any]) -> dict[str, Any]:
    """Flatten a restaurant payload and add display labels.

    The result doubles as `form_values` for the admin restaurant form, so the
    flat keys intentionally match the form field names.
    """
    decorated = dict(restaurant)
    establishment_type = str(restaurant.get("establishment_type") or "")
    decorated["establishment_type_label"] = ESTABLISHMENT_TYPE_LABELS.get(
        establishment_type,
        establishment_type,
    )
    profile = restaurant.get("owner_profile")
    profile_data = profile if isinstance(profile, dict) else {}
    decorated["owner_name"] = (
        profile_data.get("display_name") or restaurant.get("owner_user_id") or ""
    )
    _flatten_location(restaurant, decorated)
    _flatten_time_ranges(restaurant, decorated)
    return decorated


def decorate_request(restaurant_request: dict[str, Any]) -> dict[str, Any]:
    """Flatten a restaurant submission and normalize review metadata."""
    decorated = decorate_restaurant(restaurant_request)
    decorated["submitted_at"] = format_timestamp(
        restaurant_request.get("submitted_time")
    )
    decorated["reviewed_at"] = format_timestamp(restaurant_request.get("reviewed_time"))
    decorated["reject_reason"] = restaurant_request.get("rejection_message") or ""

    profile = restaurant_request.get("submitter_profile")
    profile_data = profile if isinstance(profile, dict) else {}
    submitter_user_id = restaurant_request.get("submitter_user_id") or profile_data.get(
        "user_id"
    )
    # Older meal-service responses only carry the internal integer `submitter`.
    submitter = restaurant_request.get("submitter")
    fallback = "" if submitter is None else f"사용자 #{submitter}"
    decorated["submitter_user_id"] = submitter_user_id or fallback
    decorated["submitter_name"] = (
        profile_data.get("display_name") or submitter_user_id or fallback
    )
    decorated["submitter_meta"] = (
        profile_data.get("email") or profile_data.get("username") or ""
    )
    return decorated


def decorate_meal(meal: dict[str, Any]) -> dict[str, Any]:
    """Add meal-type and weekday labels to a meal payload."""
    decorated = dict(meal)
    meal_type = str(meal.get("meal_type") or "")
    decorated["meal_type_label"] = MEAL_TYPE_LABELS.get(meal_type, meal_type)
    decorated["dow_label"] = dow_label(meal.get("date") or meal.get("served_date"))
    decorated["updated_at"] = format_timestamp(meal.get("updated_at"))
    return decorated


def decorate_manager(manager: dict[str, Any]) -> dict[str, Any]:
    """Map a manager row onto the display fields the manager list renders."""
    decorated = dict(manager)
    profile = manager.get("profile")
    profile_data = profile if isinstance(profile, dict) else {}
    decorated["name"] = profile_data.get("display_name") or manager.get("user_id") or ""
    return decorated


def decorate_manager_request(application: dict[str, Any]) -> dict[str, Any]:
    """Map a manager application onto the fields the request list renders."""
    decorated = dict(application)
    profile = application.get("applicant_profile")
    profile_data = profile if isinstance(profile, dict) else {}
    applicant_user_id = application.get("applicant_user_id") or profile_data.get(
        "user_id"
    )
    decorated["user_id"] = applicant_user_id or ""
    decorated["applicant_name"] = (
        profile_data.get("display_name") or applicant_user_id or ""
    )
    decorated["created_at"] = format_timestamp(application.get("submitted_time"))
    decorated["source_label"] = "매니저 신청"
    return decorated


def meal_slots(meals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build today's per-meal-type slots, appending brunch only when present."""
    by_type = {str(meal.get("meal_type")): meal for meal in meals}
    keys = list(DEFAULT_MEAL_SLOT_KEYS)
    if "brunch" in by_type:
        keys.insert(1, "brunch")
    return [
        {
            "key": key,
            "label": MEAL_TYPE_LABELS[key],
            "emoji": MEAL_TYPE_EMOJI[key],
            "meal": by_type.get(key),
        }
        for key in keys
    ]


def status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """Count items per status, plus an 'all' total, for status filter tabs."""
    counts: dict[str, int] = {"all": len(items)}
    for item in items:
        status = str(item.get("status") or "")
        counts[status] = counts.get(status, 0) + 1
    return counts


def filter_by_status(
    items: list[dict[str, Any]],
    status: str | None,
) -> list[dict[str, Any]]:
    """Filter items by status locally.

    meal-service does not expose a status query parameter on the submission
    list endpoint, so the tabs are resolved here.
    """
    if not status:
        return items
    return [item for item in items if str(item.get("status") or "") == status]


def search_requests(
    items: list[dict[str, Any]],
    query: str | None,
) -> list[dict[str, Any]]:
    """Filter submissions by restaurant name or submitter, case-insensitively."""
    if not query:
        return items
    needle = query.strip().lower()
    if not needle:
        return items
    return [
        item
        for item in items
        if needle in str(item.get("name") or "").lower()
        or needle in str(item.get("submitter_name") or "").lower()
        or needle in str(item.get("submitter_user_id") or "").lower()
    ]

"""Focused pagination tests for the meal list page helper."""

from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.services import page_helpers
from app.services.session_service import SessionData


ADMIN_SESSION: SessionData = {
    "user_id": "admin",
    "roles": ["admin"],
    "csrf_token": "test-csrf-token",
    "expires_at": 0,
    "token_metadata": {},
}


class _MealClientStub:
    """Capture list calls without making a meal-service request."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def list_meals(self, **kwargs: Any) -> dict[str, Any]:
        """Return a representative sixth page response."""
        self.calls.append(kwargs)
        return {
            "data": {"items": [{"id": 101}, {"id": 100}]},
            "meta": {"page": 6, "size": 20, "total": 121},
        }

    async def list_meals_by_restaurant(self, **kwargs: Any) -> dict[str, Any]:
        """Return a representative filtered restaurant page response."""
        self.calls.append(kwargs)
        return {
            "data": {"items": [{"id": 101}]},
            "meta": {"page": 1, "size": 20, "total": 1},
        }


class LoadFilteredMealsTests(IsolatedAsyncioTestCase):
    """Verify UI pagination is passed through to meal-service."""

    async def test_requests_actual_page_with_fixed_size(self) -> None:
        """The sixth UI page must not become an oversized first-page request."""
        client = _MealClientStub()
        with patch.object(page_helpers, "meal_service_client", client):
            meals, total = await page_helpers.load_filtered_meals(
                ADMIN_SESSION,
                restaurant_id=None,
                start_date="",
                end_date="",
                page=6,
                size=20,
            )

        self.assertEqual(
            client.calls,
            [
                {
                    "user_id": "admin",
                    "page": 6,
                    "size": 20,
                    "start_date": None,
                    "end_date": None,
                    "restaurant_name": None,
                    "meal_type": None,
                }
            ],
        )
        self.assertEqual(meals, [{"id": 101}, {"id": 100}])
        self.assertEqual(total, 121)

    async def test_passes_restaurant_and_meal_type_together(self) -> None:
        """A restaurant-scoped request retains the selected meal type filter."""
        client = _MealClientStub()
        with patch.object(page_helpers, "meal_service_client", client):
            meals, total = await page_helpers.load_filtered_meals(
                ADMIN_SESSION,
                restaurant_id=7,
                start_date="",
                end_date="",
                page=1,
                size=20,
                meal_type="lunch",
            )

        self.assertEqual(
            client.calls,
            [
                {
                    "user_id": "admin",
                    "restaurant_id": 7,
                    "page": 1,
                    "size": 20,
                    "start_date": None,
                    "end_date": None,
                    "meal_type": "lunch",
                }
            ],
        )
        self.assertEqual(meals, [{"id": 101}])
        self.assertEqual(total, 1)

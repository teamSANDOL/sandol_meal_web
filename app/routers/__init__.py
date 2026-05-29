"""Router exports for sandol_meal_web."""

from .admin import router as admin_router
from .auth import router as auth_router
from .owner import router as owner_router

__all__ = ["admin_router", "auth_router", "owner_router"]

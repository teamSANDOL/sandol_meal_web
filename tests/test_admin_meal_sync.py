"""Tests for the administrator archived workbook synchronization page."""

from typing import Any
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import main
from app.config import Config
from app.routers import admin


ADMIN_SESSION: dict[str, Any] = {
    "user_id": "meal-admin",
    "roles": [Config.REALM_GLOBAL_ADMIN_ROLE],
    "csrf_token": "admin-csrf-token",
    "expires_at": 0,
    "token_metadata": {},
}


UPLOADS = {
    "data": [
        {
            "upload_id": "latest-upload",
            "file_name": "latest.xlsx",
            "uploaded_at": "2026-09-21T08:00:00+09:00",
            "is_latest": True,
            "analysis_status": "completed",
            "sync_status": "completed",
            "period": {"start_date": "2026-09-21", "end_date": "2026-09-25"},
        },
        {
            "upload_id": "older-upload",
            "file_name": "older.xlsx",
            "uploaded_at": "2026-09-14T08:00:00+09:00",
            "is_latest": False,
            "analysis_status": "failed",
            "analysis_error_message": "날짜 헤더를 찾지 못했습니다.",
            "sync_status": "failed",
            "sync_error_message": "날짜 헤더를 찾지 못했습니다.",
        },
    ]
}


def _patch_admin_session(monkeypatch: Any) -> None:
    """Use an authenticated admin session without OIDC in template tests."""
    monkeypatch.setattr(admin.ph, "page_session", lambda *_args, **_kwargs: ADMIN_SESSION)


def test_admin_sync_page_lists_latest_and_previous_files(monkeypatch: Any) -> None:
    """The page exposes the newest default and detailed file statuses."""
    _patch_admin_session(monkeypatch)
    monkeypatch.setattr(
        admin.meal_service_client,
        "list_excel_uploads",
        AsyncMock(return_value=UPLOADS),
    )

    with TestClient(main.app) as client:
        response = client.get("/admin/meal-sync")

    assert response.status_code == 200
    assert "최신 업로드 파일 (기본값)" in response.text
    assert "latest.xlsx" in response.text
    assert "older.xlsx" in response.text
    assert "날짜 헤더를 찾지 못했습니다." in response.text


def test_admin_sync_page_shows_detailed_failure(monkeypatch: Any) -> None:
    """A selected-file sync failure displays its code and reason."""
    _patch_admin_session(monkeypatch)

    async def fake_post_session(*_args: Any, **_kwargs: Any) -> tuple[
        dict[str, Any], dict[str, Any]
    ]:
        return ADMIN_SESSION, {
            "csrf_token": ADMIN_SESSION["csrf_token"],
            "upload_id": "older-upload",
        }

    monkeypatch.setattr(admin.ph, "post_session", fake_post_session)
    monkeypatch.setattr(
        admin.meal_service_client,
        "sync_meals",
        AsyncMock(
            return_value={
                "data": {
                    "upload_id": "older-upload",
                    "file_name": "older.xlsx",
                    "sync_status": "failed",
                    "sync_source": "selected",
                    "sync_error_code": "no_menus",
                    "sync_error_message": "날짜 헤더, TIP/E동 식당 블록 또는 메뉴 셀을 찾지 못했습니다.",
                }
            }
        ),
    )
    monkeypatch.setattr(
        admin.meal_service_client,
        "list_excel_uploads",
        AsyncMock(return_value=UPLOADS),
    )

    with TestClient(main.app) as client:
        response = client.post("/admin/meal-sync")

    assert response.status_code == 200
    assert "동기화 실패" in response.text
    assert "no_menus" in response.text
    assert "날짜 헤더, TIP/E동 식당 블록 또는 메뉴 셀을 찾지 못했습니다." in response.text

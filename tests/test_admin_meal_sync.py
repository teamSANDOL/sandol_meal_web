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


def test_admin_sync_page_links_original_download(monkeypatch: Any) -> None:
    """Each archived file row offers a download link for its original."""
    _patch_admin_session(monkeypatch)
    monkeypatch.setattr(
        admin.meal_service_client,
        "list_excel_uploads",
        AsyncMock(return_value=UPLOADS),
    )

    with TestClient(main.app) as client:
        response = client.get("/admin/meal-sync")

    assert "/admin/meal-sync/uploads/latest-upload/file" in response.text
    assert "/admin/meal-sync/uploads/older-upload/file" in response.text


def test_admin_download_returns_original_workbook(monkeypatch: Any) -> None:
    """The original bytes and filename header from meal-service are relayed."""
    _patch_admin_session(monkeypatch)
    download = AsyncMock(
        return_value=(b"workbook", "attachment; filename*=utf-8''%EC%8B%9D.xlsx")
    )
    monkeypatch.setattr(admin.meal_service_client, "download_excel_upload", download)

    with TestClient(main.app) as client:
        response = client.get("/admin/meal-sync/uploads/latest-upload/file")

    assert response.status_code == 200
    assert response.content == b"workbook"
    assert "spreadsheetml" in response.headers["content-type"]
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["cache-control"] == "no-store"
    download.assert_awaited_once_with(user_id="meal-admin", upload_id="latest-upload")


def test_admin_download_failure_returns_to_sync_page(monkeypatch: Any) -> None:
    """A missing upload sends the admin back to the list with the reason."""
    _patch_admin_session(monkeypatch)
    monkeypatch.setattr(
        admin.meal_service_client,
        "download_excel_upload",
        AsyncMock(side_effect=admin.MealServiceError(404, "업로드 파일을 찾을 수 없습니다.")),
    )

    with TestClient(main.app) as client:
        response = client.get(
            "/admin/meal-sync/uploads/missing/file", follow_redirects=False
        )

    assert response.status_code == 302
    assert "/admin/meal-sync?error=" in response.headers["location"]


def test_non_admin_cannot_download(monkeypatch: Any) -> None:
    """An uploader-only session is refused before meal-service is called."""
    from app.services import page_helpers  # noqa: PLC0415

    session = {**ADMIN_SESSION, "roles": [Config.MEAL_UPLOADER_ROLE]}
    monkeypatch.setattr(page_helpers, "get_optional_session", lambda _request: session)
    download = AsyncMock(return_value=(b"workbook", None))
    monkeypatch.setattr(admin.meal_service_client, "download_excel_upload", download)

    with TestClient(main.app) as client:
        response = client.get(
            "/admin/meal-sync/uploads/latest-upload/file", follow_redirects=False
        )

    assert response.status_code == 403
    download.assert_not_awaited()

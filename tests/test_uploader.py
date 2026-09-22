"""Tests for the menu workbook upload result page."""

from io import BytesIO
from typing import Any
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

import main
from app.config import Config
from app.routers import uploader


UPLOAD_SESSION: dict[str, Any] = {
    "user_id": "meal-uploader",
    "roles": [Config.MEAL_UPLOADER_ROLE],
    "csrf_token": "test-csrf-token",
    "expires_at": 0,
    "token_metadata": {},
}


def _mock_upload_submission(monkeypatch: Any, response_data: dict[str, Any]) -> None:
    """Replace session parsing and the meal-service upload call."""

    async def fake_post_session(*_args: Any, **_kwargs: Any) -> tuple[
        dict[str, Any], dict[str, Any]
    ]:
        upload = UploadFile(
            filename="weekly-menu.xlsx",
            file=BytesIO(b"original workbook"),
        )
        return UPLOAD_SESSION, {"file": upload}

    monkeypatch.setattr(uploader.ph, "post_session", fake_post_session)
    monkeypatch.setattr(
        uploader.meal_service_client,
        "upload_excel",
        AsyncMock(return_value={"data": response_data}),
    )


def test_analysis_failure_shows_upload_success_without_analysis(
    monkeypatch: Any,
) -> None:
    """An archived workbook should not be shown as a failed upload."""
    _mock_upload_submission(
        monkeypatch,
        {
            "status": "uploaded",
            "upload_status": "completed",
            "analysis_status": "failed",
            "file_name": "weekly-menu.xlsx",
        },
    )

    with TestClient(main.app) as client:
        response = client.post("/uploader/excel")

    assert response.status_code == 200
    assert "파일 업로드가 완료되었습니다" in response.text
    assert "업로드 완료" in response.text
    assert "업로드한 메뉴 분석 결과" not in response.text
    assert "분석한 식단" not in response.text


def test_analysis_success_keeps_the_analysis_result_view(
    monkeypatch: Any,
) -> None:
    """A successful analysis still renders the menu summary and items."""
    _mock_upload_submission(
        monkeypatch,
        {
            "status": "completed",
            "upload_status": "completed",
            "analysis_status": "completed",
            "file_name": "weekly-menu.xlsx",
            "period": {"start_date": "2026-09-14", "end_date": "2026-09-18"},
            "uploaded_at": "2026-09-14T08:00:00+09:00",
            "file_size": 1200,
            "sha256": "0123456789abcdef",
            "summary": {"parsed": 1, "reflected": 1, "restaurants": 1},
            "items": [
                {
                    "date": "2026-09-14",
                    "restaurant": "TIP 가가식당",
                    "meal_type_label": "중식",
                    "menu": ["테스트 메뉴"],
                }
            ],
        },
    )

    with TestClient(main.app) as client:
        response = client.post("/uploader/excel")

    assert response.status_code == 200
    assert "업로드한 메뉴 분석 결과" in response.text
    assert "테스트 메뉴" in response.text
    assert "파일 업로드가 완료되었습니다" not in response.text

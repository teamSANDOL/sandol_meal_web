"""Meal workbook upload workflow for the web application."""

from typing import Any

from fastapi import Request, Response
from starlette.datastructures import UploadFile

from app.config import Config
from app.services import page_helpers as ph
from app.services.meal_client import MealServiceError, meal_service_client


def render_upload_page(
    request: Request,
    session: ph.SessionData,
    *,
    error_message: str | None = None,
    upload_result: dict[str, Any] | None = None,
    status_code: int = Config.HttpStatus.OK,
) -> Response:
    """Render the workbook form and optional analysis result."""
    return ph.render(
        request,
        session,
        "uploader/excel.html",
        status_code=status_code,
        error_message=error_message,
        upload_result=upload_result,
        max_upload_size_mb=Config.MEAL_UPLOAD_MAX_BYTES / (1024 * 1024),
    )


async def submit_upload(
    request: Request,
    session: ph.SessionData,
    upload: UploadFile | None,
) -> Response:
    """Validate and forward a workbook, then render its result."""
    if upload is None:
        return render_upload_page(
            request,
            session,
            error_message="업로드할 Excel 파일을 선택해주세요.",
            status_code=Config.HttpStatus.BAD_REQUEST,
        )

    file_name = upload.filename or ""
    safe_file_name = file_name.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    if not safe_file_name.lower().endswith(".xlsx"):
        await upload.close()
        return render_upload_page(
            request,
            session,
            error_message=".xlsx 파일만 업로드할 수 있습니다.",
            status_code=Config.HttpStatus.BAD_REQUEST,
        )

    try:
        content = await upload.read(Config.MEAL_UPLOAD_MAX_BYTES + 1)
        if len(content) > Config.MEAL_UPLOAD_MAX_BYTES:
            max_upload_size_mb = max(1, int(Config.MEAL_UPLOAD_MAX_BYTES / (1024 * 1024)))
            return render_upload_page(
                request,
                session,
                error_message=f"파일은 {max_upload_size_mb}MB 이하여야 합니다.",
                status_code=413,
            )
        result = await meal_service_client.upload_excel(
            user_id=session["user_id"], file_name=file_name, content=content
        )
    except MealServiceError as exc:
        return render_upload_page(
            request, session, error_message=exc.message, status_code=exc.status_code
        )
    finally:
        await upload.close()

    return render_upload_page(request, session, upload_result=ph.response_data(result))

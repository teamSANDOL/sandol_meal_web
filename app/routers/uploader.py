"""Role-protected menu workbook upload pages."""

from typing import Any

from fastapi import APIRouter, Request, Response
from starlette.datastructures import UploadFile

from app.config import Config
from app.services import page_helpers as ph
from app.services.meal_client import MealServiceError, meal_service_client

router = APIRouter()


def _upload_page(
    request: Request,
    session: ph.SessionData,
    *,
    error_message: str | None = None,
    upload_result: dict[str, Any] | None = None,
    status_code: int = Config.HttpStatus.OK,
) -> Response:
    """Render the upload form or the analysis result in the shared design."""
    return ph.render(
        request,
        session,
        "uploader/excel.html",
        status_code=status_code,
        error_message=error_message,
        upload_result=upload_result,
        max_upload_size_mb=Config.MEAL_UPLOAD_MAX_BYTES / (1024 * 1024),
    )


@router.get("/uploader/excel", name="meal_excel_upload_page")
async def meal_excel_upload_page(request: Request) -> Response:
    """Show the upload form to meal uploaders and administrators."""
    session = ph.page_session(request, meal_uploader=True)
    return _upload_page(request, session)


@router.post("/uploader/excel", name="meal_excel_upload_submit")
async def meal_excel_upload_submit(request: Request) -> Response:
    """Validate CSRF and role, then display the meal-service analysis."""
    session, form_data = await ph.post_session(request, meal_uploader=True)
    upload = form_data.get("file")
    if not isinstance(upload, UploadFile):
        return _upload_page(
            request,
            session,
            error_message="업로드할 Excel 파일을 선택해주세요.",
            status_code=Config.HttpStatus.BAD_REQUEST,
        )

    file_name = upload.filename or ""
    safe_file_name = file_name.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    if not safe_file_name.lower().endswith(".xlsx"):
        await upload.close()
        return _upload_page(
            request,
            session,
            error_message=".xlsx 파일만 업로드할 수 있습니다.",
            status_code=Config.HttpStatus.BAD_REQUEST,
        )

    try:
        content = await upload.read(Config.MEAL_UPLOAD_MAX_BYTES + 1)
        if len(content) > Config.MEAL_UPLOAD_MAX_BYTES:
            max_upload_size_mb = max(
                1, int(Config.MEAL_UPLOAD_MAX_BYTES / (1024 * 1024))
            )
            return _upload_page(
                request,
                session,
                error_message=f"파일은 {max_upload_size_mb}MB 이하여야 합니다.",
                status_code=413,
            )
        result = await meal_service_client.upload_excel(
            user_id=session["user_id"],
            file_name=file_name,
            content=content,
        )
    except MealServiceError as exc:
        return _upload_page(
            request,
            session,
            error_message=exc.message,
            status_code=exc.status_code,
        )
    finally:
        await upload.close()

    analysis = ph.response_data(result)
    return _upload_page(request, session, upload_result=analysis)

"""Role-protected menu workbook upload pages."""

from fastapi import APIRouter, Request, Response
from starlette.datastructures import UploadFile

from app.services import page_helpers as ph
from app.services.meal_client import meal_service_client  # noqa: F401
from app.services.uploader_service import render_upload_page, submit_upload

router = APIRouter()


@router.get("/uploader/excel", name="meal_excel_upload_page")
async def meal_excel_upload_page(request: Request) -> Response:
    """Show the upload form to meal uploaders and administrators."""
    session = ph.page_session(request, meal_uploader=True)
    return render_upload_page(request, session)


@router.post("/uploader/excel", name="meal_excel_upload_submit")
async def meal_excel_upload_submit(request: Request) -> Response:
    """Validate CSRF and role, then display the meal-service analysis."""
    session, form_data = await ph.post_session(request, meal_uploader=True)
    upload = form_data.get("file")
    return await submit_upload(
        request,
        session,
        upload if isinstance(upload, UploadFile) else None,
    )

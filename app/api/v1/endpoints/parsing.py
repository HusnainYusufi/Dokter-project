from fastapi import APIRouter, File, UploadFile, HTTPException
from app.schemas.parsing import ErrorDetail, ParsingResponse
from app.deps import LlamaParseServiceDep
from app.core.exceptions import ParsingError

router = APIRouter()


@router.post(
    "/upload",
    response_model=ParsingResponse,
    summary="Parse a PDF to Markdown",
    response_description="Per-page Markdown content extracted from the PDF.",
    responses={
        400: {
            "description": "Invalid file type — only PDF files are accepted.",
            "model": ErrorDetail,
        },
        500: {
            "description": "LlamaParse API error or internal server error.",
            "model": ErrorDetail,
        },
    },
)
async def parse_pdf(
    service: LlamaParseServiceDep,
    file: UploadFile = File(...),
) -> ParsingResponse:
    """
    Upload a PDF file and parse it to markdown using LlamaParse.

    Args:
        service: The LlamaParse service injected dependency.
        file: The uploaded PDF file.

    Returns:
        ParsingResponse: The parsed markdown content.

    Raises:
        HTTPException: If the file type is invalid or parsing fails.
    """
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Only PDF files are accepted.",
        )

    try:
        content = await file.read()
        pages = await service.parse_pdf(content, file.filename)
        return ParsingResponse(filename=file.filename, pages=pages)
    except Exception as e:
        # Catch-all for any unhandled exceptions in the service layer
        # though service layer should ideally raise specific exceptions
        if isinstance(e, ParsingError):
            raise e
        raise HTTPException(status_code=500, detail=str(e))

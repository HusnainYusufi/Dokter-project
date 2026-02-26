from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    """Standard error response body."""

    detail: str = Field(
        description="Human-readable error description.",
        examples=["Invalid file type. Only PDF files are accepted."],
    )


class ParsingResponse(BaseModel):
    """Response model for PDF parsing results."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "filename": "referral_letter.pdf",
                "pages": [
                    "## Patient Details\n\nName: John Doe\nDOB: 01/01/1980",
                    "## Clinical Summary\n\nDiagnosis: Nasopharyngeal carcinoma",
                ],
            }
        }
    )

    filename: str = Field(description="Original filename of the uploaded PDF.")
    pages: list[str] = Field(
        description=(
            "List of Markdown strings, one entry per parsed page. "
            "Failed pages contain an error message prefixed with '[Failed to parse page N]'."
        )
    )

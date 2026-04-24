from fastapi import HTTPException, status


class ProcessingError(HTTPException):
    """Base exception for extraction pipeline failures."""

    def __init__(self, detail: str, status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR) -> None:
        super().__init__(status_code=status_code, detail=detail)


class ExtractionError(ProcessingError):
    """Raised when Llama Cloud extraction fails."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail=f"Llama Cloud extraction error: {detail}")


class OpenAIExtractionError(ProcessingError):
    """Raised when OpenAI parsing or summarization fails."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail=f"OpenAI extraction error: {detail}")


class GeminiExtractionError(ProcessingError):
    """Raised when Gemini parsing or summarization fails."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail=f"Gemini extraction error: {detail}")


class StorageError(ProcessingError):
    """Raised when encrypted artifact storage fails."""


class ExportError(ProcessingError):
    """Raised when the Word-compatible export cannot be generated."""


class JobNotFoundError(ProcessingError):
    """Raised when a requested job id does not exist."""

    def __init__(self, job_id: str) -> None:
        super().__init__(
            detail=f"Extraction job '{job_id}' was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

from fastapi import HTTPException, status


class ParsingError(HTTPException):
    """Custom exception for parsing errors."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)


class LlamaParseError(ParsingError):
    """Exception raised when LlamaParse API fails."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail=f"LlamaParse API Error: {detail}")

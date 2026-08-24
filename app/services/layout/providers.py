"""Adapters for the document-structure services.

Each is deliberately thin. The value lives in base.py and render.py, which every
provider feeds; swapping Azure for Textract, or for something self-hosted where
no data may leave the building, is one class here.

Every adapter obeys one rule: analysis NEVER raises. Layout is an enrichment, so
a page still parses without it, and an outage in a structure service must not
fail a job that would otherwise have succeeded.
"""
from __future__ import annotations

import asyncio
import logging

from app.core.redaction import redact_secrets
from app.services.layout.base import LayoutField, LayoutTable, PageLayout

logger = logging.getLogger(__name__)


class AzureDocumentIntelligenceProvider:
    """Azure AI Document Intelligence, prebuilt-layout.

    Chosen as the default cloud option for its handwriting accuracy, which
    matters here: several decisive values in these files - a claimant's own
    account of her condition, a screening tool's date - are handwritten.
    """

    name = "azure"

    def __init__(self, endpoint: str, api_key: str) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._client = None

    def _ensure_client(self):  # noqa: ANN202 - vendor type, imported lazily
        if self._client is not None:
            return self._client
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential

        self._client = DocumentIntelligenceClient(
            endpoint=self._endpoint, credential=AzureKeyCredential(self._api_key)
        )
        return self._client

    async def analyze(self, page_number: int, image: bytes) -> PageLayout:
        try:
            return await asyncio.to_thread(self._analyze_sync, page_number, image)
        except Exception as exc:
            logger.warning(
                "Layout analysis failed for page %s: %s", page_number, redact_secrets(str(exc))
            )
            return PageLayout(page_number=page_number, analyzed=False)

    def _analyze_sync(self, page_number: int, image: bytes) -> PageLayout:
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

        poller = self._ensure_client().begin_analyze_document(
            "prebuilt-layout", AnalyzeDocumentRequest(bytes_source=image)
        )
        result = poller.result()
        return _from_azure(result, page_number)


class TextractProvider:
    """AWS Textract AnalyzeDocument with FORMS and TABLES."""

    name = "textract"

    def __init__(self, region: str) -> None:
        self._region = region
        self._client = None

    def _ensure_client(self):  # noqa: ANN202 - vendor type, imported lazily
        if self._client is None:
            import boto3

            self._client = boto3.client("textract", region_name=self._region)
        return self._client

    async def analyze(self, page_number: int, image: bytes) -> PageLayout:
        try:
            return await asyncio.to_thread(self._analyze_sync, page_number, image)
        except Exception as exc:
            logger.warning(
                "Layout analysis failed for page %s: %s", page_number, redact_secrets(str(exc))
            )
            return PageLayout(page_number=page_number, analyzed=False)

    def _analyze_sync(self, page_number: int, image: bytes) -> PageLayout:
        response = self._ensure_client().analyze_document(
            Document={"Bytes": image}, FeatureTypes=["TABLES", "FORMS"]
        )
        return from_textract(response, page_number)


# --------------------------------------------------------------------------
# Response mapping, kept out of the adapters so it is testable without a client.


def _from_azure(result: object, page_number: int) -> PageLayout:
    tables: list[LayoutTable] = []
    for table in getattr(result, "tables", None) or []:
        grid: dict[int, dict[int, str]] = {}
        headers: list[str] = []
        for cell in getattr(table, "cells", None) or []:
            row_index = int(getattr(cell, "row_index", 0) or 0)
            column_index = int(getattr(cell, "column_index", 0) or 0)
            content = str(getattr(cell, "content", "") or "")
            grid.setdefault(row_index, {})[column_index] = content
            if str(getattr(cell, "kind", "") or "") == "columnHeader" and row_index == 0:
                headers.append(content)
        rows = [
            [grid[r].get(c, "") for c in sorted(grid[r])] for r in sorted(grid)
        ]
        tables.append(LayoutTable(rows=rows, headers=headers, page_number=page_number))

    fields: list[LayoutField] = []
    for pair in getattr(result, "key_value_pairs", None) or []:
        key = getattr(pair, "key", None)
        value = getattr(pair, "value", None)
        label = str(getattr(key, "content", "") or "")
        text = str(getattr(value, "content", "") or "")
        if label and text:
            fields.append(LayoutField(label=label, value=text, page_number=page_number))

    return PageLayout(page_number=page_number, tables=tables, fields=fields)


def from_textract(response: dict, page_number: int) -> PageLayout:
    """Map a Textract AnalyzeDocument response.

    Textract returns a flat block list joined by relationship ids, so the blocks
    are indexed first and then walked.
    """
    blocks = {block["Id"]: block for block in response.get("Blocks", []) if "Id" in block}

    def text_of(block: dict) -> str:
        parts: list[str] = []
        for relationship in block.get("Relationships", []) or []:
            if relationship.get("Type") != "CHILD":
                continue
            for child_id in relationship.get("Ids", []):
                child = blocks.get(child_id, {})
                if child.get("BlockType") == "WORD":
                    parts.append(str(child.get("Text", "")))
                elif child.get("BlockType") == "SELECTION_ELEMENT":
                    if child.get("SelectionStatus") == "SELECTED":
                        parts.append("[X]")
        return " ".join(parts).strip()

    tables: list[LayoutTable] = []
    for block in blocks.values():
        if block.get("BlockType") != "TABLE":
            continue
        grid: dict[int, dict[int, str]] = {}
        headers: list[str] = []
        for relationship in block.get("Relationships", []) or []:
            if relationship.get("Type") != "CHILD":
                continue
            for cell_id in relationship.get("Ids", []):
                cell = blocks.get(cell_id, {})
                if cell.get("BlockType") != "CELL":
                    continue
                row = int(cell.get("RowIndex", 0))
                column = int(cell.get("ColumnIndex", 0))
                content = text_of(cell)
                grid.setdefault(row, {})[column] = content
                if "COLUMN_HEADER" in (cell.get("EntityTypes") or []):
                    headers.append(content)
        rows = [[grid[r].get(c, "") for c in sorted(grid[r])] for r in sorted(grid)]
        tables.append(LayoutTable(rows=rows, headers=headers, page_number=page_number))

    fields: list[LayoutField] = []
    for block in blocks.values():
        if block.get("BlockType") != "KEY_VALUE_SET":
            continue
        if "KEY" not in (block.get("EntityTypes") or []):
            continue
        label = text_of(block)
        value = ""
        for relationship in block.get("Relationships", []) or []:
            if relationship.get("Type") == "VALUE":
                for value_id in relationship.get("Ids", []):
                    value = text_of(blocks.get(value_id, {}))
        if label and value:
            fields.append(LayoutField(label=label, value=value, page_number=page_number))

    return PageLayout(page_number=page_number, tables=tables, fields=fields)

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

RUN_LOG_DIR = Path("app") / "run logs"
ALLOWED_STAGES = {"parse", "summarize"}

router = APIRouter()


def _safe_run_dir(run_id: str) -> Path:
    if not run_id.startswith("job_") or any(char in run_id for char in ("/", "\\", "..")):
        raise HTTPException(status_code=400, detail="Invalid run id.")
    run_dir = (RUN_LOG_DIR / run_id).resolve()
    root = RUN_LOG_DIR.resolve()
    if root not in run_dir.parents and run_dir != root:
        raise HTTPException(status_code=400, detail="Invalid run path.")
    if not run_dir.exists() or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="Run log not found.")
    return run_dir


def _file_meta(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "size_bytes": 0, "updated_at": None}
    stat = path.stat()
    return {"exists": True, "size_bytes": stat.st_size, "updated_at": stat.st_mtime}


@router.get("/runs", summary="List LLM run log folders")
def list_llm_runs() -> dict[str, Any]:
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    runs = []
    for run_dir in sorted(RUN_LOG_DIR.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if not run_dir.is_dir():
            continue
        runs.append(
            {
                "id": run_dir.name,
                "updated_at": run_dir.stat().st_mtime,
                "files": {
                    "parse": _file_meta(run_dir / "parse.jsonl"),
                    "summarize": _file_meta(run_dir / "summarize.jsonl"),
                },
            }
        )
    return {"runs": runs}


@router.get("/runs/{run_id}/{stage}", summary="Read an LLM run log file")
def read_llm_run_stage(
    run_id: str,
    stage: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    if stage not in ALLOWED_STAGES:
        raise HTTPException(status_code=400, detail="Invalid stage.")

    path = _safe_run_dir(run_id) / f"{stage}.jsonl"
    if not path.exists():
        return {"run_id": run_id, "stage": stage, "entries": [], "total": 0}

    entries: list[dict[str, Any]] = []
    total = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            if total >= offset and len(entries) < limit:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    entries.append({"raw": line.strip(), "parse_error": True})
            total += 1

    return {"run_id": run_id, "stage": stage, "entries": entries, "total": total}

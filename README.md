# Dokter — Medical Intelligence Extractive Service

A FastAPI backend and Next.js portal that ingests large, messy medical record bundles — hundreds of
scanned pages covering multiple patients and document types — and turns them into structured,
chronological, per-patient summaries with a Word-compatible export.

## What problem it solves

A medical-legal or insurance reviewer receives a single 400-page PDF. Inside it are discharge
summaries, imaging reports, physiotherapy notes and correspondence, for more than one patient,
scanned out of order, with no table of contents. Someone has to work out where each document starts
and ends, which patient it belongs to, put it in date order, and write a summary a clinician or
adjuster can act on. That is days of work per bundle.

Dokter automates the pipeline: boundary detection, patient attribution, page-level extraction,
chronological summarisation, and a drafted opinion — with cost tracking, provenance and consistency
checking layered on top.

## How it uses AI

This is the **most sophisticated AI architecture** in this set. It is a multi-provider, multi-stage
pipeline where each model is chosen for what it is actually good at.

### Stage 1 — Layout / OCR (pluggable, non-LLM)

`app/services/layout/providers.py` abstracts page geometry behind a common interface, selected by
`LAYOUT_PROVIDER`:

| Provider | Service |
| --- | --- |
| `textract` | AWS Textract (via boto3) |
| `azure` | Azure AI Document Intelligence |

Both normalise into a shared `PageLayout` in `base.py` / `render.py`, so a third provider is a thin
adapter rather than a rewrite.

### Stage 2 — Boundary & coverage detection (Llama Cloud)

Llama Cloud handles **whole-file** reasoning: where one document ends and the next begins, and which
pages cover which patient. This is a document-scale task, distinct from reading any single page,
which is why it uses a different model to the page parser.

### Stage 3 — Page parsing (OpenAI)

Per-page metadata and visible-text capture, feeding the grouping logic downstream.

### Stage 4 — Patient bundle summarisation (OpenAI)

Claimant header extraction, chronological narrative, and opinion output —
`extraction/summary.py`, `header.py`, `opinion.py`.

### Multi-provider LLM client

`app/services/extraction/llm.py` exposes two entry points, `gemini_json()` (via
`langchain-google-genai`) and `openai_json()` (via the OpenAI Responses API), both returning parsed
JSON. Both carry exponential-backoff retry, typed errors (`GeminiExtractionError`,
`OpenAIExtractionError`), and a `RunLogger` that writes every call to JSONL for replay and debugging.
Models referenced across the codebase span the GPT-4.1/4o/5 families and Gemini 1.5 through 3, so
model choice is configuration, not hardcoding.

### Rule Studio — prompts as database rows, not code

The most interesting design decision. Extraction behaviour is **not** hardcoded in prompts. A "golden
rules" prompt plus per-document-type rules live in the database (`rule_configs`, `document_types`,
`rule_config_rules`), and `services/rules/prompt_builder.py` assembles the actual prompt at run time
from that snapshot — including a catch-all type for unrecognised documents.

The consequence: **business rules change from the portal without a code release.** A reviewer who
decides radiology reports need a different treatment edits a rule, not a Python file. Rule configs
are snapshotted per job, so a job run last month can still be explained by the rules that were in
force then.

### Output verification (non-LLM, and the part that matters most)

`extraction/consistency.py` audits what the model produced, because a plausible-sounding medical
summary that is subtly wrong is worse than no summary:

- `find_contradictions()` — paragraphs asserting both a positive and a negative finding, with
  negation-stripping so "no evidence of fracture" isn't read as a fracture
- `find_absent_references()` — the summary cites a document that isn't in the bundle
- `find_duplicate_studies()` — the same study counted twice
- `find_temporal_outliers()` — dates that fall outside the plausible window for the review
- `find_unattributed_records()` — content with no traceable source document
- `reconcile()` — reconciles the assembled bundle against source records

`extraction/quality.py` scores extraction quality and detects broken pagination; `date_convention.py`
resolves DD/MM vs MM/DD ambiguity before dates are trusted. Tests cover provenance, reconciliation
and index rows.

### Cost tracking

`extraction/cost.py` and the pipeline accumulate input tokens, output tokens and USD **per stage**
per job, surfaced as a `CostSummary`. Necessary when a single bundle triggers hundreds of model
calls.

## Security engineering

Two things here are better than in most of this codebase family and worth calling out:

**Credential redaction (`app/core/redaction.py`).** When a provider rejects a key, its error message
contains the key. That message was landing in `job.error`, being written to the database and rendered
in the portal — leaking a live credential onto a screen where it outlived rotation. Every string that
comes from an exception or provider response now passes through a pattern-based redactor covering
OpenAI (`sk-`, `sk-proj-`, `sk-svcacct-`, `org-`) and Google (`AIza`) key shapes. Pattern-based
deliberately, so a provider added later is still caught.

**Encryption at rest (`app/services/encryption.py`).** Uploaded PDFs and generated `.doc` artifacts
are encrypted with Fernet before storage. `ARTIFACT_ENCRYPTION_KEY` must be set before changing
providers on an existing deployment or previously stored jobs become undecryptable.

## Architecture

```
Upload ─▶ Layout (Textract│Azure) ─▶ Boundary detection (Llama Cloud)
                                              │
                                              ▼
   Word export ◀── Consistency ◀── Summary (OpenAI) ◀── Page parse (OpenAI)
                    + Quality              ▲
                    + Cost          Rule Studio (DB-driven prompts)
```

**Storage:** MySQL for vault metadata, extraction jobs and artifact indexes; MinIO (S3-compatible)
for encrypted blobs, with a local fallback in `storage/`. A legacy importer migrates pre-existing
`storage/jobs` records into the database-backed vault on startup.

**Frontend:** Next.js portal (`frontend/`) with NextAuth login, a review dashboard, `react-pdf` /
`pdfjs-dist` for document viewing, `mammoth` for rendering the `.doc` output, and Framer Motion.

## Stack

FastAPI · SQLAlchemy + PyMySQL · MySQL · MinIO / boto3 · OpenAI · Google Gemini
(`langchain-google-genai`) · Llama Cloud · AWS Textract / Azure Document Intelligence ·
`cryptography` (Fernet) · pypdf + pypdfium2 + Pillow · Next.js + NextAuth · Docker Compose · ruff ·
pytest

## Required configuration

`OPENAI_API_KEY`, `LLAMA_CLOUD_API_KEY`, `GEMINI_API_KEY`, `ARTIFACT_ENCRYPTION_KEY`, plus credentials
for whichever `LAYOUT_PROVIDER` is selected.

> **Related:** `HusnainYusufi/Medical-PDF-Parsing-Summeriser` is a much smaller service covering only
> the LlamaParse ingestion step. This repository is the full product.

---

<!-- The original project README is preserved below. -->

# Medical Intelligence Extractive Service

A FastAPI backend for encrypted medical-document storage and extraction, patient/document boundary detection, OpenAI-assisted page parsing and patient bundle summarization, plus Word-compatible summary export.

## Features

- **Dynamic rule engine (Rule Studio)** storing the golden rules and per-document-type behavior in the database, so business rules change from the portal without a code release.
- **Hybrid AI pipeline** with Llama Cloud for whole-file boundary detection and patient coverage classification.
- **OpenAI page parsing** for page-local metadata and visible-text capture used by downstream grouping.
- **OpenAI patient bundle summaries** for claimant header extraction, chronological summary, and opinion output.
- **Encrypted artifact storage** for uploaded PDFs and generated `.doc` outputs.
- **Encrypted vault storage** for PDFs, audio, DOCX, and images using MySQL metadata plus S3-compatible object storage.
- **Next.js portal** for login, dashboard review, and download actions.

## Local stack

- **MySQL** stores vault metadata, extraction jobs, and artifact indexes.
- **MinIO** acts as the local S3-compatible bucket for encrypted blobs.
- **Legacy import** migrates existing `storage/jobs` records into the new database-backed vault on startup.

## Setup

1.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

2.  **Environment Variables**
    Copy `.env.example` to `.env` and set both your `LLAMA_CLOUD_API_KEY` and `OPENAI_API_KEY`.
    ```bash
    cp .env.example .env
    ```
    Recommended:
    - Set `ARTIFACT_ENCRYPTION_KEY` before changing providers in an existing deployment, so stored jobs remain decryptable.
    - Keep `LLAMA_CLOUD_API_KEY` configured because boundary detection and patient-window classification still depend on Llama.

3.  **Run the Backend API**
    ```bash
    uvicorn app.main:app --reload
    ```

    Or boot the full local stack:
    ```bash
    docker compose up --build
    ```

4.  **Run the Frontend Portal**
    ```bash
    cd frontend
    npm install
    npm run dev
    ```

5.  **Run the Tests**
    ```bash
    pip install -r requirements-dev.txt
    pytest
    ```
    The suite runs against SQLite and the local object-store fallback, so it needs
    no MySQL, MinIO, or AI provider keys.

## Usage

-   **API Documentation**: `http://localhost:8000/docs`
-   **Frontend Portal**: `http://localhost:3000`

## Rule Studio

Business rules live in the database, not in code. The **Rule Studio** section of
the portal manages named *configurations*, each holding a global golden rule
prompt, an opinion template, and per-document-type rules that say how the AI
recognizes a document and what to do with it (extract, take the whole data, or
skip). Pick a configuration when starting an extraction; the job records the
configuration and version it ran with, and past results are never rewritten by a
later edit.

The behavior the pipeline had before the rule engine ships as an editable
**Default (Golden Rules)** configuration, seeded once on first start.

See [`@docs/rule_studio.md`](@docs/rule_studio.md) for the full guide.

## API authentication

`API_AUTH_TOKEN` is unset by default and the API accepts any caller that can
reach it. Setting it requires `Authorization: Bearer <token>` on every `/api/v1`
request. Note that the portal calls the API from the browser, so the matching
`NEXT_PUBLIC_API_AUTH_TOKEN` is compiled into the client bundle and is readable
by anyone who loads the portal: treat the token as a guard against
unauthenticated scanning and stray direct calls, not as a substitute for keeping
the API off the public network.

## Project Structure

```plaintext
/
├── app/
│   ├── api/
│   ├── core/
│   ├── db/
│   ├── schemas/
│   ├── services/
│   │   ├── extraction/   # page parsing, grouping, summary, opinion
│   │   └── rules/        # rule configurations and prompt assembly
│   └── main.py
├── frontend/
├── tests/
├── @docs/
├── .env.example
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

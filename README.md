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

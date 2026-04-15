# Medical Intelligence Extractive Service

A FastAPI backend for encrypted medical-document extraction, patient/document boundary detection, OpenAI-assisted page parsing and patient bundle summarization, plus Word-compatible summary export.

## Features

- **Hybrid AI pipeline** with Llama Cloud for whole-file boundary detection and patient coverage classification.
- **OpenAI page parsing** for page-local metadata and visible-text capture used by downstream grouping.
- **OpenAI patient bundle summaries** for claimant header extraction, chronological summary, and opinion output.
- **Encrypted artifact storage** for uploaded PDFs and generated `.doc` outputs.
- **Next.js portal** for login, dashboard review, and download actions.

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

4.  **Run the Frontend Portal**
    ```bash
    cd frontend
    npm install
    npm run dev
    ```

## Usage

-   **API Documentation**: `http://localhost:8000/docs`
-   **Frontend Portal**: `http://localhost:3000`

## Project Structure

```plaintext
/
├── app/
│   ├── api/
│   ├── core/
│   ├── schemas/
│   ├── services/
│   └── main.py
├── frontend/
├── .env.example
├── requirements.txt
└── README.md
```

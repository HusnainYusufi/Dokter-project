# Medical PDF Parsing Microservice

A high-performance FastAPI microservice for parsing medical PDFs using LlamaParse.

## Features

- **FastAPI**: High-performance async API.
- **LlamaParse**: Advanced PDF parsing for medical documents.
- **Modular Architecture**: Domain-driven design with strict separation of concerns.
- **Strict Linting**: Enforced via `ruff`.

## Setup

1.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

2.  **Environment Variables**
    Copy `.env.example` to `.env` and set your `LLAMA_CLOUD_API_KEY`.
    ```bash
    cp .env.example .env
    ```

3.  **Run the Application**
    ```bash
    uvicorn app.main:app --reload
    ```

## Usage

-   **API Documentation**: `http://localhost:8000/docs`
-   **Frontend Upload UI**: `http://localhost:8000/`

## Project Structure

```plaintext
/
├── app/
│   ├── api/
│   ├── core/
│   ├── schemas/
│   ├── services/
│   ├── static/
│   └── main.py
├── .env.example
├── requirements.txt
├── ruff.toml
└── README.md
```

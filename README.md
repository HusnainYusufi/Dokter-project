# Medical Intelligence Extractive Service

A FastAPI backend for encrypted medical-document extraction, patient/document boundary detection, page-wise review, and Word-compatible summary export.

## Features

- **Page-wise extraction** via Llama Cloud extraction jobs.
- **Boundary detection** for mixed-patient and multi-document files.
- **Extractive summaries** aligned to medico-legal workflow constraints.
- **Encrypted artifact storage** for uploaded PDFs and generated `.doc` outputs.
- **Next.js portal** for login, dashboard review, and download actions.

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

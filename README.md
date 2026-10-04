# DocuPay — AI-Powered Intelligent Document Processing

DocuPay is a multi-tenant SaaS template for automating document ingestion and extraction (invoices, contracts, resumes, etc.) with AI, human-in-the-loop review, and webhook delivery.

## What it does
- Upload PDFs/images to a secure tenant-scoped backend.
- OCR scanned docs, classify type (invoice/contract/resume), and extract structured fields via LLM prompts.
- Confidence scoring to auto-approve high-confidence docs; route low-confidence items to a human review queue.
- Review UI to approve/reject flagged fields.
- Export via JSON/CSV (planned) and webhooks with delivery logs.
- Org-level analytics (planned): docs processed per day, % requiring review, webhook success.

## Tech stack
- Backend: Python 3.11, Django 5, Django REST Framework, SimpleJWT, Celery + Redis, PostgreSQL, django-storages + boto3 (S3), pytesseract, OpenAI API.
- Frontend: Next.js 14 (App Router) with TypeScript, React Query, Tailwind CSS.
- Infra: Docker + docker-compose (backend, frontend, db, redis, celery), .env configuration.
- CI: GitHub Actions (backend tests, frontend lint/build) scaffold.

## Extraction pipeline (v2)
Code: `backend/apps/processing/`.

1. **Ingest** (`ingest.py`): reads the file through the storage API (works on local disk and S3). Digital PDFs use the embedded text layer (free). Scanned PDFs and images become resized PNGs for a vision model. Page and size limits apply.
2. **Extract** (`providers/`): one interface, three backends, selected with `EXTRACTION_PROVIDER`:
   - `anthropic`: Claude with structured JSON output. Fast tier `claude-haiku-4-5`, strong tier `claude-opus-5-5`.
   - `openai`: Chat Completions with a strict JSON Schema.
   - `heuristic`: offline regex (and Tesseract for images). No key and no cost, but low accuracy. Use it for development only.
3. **Validate** (`validation.py`): confidence comes from checks that can be proved. The checks cover required fields, subtotal + tax = total, line items adding up, quantity x price, date order, currency and duplicate invoice numbers. A document auto-approves only if no critical check fails, the amounts are proved, and the score reaches the organization's threshold.
4. **Escalate** (`pipeline.py`): if the fast result cannot auto-approve, the strong model runs once. If it still fails, the document goes to human review with the failed checks.
5. **Task** (`tasks.py`): retries transient provider errors with backoff, sets `FAILED` with a message on permanent errors, is idempotent, and runs on its own `extraction` queue. The model, tokens and estimated cost of each attempt are saved in `Document.processing_meta`.

## Frontend
`frontend/` is a Next.js 16 / React 19 / Tailwind 4 app exported as static files (`npm run build` writes `out/`), so it can be served from a CDN or S3 with no Node server.
- **Overview:** real numbers from `/api/documents/stats/`: the share handled without a person, invoices waiting for you, approved value and processing cost.
- **Review:** the document page next to the fields, the reasons it needs you in plain words, inline corrections, and keyboard shortcuts (A approve, R reject, J/K next/previous).
- **Documents:** upload by drag and drop, live progress, filters and search, retry failed documents.
- **Settings:** an amount limit and the "review new vendors" rule.

Set `NEXT_PUBLIC_API_BASE` to the API URL at build time. Users in several companies pick one in the sidebar (sent as `X-Organization-ID`).

## Benchmark
`backend/benchmark/` holds an offline benchmark (40 labeled invoices, 373 injected extraction errors) and the evaluator's report (`REPORT.md`). Run `backend/.venv/bin/python backend/benchmark/run.py`; add `--provider anthropic` with an API key to measure real model accuracy.

## Tests
```
cd backend
pip install -r requirements-dev.txt
pytest
```
Tests need no Postgres, Redis, S3 or API key.

## How to run (dev)
1) Copy env: `cp .env.example .env` and adjust secrets (DB, Redis, LLM provider, AWS if using S3).
2) Start services: `docker compose up --build`.
   - Backend: http://localhost:8000
   - Frontend: http://localhost:3000
3) Create admin user: `docker compose exec backend python manage.py createsuperuser`.
4) Log in at the frontend and upload a file. It is processed by the provider set in `EXTRACTION_PROVIDER`.

## Roadmap / TODO
- Webhooks: CRUD UI + delivery task with retries and logs; export CSV/JSON endpoints.
- Analytics: docs per org/day, review rate, webhook success.
- Org selection (if multi-org user), improved permissions.
- Testing: frontend e2e/unit; remove `|| true` from the frontend CI job.

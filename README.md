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

## Current state (MVP scaffold)
- Auth: JWT obtain/refresh; `/api/users/me/` endpoint; frontend auth gate + token-aware navbar with logout.
- Orgs: Org model/membership; queries scoped to user’s org membership.
- Documents: Models + API CRUD; upload endpoint `/api/documents/upload/` (multipart) sets status `queued`.
- Processing: Celery task stubs (OCR/classify/extract/confidence) ready to wire; not executing real pipeline yet.
- Review: ReviewTask model + API; approve/reject actions update task/document status; frontend review list/detail hooked to API.
- Webhooks: Models + API stubs; UI list with mock data; delivery task stub.
- Frontend: Dashboard, documents list, upload form (calls API), review queue/detail, webhooks settings; protected by AuthGate; Axios client attaches Bearer tokens; 401 interceptor clears tokens and redirects to login.
- Docker: Backend installs tesseract; compose starts backend, celery, redis, postgres, frontend.

## How to run (dev)
1) Copy env: `cp .env.example .env` and adjust secrets (DB, Redis, OpenAI, AWS if using S3).
2) Start services: `docker compose up --build`.
   - Backend: http://localhost:8000
   - Frontend: http://localhost:3000
3) Create admin user: `docker compose exec backend python manage.py createsuperuser` (or use precreated admin/admin123 from setup).
4) Log in at the frontend; upload a file; it will appear as `queued` (pipeline still stubbed).

## Roadmap / TODO
- Implement processing pipeline: pytesseract OCR + OpenAI classification/extraction + confidence scoring; update Document/ExtractedData/ReviewTask; add prompt templates per doc type.
- Webhooks: CRUD UI + delivery task with retries and logs; export CSV/JSON endpoints.
- Analytics: docs per org/day, review rate, webhook success.
- Org selection (if multi-org user), improved permissions.
- Testing: DRF API tests, frontend e2e/unit; harden CI (remove `|| true`).

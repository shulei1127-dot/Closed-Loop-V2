# Closed Loop V2 — Project Context

## What is this project

长亭科技售后闭环自动化平台（Closed Loop V2）。核心流水线：钉钉文档采集 → 字段识别归一化 → 任务规划 → 自动执行 → 审计轨迹。

三大业务模块：`visit`（交付转售后回访）、`inspection`（巡检工单）、`proactive`（超半年主动回访）。

## Tech stack

Python 3.11+ / FastAPI / SQLAlchemy 2.x / psycopg3 / PostgreSQL 16 / Alembic / APScheduler / httpx / Playwright / pydantic-settings / Jinja2 / pytest

## Key files

- `apps/api/main.py` — FastAPI app + lifespan
- `core/config.py` — Settings (70+ env vars)
- `core/db.py` — DB engine/session
- `core/runtime_state.py` — Thread-safe lock registry
- `services/sync_service.py` — Sync pipeline orchestrator
- `services/task_execution_service.py` — Task execution orchestrator
- `services/task_dispatcher.py` — Async worker pool
- `services/module_registry.py` — Module definitions + registry
- `scheduler/jobs.py` — APScheduler cron jobs

## Architecture

Pipeline: Collectors → Recognizers → Planners → Executors

Each module registers in COLLECTOR_REGISTRY, RECOGNIZER_REGISTRY, PLANNER_REGISTRY, EXECUTOR_REGISTRY.

DB tables: module_configs → source_snapshots → normalized_records → task_plans → task_runs (+ task_batches/task_batch_jobs)

## Dev commands

- Start: `uvicorn apps.api.main:app --reload`
- Migrate: `alembic upgrade head`
- Test: `pytest tests/ -v` (needs Docker PostgreSQL or TEST_DATABASE_URL)
- Install: `pip install -e .[dev]`

## Conventions

- All config via `.env` + Settings class, no hardcoded values
- New tables require Alembic migration
- UUID PKs + TimestampMixin on all models
- Repo layer only for DB access, no raw SQL in services
- Collectors inherit ConfiguredCollectorBase
- Executors register by (module_code, task_type) in EXECUTOR_REGISTRY
- Real execution gated by ENABLE_REAL_EXECUTION + module flags
- API responses use Pydantic schemas, not raw ORM objects
- Ops API uses in-memory read cache
- Console: Jinja2 templates, vanilla JS, no build tools
- Chinese holiday-aware scheduler (chinesecalendar)
- PTS API rate limit: 5 req/s, dispatcher single worker + 3s delay

## Adding a new module

Follow the pipeline pattern: model → repo → schema → collector → recognizer → planner → executor → registry → scheduler job → API router → console page → tests

## Key API patterns

- Sync: `POST /api/sync/run` (module_code body)
- Task execute: `POST /api/tasks/{id}/execute` or `enqueue-execute`
- Batch: `POST /api/tasks/batch/execute-pending`
- Ops overview: `GET /api/ops/overview` (cached)
- PTS session: `POST /api/ops/pts-session` (api_token or cookie)

## Full skill reference

Detailed project knowledge is in `.claude/skills/closed-loop-v2.md` — invoke for comprehensive architecture, operation patterns, and development guides.
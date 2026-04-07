from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from apps.api.router import api_router
from apps.web.router import router as web_router
from core.config import get_settings
from core.exceptions import EnvironmentDependencyError
from core.logging import configure_logging
from core.scheduler import build_scheduler
from scheduler.jobs import register_jobs
from services.task_dispatcher import get_task_dispatcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    scheduler = build_scheduler()
    job_ids = register_jobs(scheduler)
    dispatcher = get_task_dispatcher()
    await dispatcher.start()
    if settings.scheduler_enabled:
        scheduler.start()
    app.state.scheduler = scheduler
    app.state.scheduler_job_ids = job_ids
    app.state.task_dispatcher = dispatcher
    try:
        yield
    finally:
        if settings.scheduler_enabled:
            scheduler.shutdown(wait=False)
        await dispatcher.stop()


settings = get_settings()
app = FastAPI(title="closed_loop_v2", lifespan=lifespan, debug=settings.app_debug)
app.include_router(api_router)
app.include_router(web_router)
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parents[2] / "static")), name="static")


@app.middleware("http")
async def disable_cache_for_console_and_api(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/console") or path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.exception_handler(EnvironmentDependencyError)
async def environment_dependency_error_handler(
    request: Request,
    exc: EnvironmentDependencyError,
):
    payload = {
        "ok": False,
        "error_type": exc.error_type,
        "message": exc.public_message,
        "hint": exc.hint,
        "details": exc.details,
    }
    if request.url.path.startswith("/api"):
        return JSONResponse(status_code=exc.status_code, content=payload)

    html = f"""
    <html lang="zh-CN">
      <head>
        <meta charset="utf-8" />
        <title>环境依赖未就绪</title>
        <style>
          body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 40px; color: #1f2937; }}
          .card {{ max-width: 760px; padding: 24px 28px; border: 1px solid #e5e7eb; border-radius: 16px; background: #fffdf8; }}
          h1 {{ margin: 0 0 12px; font-size: 28px; }}
          p {{ line-height: 1.6; }}
          code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 6px; }}
          .muted {{ color: #6b7280; }}
        </style>
      </head>
      <body>
        <div class="card">
          <h1>环境依赖未就绪</h1>
          <p>{exc.public_message}</p>
          <p><strong>提示：</strong>{exc.hint or '请检查环境配置。'}</p>
          <p class="muted">错误类型：<code>{exc.error_type}</code></p>
        </div>
      </body>
    </html>
    """
    return HTMLResponse(status_code=exc.status_code, content=html)

from fastapi import APIRouter

from apps.api.routers import health, modules, ops, records, snapshots, sync, task_runs, tasks


api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(sync.router, prefix="/api", tags=["sync"])
api_router.include_router(snapshots.router, prefix="/api", tags=["snapshots"])
api_router.include_router(records.router, prefix="/api", tags=["records"])
api_router.include_router(tasks.router, prefix="/api", tags=["tasks"])
api_router.include_router(task_runs.router, prefix="/api", tags=["task-runs"])
api_router.include_router(modules.router, prefix="/api", tags=["modules"])
api_router.include_router(ops.router, prefix="/api", tags=["ops"])

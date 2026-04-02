# Closed-Loop-V2

第一阶段代码骨架，覆盖以下链路：

- mock 采集
- 字段识别
- 标准化入库
- 任务规划
- 结果查询 API

## 技术栈

- FastAPI
- PostgreSQL
- SQLAlchemy 2.x
- Alembic
- APScheduler
- Pydantic
- Playwright fallback stub

## 目录结构

```text
closed_loop_v2/
├── apps/api
├── core
├── migrations
├── models
├── repositories
├── scheduler
├── schemas
├── services
└── tests
```

## 快速启动

1. 创建数据库 `closed_loop_v2`
2. 复制环境变量：`cp .env.example .env`
3. 创建虚拟环境：`python3 -m venv .venv`
4. 安装依赖：`.venv/bin/pip install -e .[dev]`
5. 执行迁移：`.venv/bin/alembic upgrade head`
6. 启动服务：`.venv/bin/uvicorn apps.api.main:app --reload`

## 核心接口

- `GET /healthz`
- `POST /api/sync/run`
- `GET /api/snapshots`
- `GET /api/records`
- `GET /api/tasks`
- `GET /api/modules/summary`

## 第一阶段说明

- 只使用 mock collectors
- Playwright 仅保留 fallback 接口和 stub
- executors 仅保留抽象定义，不做真实闭环执行

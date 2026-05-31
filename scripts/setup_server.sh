#!/usr/bin/env bash
# ============================================================================
# closed_loop_v2 — 服务器部署前置脚本
# 用法: bash scripts/setup_server.sh
# 说明: 在目标服务器上运行，检查环境 + 创建数据库 + 安装依赖 + 迁移
#       运行前请先把项目文件夹拷到服务器
# ============================================================================

set -euo pipefail

APP_PORT=8200
APP_DB_NAME="closed_loop_v2"
APP_DB_USER="closed_loop"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PASS=0
FAIL=0

green() { printf "\033[32m[PASS]\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
red()   { printf "\033[31m[FAIL]\033[0m %s\n" "$1"; FAIL=$((FAIL+1)); }
info()  { printf "\033[36m[INFO]\033[0m %s\n" "$1"; }
step()  { printf "\n\033[1;35m===== %s =====\033[0m\n" "$1"; }

echo "============================================"
echo " closed_loop_v2 服务器部署前置"
echo "============================================"

# ===================== 阶段一：环境预检 =====================

step "1/4 环境预检"

# Python
PYTHON3=$(command -v python3 2>/dev/null || echo "")
if [[ -z "$PYTHON3" ]]; then
  red "未找到 python3，请先安装 Python >= 3.11"
else
  PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
  PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
  PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')
  if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 11 ]]; then
    red "Python ${PY_VERSION} < 3.11，项目要求 >= 3.11"
  else
    green "Python ${PY_VERSION}"
  fi
  if python3 -c "import venv" 2>/dev/null; then
    green "python3 -m venv 可用"
  else
    red "python3 -m venv 不可用，需安装 python3-venv"
  fi
fi

# PostgreSQL
PSQL=$(command -v psql 2>/dev/null || echo "")
if [[ -z "$PSQL" ]]; then
  red "未找到 psql，请先安装 PostgreSQL >= 13"
else
  green "PostgreSQL $(psql --version 2>/dev/null | awk '{print $3}')"
fi

# 端口
PORT_OK=true
if command -v ss &>/dev/null; then
  if ss -tlnp 2>/dev/null | grep -q ":${APP_PORT} "; then
    red "端口 ${APP_PORT} 已被占用"
    PORT_OK=false
  fi
elif command -v lsof &>/dev/null; then
  if lsof -i :${APP_PORT} &>/dev/null 2>&1; then
    red "端口 ${APP_PORT} 已被占用"
    PORT_OK=false
  fi
fi
if $PORT_OK; then
  green "端口 ${APP_PORT} 可用"
fi

if [[ $FAIL -gt 0 ]]; then
  echo ""
  red "环境预检未通过，请先修复上述问题后重新运行"
  exit 1
fi

green "环境预检通过"

# ===================== 阶段二：创建数据库 =====================

step "2/4 创建数据库"

# 检查数据库是否已存在
DB_EXISTS=$(sudo -u postgres psql -lqt 2>/dev/null | grep "^ ${APP_DB_NAME} " | wc -l || echo "0")
if [[ "$DB_EXISTS" -gt 0 ]]; then
  info "数据库 ${APP_DB_NAME} 已存在，跳过创建"
else
  info "创建数据库用户和数据库..."
  sudo -u postgres psql -c "CREATE USER ${APP_DB_USER} WITH PASSWORD '${APP_DB_USER}';" 2>/dev/null || true
  sudo -u postgres psql -c "CREATE DATABASE ${APP_DB_NAME} OWNER ${APP_DB_USER};"
  green "数据库 ${APP_DB_NAME} 创建完成"
fi

# 测试连接
if [[ -n "$PSQL" ]]; then
  if PGPASSWORD="${APP_DB_USER}" psql -h localhost -U "${APP_DB_USER}" -d "${APP_DB_NAME}" -c "SELECT 1" &>/dev/null; then
    green "数据库连接测试成功"
  else
    red "数据库连接失败，请检查用户权限"
    info "可手动执行: sudo -u postgres psql -c \"ALTER USER ${APP_DB_USER} WITH PASSWORD '${APP_DB_USER}';\""
    exit 1
  fi
fi

# ===================== 阶段三：安装 Python 依赖 =====================

step "3/4 安装 Python 依赖"

cd "$PROJECT_DIR"

if [[ ! -d ".venv" ]]; then
  info "创建虚拟环境..."
  python3 -m venv .venv
  green "虚拟环境创建完成"
else
  info "虚拟环境已存在，跳过创建"
fi

source .venv/bin/activate

info "安装项目依赖..."
pip install -e . 2>&1 | tail -5
green "Python 依赖安装完成"

# ===================== 阶段四：数据库迁移 + .env 配置 =====================

step "4/4 数据库迁移 & 配置"

# .env
if [[ ! -f ".env" ]]; then
  cp .env.example .env
  # 更新数据库连接字符串
  sed -i "s|postgresql+psycopg://postgres:postgres@localhost:5432/closed_loop_v2|postgresql+psycopg://${APP_DB_USER}:${APP_DB_USER}@localhost:5432/${APP_DB_NAME}|g" .env
  green ".env 已从 .env.example 复制，数据库连接已更新"
  info "请编辑 .env 配置 PTS_API_TOKEN 等业务参数"
else
  info ".env 已存在，跳过"
fi

# alembic.ini
if [[ -f "alembic.ini" ]]; then
  sed -i "s|postgresql+psycopg://postgres:postgres@localhost:5432/closed_loop_v2|postgresql+psycopg://${APP_DB_USER}:${APP_DB_USER}@localhost:5432/${APP_DB_NAME}|g" alembic.ini
fi

# 数据库迁移
info "执行数据库迁移..."
alembic upgrade head 2>&1 | tail -5
green "数据库迁移完成"

# ===================== 完成 =====================

echo ""
echo "============================================"
echo " 部署前置完成！"
echo "============================================"
echo ""
echo " 启动服务:"
echo "   cd ${PROJECT_DIR}"
echo "   source .venv/bin/activate"
echo "   uvicorn apps.api.main:app --host 0.0.0.0 --port ${APP_PORT}"
echo ""
echo " 后台常驻 (推荐):"
echo "   nohup .venv/bin/uvicorn apps.api.main:app --host 0.0.0.0 --port ${APP_PORT} > app.log 2>&1 &"
echo ""
echo " 访问地址: http://<服务器IP>:${APP_PORT}/console"
echo ""

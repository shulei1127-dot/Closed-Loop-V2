from collections.abc import Generator
import logging

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from core.config import get_settings
from core.exceptions import EnvironmentDependencyError
from models.base import Base


settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
logger = logging.getLogger(__name__)


def safe_database_url() -> str:
    try:
        return make_url(settings.database_url).render_as_string(hide_password=True)
    except Exception:
        return "<invalid database url>"


def probe_database_connection() -> tuple[bool, str | None]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True, None
    except SQLAlchemyError as exc:
        logger.exception("database connectivity check failed for %s", safe_database_url())
        return False, str(exc)


def ensure_database_ready() -> None:
    ok, error_message = probe_database_connection()
    if ok:
        return
    raise EnvironmentDependencyError(
        error_type="database_unavailable",
        public_message="数据库不可达",
        hint="请检查 DATABASE_URL、PostgreSQL 服务和数据库初始化状态。",
        details={
            "database_url": safe_database_url(),
            "reason": error_message,
        },
    )


def get_db() -> Generator[Session, None, None]:
    ensure_database_ready()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    Base.metadata.create_all(bind=engine)

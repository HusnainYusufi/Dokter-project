from __future__ import annotations

import socket
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import ArgumentError, OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.models import Base


def _host_resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
        return True
    except OSError:
        return False


def _coerce_mysql_to_pymysql_url(raw_url: str) -> str:
    s = raw_url.strip()
    lower = s.lower()
    for bad in ("mysql+mysqldb://", "mysql+mysqldb3://"):
        if lower.startswith(bad):
            return "mysql+pymysql://" + s.split("://", 1)[1]
    if lower.startswith("mysql://") and "mysql+mysql" not in lower.split("://", 1)[0]:
        return "mysql+pymysql://" + s.split("://", 1)[1]
    return s


def _normalize_database_url(raw_url: str) -> str:
    raw_url = _coerce_mysql_to_pymysql_url(raw_url)
    parsed = urlsplit(raw_url)
    hostname = parsed.hostname
    if not hostname or hostname != "mysql" or _host_resolves(hostname):
        return raw_url

    netloc = parsed.netloc.replace("@mysql:", "@127.0.0.1:").replace("@mysql/", "@127.0.0.1/")
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _build_engine(database_url: str):
    database_url = _normalize_database_url(database_url)
    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    try:
        return create_engine(
            database_url,
            future=True,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
    except ModuleNotFoundError:
        if not settings.ALLOW_LOCAL_FALLBACK:
            raise
        fallback_url = _fallback_sqlite_url()
        return create_engine(
            fallback_url,
            future=True,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False},
        )


def _parse_database_url(database_url: str) -> URL:
    normalized_url = _normalize_database_url(database_url)
    try:
        return make_url(normalized_url)
    except ArgumentError as exc:
        raise RuntimeError(
            "DATABASE_URL is invalid. Example: mysql+pymysql://user:password@host:3306/database"
        ) from exc


def _ensure_database_exists(database_url: str) -> None:
    url = _parse_database_url(database_url)
    if not url.drivername.startswith("mysql"):
        return

    database_name = url.database
    if not database_name:
        raise RuntimeError("DATABASE_URL must include a database name.")

    admin_url = url.set(database=None)
    admin_engine = create_engine(admin_url, future=True, pool_pre_ping=True)
    quoted_database_name = database_name.replace("`", "``")
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE DATABASE IF NOT EXISTS `{quoted_database_name}`"))
    finally:
        admin_engine.dispose()


def _fallback_sqlite_url() -> str:
    fallback_path = settings.LEGACY_JOB_STORAGE_DIR.parent / "dev-fallback.db"
    fallback_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{fallback_path.as_posix()}"


engine: Engine | None = None
SessionLocal = sessionmaker(autoflush=False, autocommit=False, expire_on_commit=False)


def _rebind_engine(database_url: str) -> None:
    global engine
    engine = _build_engine(database_url)
    SessionLocal.configure(bind=engine)


def _ensure_engine() -> Engine:
    global engine
    if engine is None:
        _rebind_engine(settings.DATABASE_URL)
    return engine


def init_database_schema() -> None:
    try:
        _ensure_database_exists(settings.DATABASE_URL)
        Base.metadata.create_all(bind=_ensure_engine())
    except RuntimeError:
        raise
    except OperationalError:
        if not settings.ALLOW_LOCAL_FALLBACK:
            raise
        fallback_url = _fallback_sqlite_url()
        _rebind_engine(fallback_url)
        Base.metadata.create_all(bind=engine)

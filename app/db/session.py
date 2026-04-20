from __future__ import annotations

import socket
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.models import Base


def _host_resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
        return True
    except OSError:
        return False


def _normalize_database_url(raw_url: str) -> str:
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
        fallback_url = _fallback_sqlite_url()
        return create_engine(
            fallback_url,
            future=True,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False},
        )


def _fallback_sqlite_url() -> str:
    fallback_path = settings.LEGACY_JOB_STORAGE_DIR.parent / "dev-fallback.db"
    fallback_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{fallback_path.as_posix()}"


engine = _build_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(autoflush=False, autocommit=False, expire_on_commit=False)
SessionLocal.configure(bind=engine)


def _rebind_engine(database_url: str) -> None:
    global engine
    engine = _build_engine(database_url)
    SessionLocal.configure(bind=engine)


def init_database_schema() -> None:
    try:
        Base.metadata.create_all(bind=engine)
    except OperationalError:
        fallback_url = _fallback_sqlite_url()
        _rebind_engine(fallback_url)
        Base.metadata.create_all(bind=engine)

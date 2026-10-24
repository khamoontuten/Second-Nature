"""Engine/session management and startup initialisation (migrations + seed data)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import MODEL_VERSION, __version__
from app.core.config import get_settings
from app.database.models import Base, ClimateProviderRecord, CoolingStrategyRecord, ModelVersion

log = logging.getLogger("second_nature.db")


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    if url.startswith("sqlite"):
        kw = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kw["poolclass"] = StaticPool
        else:
            Path(url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, **kw)

        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if not url.endswith("://") and ":memory:" not in url:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)


@lru_cache
def _factory():
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    s = _factory()()
    try:
        yield s
    finally:
        s.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    s = _factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def run_migrations() -> None:
    """Apply Alembic migrations (``alembic upgrade head``); fall back to create_all for in-memory SQLite."""
    url = get_settings().database_url
    if url in ("sqlite://", "sqlite:///:memory:"):
        Base.metadata.create_all(get_engine())
        return
    from alembic import command
    from alembic.config import Config

    ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


def seed() -> None:
    from app.climate.ingestion import PROVIDER_INFO
    from app.strategies.library import STRATEGIES

    with session_scope() as s:
        if not s.scalar(select(ModelVersion).where(ModelVersion.version == MODEL_VERSION)):
            s.add(ModelVersion(version=MODEL_VERSION, app_version=__version__, notes="registered at startup"))
        for pid, info in PROVIDER_INFO.items():
            if not s.get(ClimateProviderRecord, pid):
                s.add(ClimateProviderRecord(id=pid, label=info["label"], requires_credentials=info["credentials"],
                                            data_nature=info["nature"], url=info["url"]))
        for st in STRATEGIES:
            if not s.get(CoolingStrategyRecord, st["id"]):
                s.add(CoolingStrategyRecord(id=st["id"], key=st["key"], name=st["name"], meta=st))


def init_db() -> None:
    run_migrations()
    seed()

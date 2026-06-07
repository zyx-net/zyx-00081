from contextlib import contextmanager
from pathlib import Path
import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import DB_PATH as DEFAULT_DB_PATH

Base = declarative_base()

_engine_cache = {}
_session_cache = {}


def _get_db_path():
    env_path = os.environ.get("POWER_ANALYTICS_DB_PATH")
    return Path(env_path) if env_path else DEFAULT_DB_PATH


def _get_engine():
    db_path = _get_db_path()
    path_str = str(db_path)
    if path_str not in _engine_cache:
        _engine_cache[path_str] = create_engine(
            f"sqlite:///{db_path}",
            echo=False,
            future=True,
            connect_args={"check_same_thread": False},
        )
    return _engine_cache[path_str]


def _get_session_maker():
    engine = _get_engine()
    engine_id = id(engine)
    if engine_id not in _session_cache:
        _session_cache[engine_id] = sessionmaker(
            autocommit=False, autoflush=False, bind=engine, future=True
        )
    return _session_cache[engine_id]


@contextmanager
def get_db():
    db = _get_session_maker()()
    try:
        yield db
    finally:
        db.close()


def create_db_session():
    return _get_session_maker()()


def reset_engine_cache():
    _engine_cache.clear()
    _session_cache.clear()


def init_db(reset: bool = False):
    from . import models

    db_path = _get_db_path()

    if reset and db_path.exists():
        reset_engine_cache()
        db_path.unlink()

    engine = _get_engine()
    Base.metadata.create_all(bind=engine)

    with get_db() as db:
        models.init_anomaly_types(db)
        models.init_default_field_mappings(db)
        db.commit()

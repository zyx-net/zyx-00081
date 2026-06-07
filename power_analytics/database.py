from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import DB_PATH

Base = declarative_base()

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)


@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_db_session():
    return SessionLocal()


def init_db(reset: bool = False):
    from . import models

    if reset and DB_PATH.exists():
        DB_PATH.unlink()

    Base.metadata.create_all(bind=engine)

    with get_db() as db:
        models.init_anomaly_types(db)
        models.init_default_field_mappings(db)
        db.commit()

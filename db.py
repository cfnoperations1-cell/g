"""Shared SQLAlchemy engine/session used by both the CRM app and the scraper CLI.

Plain SQLAlchemy (not Flask-SQLAlchemy) so the scraper agent can write leads
without needing a Flask application context.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

import config

_connect_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(config.DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def init_db() -> None:
    import models  # noqa: F401  (registers models on Base before create_all)

    Base.metadata.create_all(bind=engine)

"""Shared SQLAlchemy engine/session used by both the CRM app and the scraper CLI.

Plain SQLAlchemy (not Flask-SQLAlchemy) so the scraper agent can write leads
without needing a Flask application context.
"""
import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker

import config

logger = logging.getLogger(__name__)

_connect_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(config.DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def _sql_literal(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def _add_missing_columns() -> None:
    """Bring an already-created SQLite database up to the current models.

    create_all() only creates tables that don't exist -- it never alters one
    that does. Anyone who ran the vendor scraper before the clinics agent
    existed already has a leads table without kind/clinic_type/..., and
    would hit "no such column" on the next run. There's no migration tool in
    this project, and the added columns are all nullable or carry a default,
    so a plain ADD COLUMN pass is enough.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all() will build it from scratch
            present = {col["name"] for col in inspector.get_columns(table.name)}

            for column in table.columns:
                if column.name in present:
                    continue
                default = getattr(column.default, "arg", None)
                if not column.nullable and (default is None or callable(default)):
                    # SQLite can't add a NOT NULL column without a constant
                    # default; nothing in these models does that, but refuse
                    # loudly rather than write a broken schema.
                    logger.warning(
                        "cannot add NOT NULL column %s.%s without a default; skipping",
                        table.name, column.name,
                    )
                    continue
                ddl = f"{column.name} {column.type.compile(engine.dialect)}"
                if default is not None and not callable(default):
                    ddl += f" DEFAULT {_sql_literal(default)}"
                if not column.nullable:
                    ddl += " NOT NULL"
                logger.info("migrating: adding column %s.%s", table.name, column.name)
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))

            # Indexes declared on columns that were just added don't exist yet.
            for index in table.indexes:
                columns = ", ".join(col.name for col in index.columns)
                try:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index.name} ON {table.name} ({columns})"))
                except OperationalError as exc:
                    logger.debug("index %s not created: %s", index.name, exc)


def init_db() -> None:
    import models  # noqa: F401  (registers models on Base before create_all)

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()

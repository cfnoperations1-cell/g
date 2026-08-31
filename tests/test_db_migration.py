"""The clinics agent added columns to a table the vendor scraper may already
have created. create_all() never alters an existing table, so init_db() has to
add them itself or an existing leads.db breaks with "no such column"."""
from sqlalchemy import create_engine, inspect, text

import db
from db import _sql_literal


def test_sql_literal_renders_each_type():
    assert _sql_literal(True) == "1"
    assert _sql_literal(False) == "0"
    assert _sql_literal(7) == "7"
    assert _sql_literal("vendor") == "'vendor'"
    assert _sql_literal("it's") == "'it''s'"


def test_missing_columns_are_added_to_an_existing_table(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")

    # A leads table as it looked before clinics existed, with a row in it.
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE leads ("
            "  id INTEGER PRIMARY KEY,"
            "  company_name VARCHAR(255) NOT NULL,"
            "  website VARCHAR(500) NOT NULL,"
            "  domain VARCHAR(255) NOT NULL,"
            "  source VARCHAR(100) NOT NULL,"
            "  sells_direct BOOLEAN NOT NULL DEFAULT 0,"
            "  manufactures BOOLEAN NOT NULL DEFAULT 0,"
            "  us_based BOOLEAN NOT NULL DEFAULT 0,"
            "  status VARCHAR(50) NOT NULL DEFAULT 'new',"
            "  opted_out BOOLEAN NOT NULL DEFAULT 0"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO leads (company_name, website, domain, source) "
            "VALUES ('Old Vendor', 'https://old.com', 'old.com', 'seed')"
        ))

    monkeypatch.setattr(db, "engine", engine)
    db.init_db()

    columns = {col["name"] for col in inspect(engine).get_columns("leads")}
    for added in ("kind", "clinic_type", "city", "peptides_offered", "telehealth"):
        assert added in columns

    # The pre-existing row survives and reads as a vendor, so the outreach
    # agent picks the right copy for it.
    with engine.begin() as conn:
        row = conn.execute(text("SELECT company_name, kind, telehealth FROM leads")).one()
    assert row == ("Old Vendor", "vendor", 0)


def test_migration_is_idempotent(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(db, "engine", engine)
    db.init_db()
    db.init_db()  # a second run must not fail on already-present columns

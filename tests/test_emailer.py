import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from db import Base
from emailer.agent import build_email, load_message, pending_leads, render
from models import Lead, Outreach


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def add_lead(session, domain, email="sales@x.com", manufactures=True):
    lead = Lead(
        company_name=domain, website=f"https://{domain}", domain=domain,
        email=email, manufactures=manufactures, sells_direct=True, us_based=True,
        source="test", status="new",
    )
    session.add(lead)
    session.commit()
    return lead


def test_load_message_splits_subject_and_body(tmp_path):
    f = tmp_path / "m.txt"
    f.write_text("Subject: Hello there\n\nBody line one.\nBody line two.\n")
    subject, body = load_message(f)
    assert subject == "Hello there"
    assert body.startswith("Body line one.")


def test_load_message_requires_subject(tmp_path):
    f = tmp_path / "m.txt"
    f.write_text("No subject here\n")
    with pytest.raises(ValueError):
        load_message(f)


def test_real_message_template_renders_without_placeholders_left():
    subject, body = load_message(__import__("pathlib").Path("emailer/message.txt"))
    assert "Jonathan Cole" in body
    rendered = render(body)
    assert "{" not in rendered and "}" not in rendered


def test_pending_leads_excludes_already_contacted():
    session = make_session()
    a = add_lead(session, "a.com", "a@a.com")
    add_lead(session, "b.com", "b@b.com")
    session.add(Outreach(lead_id=a.id, to_email="a@a.com", subject="s", body="b", delivery="sent"))
    session.commit()

    pending = pending_leads(session, only_manufacturers=False, limit=None)
    assert [lead.domain for lead in pending] == ["b.com"]


def test_pending_leads_skips_leads_without_email():
    session = make_session()
    add_lead(session, "a.com", email=None)
    add_lead(session, "b.com", email="")
    add_lead(session, "c.com", email="c@c.com")
    assert [lead.domain for lead in pending_leads(session, False, None)] == ["c.com"]


def test_pending_leads_can_filter_to_manufacturers():
    session = make_session()
    add_lead(session, "maker.com", "m@m.com", manufactures=True)
    add_lead(session, "reseller.com", "r@r.com", manufactures=False)
    pending = pending_leads(session, only_manufacturers=True, limit=None)
    assert [lead.domain for lead in pending] == ["maker.com"]


def test_pending_leads_respects_limit():
    session = make_session()
    for i in range(5):
        add_lead(session, f"d{i}.com", f"d{i}@d.com")
    assert len(pending_leads(session, False, limit=2)) == 2


def test_build_email_addresses_the_lead(monkeypatch):
    monkeypatch.setattr(config, "SENDER_EMAIL", "jon@example.com")
    monkeypatch.setattr(config, "SENDER_NAME", "Jonathan Cole")
    session = make_session()
    lead = add_lead(session, "acme.com", "sales@acme.com")

    msg = build_email(lead, "Subject here", "Body here")
    assert msg["To"] == "sales@acme.com"
    assert msg["Subject"] == "Subject here"
    assert "jon@example.com" in msg["From"]
    assert msg["Reply-To"] == "jon@example.com"
    assert "Body here" in msg.get_content()

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from db import Base
from emailer.agent import build_email, load_message, next_step_for, plan_outreach, render
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


def plan(session, **kw):
    kw.setdefault("only_manufacturers", False)
    kw.setdefault("limit", None)
    kw.setdefault("interval_days", 3)
    kw.setdefault("max_followups", 4)
    return plan_outreach(session, **kw)


def test_plan_skips_leads_without_email():
    session = make_session()
    add_lead(session, "a.com", email=None)
    add_lead(session, "b.com", email="")
    add_lead(session, "c.com", email="c@c.com")
    assert [lead.domain for lead, _ in plan(session)] == ["c.com"]


def test_plan_can_filter_to_manufacturers():
    session = make_session()
    add_lead(session, "maker.com", "m@m.com", manufactures=True)
    add_lead(session, "reseller.com", "r@r.com", manufactures=False)
    assert [lead.domain for lead, _ in plan(session, only_manufacturers=True)] == ["maker.com"]


def test_plan_respects_limit():
    session = make_session()
    for i in range(5):
        add_lead(session, f"d{i}.com", f"d{i}@d.com")
    assert len(plan(session, limit=2)) == 2


# --- follow-up cadence -------------------------------------------------

NOW = datetime(2026, 8, 20, 12, 0)


def sent(days_ago, step=1):
    return Outreach(
        lead_id=1, to_email="x@x.com", step=step, subject="s", body="b",
        delivery="sent", created_at=NOW - timedelta(days=days_ago),
    )


def bare_lead(**kw):
    return Lead(company_name="X", website="https://x.com", domain="x.com",
                email="x@x.com", source="t", status=kw.pop("status", "new"), **kw)


def test_never_contacted_is_due_for_first_email():
    assert next_step_for(bare_lead(), [], 3, 4, NOW) == 1


def test_not_due_before_the_interval_elapses():
    assert next_step_for(bare_lead(), [sent(2)], 3, 4, NOW) is None


def test_due_for_first_followup_after_three_days():
    assert next_step_for(bare_lead(), [sent(3)], 3, 4, NOW) == 2


def test_followups_repeat_every_interval():
    history = [sent(9, 1), sent(6, 2), sent(3, 3)]
    assert next_step_for(bare_lead(), history, 3, 4, NOW) == 4


def test_sequence_stops_once_they_reply():
    lead = bare_lead()
    lead.replied_at = NOW - timedelta(days=1)
    assert next_step_for(lead, [sent(10)], 3, 4, NOW) is None


def test_sequence_stops_on_replied_status_set_by_hand():
    assert next_step_for(bare_lead(status="replied"), [sent(10)], 3, 4, NOW) is None


def test_sequence_stops_when_opted_out():
    lead = bare_lead()
    lead.opted_out = True
    assert next_step_for(lead, [sent(10)], 3, 4, NOW) is None


def test_sequence_stops_at_the_followup_cap():
    history = [sent(30, 1), sent(20, 2), sent(15, 3)]
    # cap of 2 follow-ups means 3 messages total -- already reached
    assert next_step_for(bare_lead(), history, 3, 2, NOW) is None


def test_failed_sends_do_not_count_toward_the_sequence():
    failed = Outreach(lead_id=1, to_email="x@x.com", step=1, subject="s", body="b",
                      delivery="failed", created_at=NOW - timedelta(days=1))
    # the only attempt failed, so this lead still needs a first contact
    assert next_step_for(bare_lead(), [failed], 3, 4, NOW) == 1


def test_plan_returns_step_numbers_per_lead():
    session = make_session()
    fresh = add_lead(session, "fresh.com", "f@f.com")
    due = add_lead(session, "due.com", "d@d.com")
    waiting = add_lead(session, "waiting.com", "w@w.com")
    session.add(Outreach(lead_id=due.id, to_email="d@d.com", step=1, subject="s", body="b",
                         delivery="sent", created_at=datetime.utcnow() - timedelta(days=5)))
    session.add(Outreach(lead_id=waiting.id, to_email="w@w.com", step=1, subject="s", body="b",
                         delivery="sent", created_at=datetime.utcnow() - timedelta(hours=6)))
    session.commit()

    result = {lead.domain: step for lead, step in plan(session)}
    assert result == {"fresh.com": 1, "due.com": 2}
    assert fresh.id and waiting.id  # both exist; waiting simply isn't due


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

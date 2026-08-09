from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from emailer.replies import apply_replies, contacted_domain_map, email_domain
from models import Lead, Outreach


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def contacted(session, domain, to_email):
    lead = Lead(company_name=domain, website=f"https://{domain}", domain=domain,
                email=to_email, source="t", status="contacted")
    session.add(lead)
    session.commit()
    session.add(Outreach(lead_id=lead.id, to_email=to_email, step=1, subject="s",
                         body="b", delivery="sent", created_at=datetime.utcnow() - timedelta(days=4)))
    session.commit()
    return lead


def test_email_domain_strips_www():
    assert email_domain("jane@www.acme.com") == "acme.com"


def test_contacted_domain_map_keys_by_domain():
    session = make_session()
    contacted(session, "acme.com", "sales@acme.com")
    assert "acme.com" in contacted_domain_map(session)


def test_reply_from_any_person_at_the_domain_counts():
    session = make_session()
    lead = contacted(session, "acme.com", "sales@acme.com")
    # replier is a different mailbox than the one we wrote to
    apply_replies(session, [("jane@acme.com", "Re: intro", "sure, let's talk")], dry_run=False)
    assert lead.replied_at is not None
    assert lead.status == "replied"
    assert lead.opted_out is False


def test_unsubscribe_request_sets_opt_out():
    session = make_session()
    lead = contacted(session, "acme.com", "sales@acme.com")
    apply_replies(session, [("jane@acme.com", "Re: intro", "Please unsubscribe me")], dry_run=False)
    assert lead.opted_out is True
    assert lead.replied_at is not None


def test_unrelated_mail_is_ignored():
    session = make_session()
    lead = contacted(session, "acme.com", "sales@acme.com")
    stats = apply_replies(session, [("newsletter@somewhere.com", "Deals", "hi")], dry_run=False)
    assert lead.replied_at is None
    assert stats["unmatched"] == 1


def test_dry_run_changes_nothing():
    session = make_session()
    lead = contacted(session, "acme.com", "sales@acme.com")
    apply_replies(session, [("jane@acme.com", "Re: intro", "interested")], dry_run=True)
    assert lead.replied_at is None
    assert lead.status == "contacted"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from emailer.agent import (
    DEFAULT_CLINIC_FOLLOWUP_FILE,
    DEFAULT_CLINIC_MESSAGE_FILE,
    DEFAULT_FOLLOWUP_FILE,
    DEFAULT_MESSAGE_FILE,
    load_message,
    plan_outreach,
    templates_for,
)
from models import Lead, LeadKind


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        Lead(company_name="Vendor", website="https://v.com", domain="v.com", source="s",
             email="sales@v.com", kind=LeadKind.VENDOR.value),
        Lead(company_name="Clinic", website="https://c.com", domain="c.com", source="s",
             email="info@c.com", kind=LeadKind.CLINIC.value),
    ])
    session.commit()
    return session


def test_each_kind_gets_its_own_copy():
    assert templates_for("vendor") == (DEFAULT_MESSAGE_FILE, DEFAULT_FOLLOWUP_FILE)
    assert templates_for("clinic") == (DEFAULT_CLINIC_MESSAGE_FILE, DEFAULT_CLINIC_FOLLOWUP_FILE)


def test_unknown_or_missing_kind_falls_back_to_vendor_copy():
    """Leads saved before kinds existed still get a usable template."""
    assert templates_for(None) == (DEFAULT_MESSAGE_FILE, DEFAULT_FOLLOWUP_FILE)
    assert templates_for("something_else") == (DEFAULT_MESSAGE_FILE, DEFAULT_FOLLOWUP_FILE)


def test_explicit_file_overrides_every_kind(tmp_path):
    override = tmp_path / "custom.txt"
    assert templates_for("clinic", message_file=override)[0] == override


def test_clinic_templates_load_and_are_clinic_specific():
    subject, body = load_message(DEFAULT_CLINIC_MESSAGE_FILE)
    assert "clinic" in subject.lower()
    assert "{sender_postal_address}" in body and "{unsubscribe_line}" in body
    # The vendor pitch is about reselling API; the clinic pitch is not.
    assert "B2C brands" not in body


def test_kind_filter_narrows_the_send_list():
    session = make_session()
    everyone = plan_outreach(session, False, None, 3, 4)
    clinics_only = plan_outreach(session, False, None, 3, 4, kind="clinic")

    assert {lead.kind for lead, _ in everyone} == {"vendor", "clinic"}
    assert [lead.company_name for lead, _ in clinics_only] == ["Clinic"]

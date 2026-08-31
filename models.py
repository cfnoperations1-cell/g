import enum
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from db import Base


class LeadStatus(str, enum.Enum):
    NEW = "new"
    CONTACTED = "contacted"
    REPLIED = "replied"
    QUALIFIED = "qualified"
    DISQUALIFIED = "disqualified"


class LeadKind(str, enum.Enum):
    """Which agent found this lead, and therefore which pitch it gets.

    Vendors (the scraper agent) and clinics (the clinics agent) share one
    table so dedup, outreach cadence, reply detection, and the CRM all work
    the same for both -- but they are different businesses with different
    outreach copy, so every query that leads to an email filters on kind.
    """

    VENDOR = "vendor"
    CLINIC = "clinic"


class CompanyType(str, enum.Enum):
    RESEARCH_ONLY = "research_only"
    CONSUMER_AND_RESEARCH = "consumer_and_research"
    COMPOUNDING_PHARMACY = "compounding_pharmacy"
    MANUFACTURING_LAB = "manufacturing_lab"


class ClinicType(str, enum.Enum):
    """What a peptide-offering clinic primarily practices."""

    MED_SPA = "med_spa"
    HORMONE_CLINIC = "hormone_clinic"
    WEIGHT_LOSS_CLINIC = "weight_loss_clinic"
    REGENERATIVE_CLINIC = "regenerative_clinic"
    WELLNESS_CLINIC = "wellness_clinic"


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("domain", name="uq_lead_domain"),)

    id = Column(Integer, primary_key=True)
    company_name = Column(String(255), nullable=False)
    website = Column(String(500), nullable=False)
    domain = Column(String(255), nullable=False, index=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    source = Column(String(100), nullable=False, default="unknown")
    matched_query = Column(String(255), nullable=True)
    # For vendors: the "research use only" wording. For clinics: the wording
    # showing they offer peptide therapy.
    research_only_evidence = Column(Text, nullable=True)
    kind = Column(String(20), nullable=False, default=LeadKind.VENDOR.value, index=True)
    company_type = Column(String(50), nullable=True)
    # --- clinic leads only (kind="clinic") ---
    clinic_type = Column(String(50), nullable=True)
    city = Column(String(120), nullable=True)
    # Comma-separated tracked compounds named on the clinic's own site.
    peptides_offered = Column(Text, nullable=True)
    telehealth = Column(Boolean, nullable=False, default=False)
    sells_direct = Column(Boolean, nullable=False, default=False)
    manufactures = Column(Boolean, nullable=False, default=False)
    us_based = Column(Boolean, nullable=False, default=False)
    state = Column(String(50), nullable=True)
    status = Column(String(50), nullable=False, default=LeadStatus.NEW.value)
    # Set when a reply is detected (or marked by hand). Stops the follow-up
    # sequence -- the whole point of the cadence is to stop once they answer.
    replied_at = Column(DateTime, nullable=True)
    opted_out = Column(Boolean, nullable=False, default=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    outreach = relationship("Outreach", back_populates="lead", cascade="all, delete-orphan")


class Outreach(Base):
    """One row per message prepared or sent to a lead.

    The emailer checks this table before contacting anyone, so re-running it
    never emails the same company twice -- important when the scraper adds
    vendors continuously and the emailer runs on a schedule behind it.
    """

    __tablename__ = "outreach"

    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    to_email = Column(String(255), nullable=False)
    # 1 = first contact, 2+ = follow-ups in the cadence.
    step = Column(Integer, nullable=False, default=1)
    subject = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)
    # "drafted" = written to disk for review; "sent" = handed to an SMTP server.
    delivery = Column(String(20), nullable=False, default="drafted")
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    lead = relationship("Lead", back_populates="outreach")


class DiscoveryState(Base):
    """Where an agent left off in its query space.

    The clinics agent walks a query space of hundreds of thousands of
    (city, service, template) combinations. Without a saved cursor every run
    would start at the top and re-search the same first few hundred queries,
    finding only clinics already in the CRM. The cursor is what makes "400
    new leads a day, every day" possible on the same keyword list.
    """

    __tablename__ = "discovery_state"

    agent = Column(String(50), primary_key=True)
    query_cursor = Column(Integer, nullable=False, default=0)
    queries_run = Column(Integer, nullable=False, default=0)
    last_run_at = Column(DateTime, nullable=True)

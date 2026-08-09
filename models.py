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


class CompanyType(str, enum.Enum):
    RESEARCH_ONLY = "research_only"
    CONSUMER_AND_RESEARCH = "consumer_and_research"
    COMPOUNDING_PHARMACY = "compounding_pharmacy"
    MANUFACTURING_LAB = "manufacturing_lab"


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
    research_only_evidence = Column(Text, nullable=True)
    company_type = Column(String(50), nullable=True)
    sells_direct = Column(Boolean, nullable=False, default=False)
    manufactures = Column(Boolean, nullable=False, default=False)
    us_based = Column(Boolean, nullable=False, default=False)
    state = Column(String(50), nullable=True)
    status = Column(String(50), nullable=False, default=LeadStatus.NEW.value)
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
    subject = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)
    # "drafted" = written to disk for review; "sent" = handed to an SMTP server.
    delivery = Column(String(20), nullable=False, default="drafted")
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    lead = relationship("Lead", back_populates="outreach")

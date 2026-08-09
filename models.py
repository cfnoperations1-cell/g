import enum
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint

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
    us_based = Column(Boolean, nullable=False, default=False)
    state = Column(String(50), nullable=True)
    status = Column(String(50), nullable=False, default=LeadStatus.NEW.value)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

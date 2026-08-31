from datetime import datetime

from flask import Blueprint, abort, redirect, render_template, request, url_for

from db import SessionLocal
from models import ClinicType, CompanyType, Lead, LeadKind, LeadStatus

bp = Blueprint("leads", __name__)

STATUS_CHOICES = [s.value for s in LeadStatus]
COMPANY_TYPE_CHOICES = [t.value for t in CompanyType]
CLINIC_TYPE_CHOICES = [t.value for t in ClinicType]
KIND_CHOICES = [k.value for k in LeadKind]


@bp.route("/")
def index():
    return redirect(url_for("leads.list_leads"))


@bp.route("/leads")
def list_leads():
    status_filter = request.args.get("status", "")
    type_filter = request.args.get("type", "")
    kind_filter = request.args.get("kind", "")
    session = SessionLocal()
    try:
        query = session.query(Lead).order_by(Lead.created_at.desc())
        if status_filter:
            query = query.filter(Lead.status == status_filter)
        if kind_filter:
            query = query.filter(Lead.kind == kind_filter)
        if type_filter:
            # One "type" filter over two columns: which one it means depends
            # on the kind of lead, so match either.
            query = query.filter((Lead.company_type == type_filter) | (Lead.clinic_type == type_filter))
        leads = query.all()
        # Only offer the type filters that apply to what's being shown.
        if kind_filter == LeadKind.CLINIC.value:
            type_choices = CLINIC_TYPE_CHOICES
        elif kind_filter == LeadKind.VENDOR.value:
            type_choices = COMPANY_TYPE_CHOICES
        else:
            type_choices = COMPANY_TYPE_CHOICES + CLINIC_TYPE_CHOICES
    finally:
        session.close()
    return render_template(
        "leads.html",
        leads=leads,
        status_choices=STATUS_CHOICES,
        current_status=status_filter,
        type_choices=type_choices,
        current_type=type_filter,
        kind_choices=KIND_CHOICES,
        current_kind=kind_filter,
    )


@bp.route("/leads/<int:lead_id>", methods=["GET", "POST"])
def lead_detail(lead_id):
    session = SessionLocal()
    try:
        lead = session.get(Lead, lead_id)
        if lead is None:
            abort(404)

        if request.method == "POST":
            lead.status = request.form.get("status", lead.status)
            if lead.kind == LeadKind.CLINIC.value:
                lead.clinic_type = request.form.get("lead_type") or None
            else:
                lead.company_type = request.form.get("lead_type") or None
            lead.notes = request.form.get("notes", lead.notes)
            lead.opted_out = request.form.get("opted_out") == "on"
            # Marking a lead replied here is what stops their follow-ups when
            # IMAP reply detection isn't configured.
            if lead.status == "replied" and lead.replied_at is None:
                lead.replied_at = datetime.utcnow()
            session.commit()
            return redirect(url_for("leads.lead_detail", lead_id=lead_id))

        is_clinic = lead.kind == LeadKind.CLINIC.value
        return render_template(
            "lead_detail.html",
            lead=lead,
            status_choices=STATUS_CHOICES,
            type_choices=CLINIC_TYPE_CHOICES if is_clinic else COMPANY_TYPE_CHOICES,
            current_type=lead.clinic_type if is_clinic else lead.company_type,
            is_clinic=is_clinic,
        )
    finally:
        session.close()


@bp.route("/leads/<int:lead_id>/delete", methods=["POST"])
def delete_lead(lead_id):
    session = SessionLocal()
    try:
        lead = session.get(Lead, lead_id)
        if lead is not None:
            session.delete(lead)
            session.commit()
    finally:
        session.close()
    return redirect(url_for("leads.list_leads"))

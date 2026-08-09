from flask import Blueprint, abort, redirect, render_template, request, url_for

from db import SessionLocal
from models import CompanyType, Lead, LeadStatus

bp = Blueprint("leads", __name__)

STATUS_CHOICES = [s.value for s in LeadStatus]
COMPANY_TYPE_CHOICES = [t.value for t in CompanyType]


@bp.route("/")
def index():
    return redirect(url_for("leads.list_leads"))


@bp.route("/leads")
def list_leads():
    status_filter = request.args.get("status", "")
    type_filter = request.args.get("type", "")
    session = SessionLocal()
    try:
        query = session.query(Lead).order_by(Lead.created_at.desc())
        if status_filter:
            query = query.filter(Lead.status == status_filter)
        if type_filter:
            query = query.filter(Lead.company_type == type_filter)
        leads = query.all()
    finally:
        session.close()
    return render_template(
        "leads.html",
        leads=leads,
        status_choices=STATUS_CHOICES,
        current_status=status_filter,
        type_choices=COMPANY_TYPE_CHOICES,
        current_type=type_filter,
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
            lead.company_type = request.form.get("company_type") or None
            lead.notes = request.form.get("notes", lead.notes)
            session.commit()
            return redirect(url_for("leads.lead_detail", lead_id=lead_id))

        return render_template(
            "lead_detail.html", lead=lead, status_choices=STATUS_CHOICES, type_choices=COMPANY_TYPE_CHOICES
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

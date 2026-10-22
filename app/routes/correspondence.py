import datetime

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user
from markupsafe import escape

from app.extensions import db
from app.models import Correspondence
from app.utils.decorators import permission_for
from app.utils.rendering import page


STATUSES = {"Received", "Draft", "Sent", "Replied", "Filed", "Closed"}


def correspondence():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            direction = request.form.get("direction", "")
            reference = request.form.get("reference_number", "").strip()
            contact = request.form.get("contact_name", "").strip()
            subject = request.form.get("subject", "").strip()
            letter_date = request.form.get("letter_date", "").strip()
            status = request.form.get("status", "")
            try:
                datetime.date.fromisoformat(letter_date)
            except ValueError:
                flash("Enter a valid letter date.", "danger")
            else:
                if direction not in {"Incoming", "Outgoing"} or status not in STATUSES or not all((reference, contact, subject)):
                    flash("Complete the required correspondence fields.", "danger")
                elif direction == "Incoming" and status in {"Draft", "Sent"}:
                    flash("Incoming letters cannot have an outgoing status.", "danger")
                elif direction == "Outgoing" and status in {"Received", "Replied"}:
                    flash("Outgoing letters cannot have an incoming status.", "danger")
                elif Correspondence.query.filter_by(reference_number=reference).first():
                    flash("That reference number is already registered.", "danger")
                else:
                    db.session.add(Correspondence(
                        direction=direction,
                        reference_number=reference,
                        contact_name=contact,
                        subject=subject,
                        details=request.form.get("details", "").strip() or None,
                        letter_date=letter_date,
                        status=status,
                        recorded_by=current_user.id,
                    ))
                    db.session.commit()
                    flash("Correspondence registered.", "success")
        elif action == "update":
            record = db.session.get(Correspondence, request.form.get("record_id", type=int))
            status = request.form.get("status", "")
            if not record:
                abort(404)
            if status not in STATUSES or (record.direction == "Incoming" and status in {"Draft", "Sent"}) or (record.direction == "Outgoing" and status in {"Received", "Replied"}):
                abort(400)
            record.status = status
            db.session.commit()
            flash("Correspondence status updated.", "success")
        else:
            abort(400)
        return redirect(url_for("correspondence", q=request.args.get("q", "")))

    search = request.args.get("q", "").strip()
    query = Correspondence.query
    if search:
        pattern = f"%{search}%"
        query = query.filter(db.or_(
            Correspondence.reference_number.ilike(pattern),
            Correspondence.contact_name.ilike(pattern),
            Correspondence.subject.ilike(pattern),
            Correspondence.details.ilike(pattern),
        ))
    records = query.order_by(Correspondence.letter_date.desc(), Correspondence.id.desc()).all()
    rows = []
    cards = []
    safe_html = lambda value: str(escape(value)).replace("{", "&#123;").replace("}", "&#125;")
    for record in records:
        valid_statuses = ("Received", "Replied", "Filed", "Closed") if record.direction == "Incoming" else ("Draft", "Sent", "Filed", "Closed")
        options = "".join(
            f"<option value='{status}'{' selected' if record.status == status else ''}>{status}</option>"
            for status in valid_statuses
        )
        status_form = (
            f"<form method='post' class='correspondence-status-form'><input type='hidden' name='action' value='update'>"
            f"<input type='hidden' name='record_id' value='{record.id}'><label for='correspondence-status-{record.id}'>Update status</label>"
            f"<div><select id='correspondence-status-{record.id}' class='form-select form-select-sm' name='status'>{options}</select>"
            f"<button class='btn btn-sm btn-outline-success'>Save</button></div></form>"
        )
        table_status_form = status_form.replace(f"correspondence-status-{record.id}", f"correspondence-status-table-{record.id}")
        card_status_form = status_form.replace(f"correspondence-status-{record.id}", f"correspondence-status-card-{record.id}")
        details_table = f"<br><small>{safe_html(record.details)}</small>" if record.details else ""
        details_card = f"<p class='correspondence-record-details'>{safe_html(record.details)}</p>" if record.details else ""
        rows.append(
            f"<tr><td>{safe_html(record.letter_date)}</td><td>{safe_html(record.direction)}</td><td><code>{safe_html(record.reference_number)}</code></td>"
            f"<td>{safe_html(record.contact_name)}</td><td>{safe_html(record.subject)}{details_table}</td>"
            f"<td>{safe_html(record.recorder.full_name if record.recorder else '-')}</td><td>{table_status_form}</td></tr>"
        )
        cards.append(
            f"<article class='correspondence-record-card'><div class='correspondence-record-top'>"
            f"<span class='correspondence-record-direction'>{safe_html(record.direction)}</span>"
            f"<time>{safe_html(record.letter_date)}</time></div>"
            f"<h3>{safe_html(record.subject)}</h3><p><strong>Reference:</strong> <code>{safe_html(record.reference_number)}</code></p>"
            f"<p><strong>Contact:</strong> {safe_html(record.contact_name)}</p>"
            f"{details_card}"
            f"<p class='correspondence-record-author'><strong>Recorded by:</strong> {safe_html(record.recorder.full_name if record.recorder else '-')}</p>"
            f"{card_status_form}</article>"
        )
    today = datetime.date.today().isoformat()
    body = f"""<section class='panel'><h1>Official <span class='accent'>Correspondence</span></h1><p class='muted'>Register incoming and outgoing letters and preserve their reference, contact, subject, and status history.</p><form method='post' class='row g-3'><input type='hidden' name='action' value='create'><div class='col-md-3'><label class='form-label'>Direction</label><select class='form-select' name='direction' id='letter-direction'><option>Incoming</option><option>Outgoing</option></select></div><div class='col-md-3'><label class='form-label'>Reference number</label><input class='form-control' name='reference_number' required></div><div class='col-md-3'><label class='form-label'>Letter date</label><input class='form-control' type='date' name='letter_date' value='{today}' required></div><div class='col-md-3'><label class='form-label'>Initial status</label><select class='form-select' name='status' id='letter-status' required><option>Received</option><option>Replied</option><option>Filed</option><option>Closed</option><option>Draft</option><option>Sent</option></select></div><div class='col-md-6'><label class='form-label'>Sender / recipient</label><input class='form-control' name='contact_name' required></div><div class='col-md-6'><label class='form-label'>Subject</label><input class='form-control' name='subject' required></div><div class='col-12'><label class='form-label'>Details / notes</label><textarea class='form-control' name='details' rows='3'></textarea></div><div class='col-12'><button class='btn btn-accent'>Register Letter</button></div></form></section><section class='panel correspondence-history-panel'><div class='correspondence-history-header'><h2>Correspondence History</h2><form method='get' class='correspondence-search'><input class='form-control' name='q' value='{safe_html(search)}' placeholder='Search reference, contact, subject'><button class='btn btn-outline-success' type='submit'>Search</button></form></div><div class='table-responsive correspondence-history-table'><table class='table align-middle'><thead><tr><th>Date</th><th>Direction</th><th>Reference</th><th>Contact</th><th>Subject / Details</th><th>Recorded by</th><th>Status</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="7" class="muted">No correspondence records found.</td></tr>'}</tbody></table></div><div class='correspondence-history-cards'>{''.join(cards) or '<p class="muted mb-0">No correspondence records found.</p>'}</div></section>"""
    body += """<style>
.correspondence-history-cards{display:none}
.correspondence-history-header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
.correspondence-history-header h2{margin:0}
.correspondence-search{display:flex;align-items:center;gap:8px;width:min(100%,360px);min-width:0}
.correspondence-search .form-control{min-width:0;flex:1 1 auto}
.correspondence-search button{flex:0 0 auto;white-space:nowrap}
.correspondence-record-card{min-width:0;padding:13px;border:1px solid var(--border);border-radius:11px;background:var(--card);overflow-wrap:anywhere}
.correspondence-record-top{display:flex;justify-content:space-between;align-items:center;gap:8px;color:var(--muted);font-size:.78rem}
.correspondence-record-direction{font-weight:750;color:var(--green-700)}
.correspondence-record-card h3{margin:9px 0;font-size:1rem;overflow-wrap:anywhere}
.correspondence-record-card p{margin:5px 0;font-size:.84rem;overflow-wrap:anywhere}
.correspondence-record-details{white-space:pre-wrap}
.correspondence-record-author{color:var(--muted)}
.correspondence-status-form{margin-top:11px;padding-top:10px;border-top:1px solid var(--border)}
.correspondence-status-form>label{display:block;margin-bottom:5px;font-size:.75rem;font-weight:700;color:var(--muted)}
.correspondence-status-form>div{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center}
.correspondence-status-form button{white-space:nowrap}
@media(max-width:600px){.correspondence-history-header{align-items:stretch}.correspondence-history-header h2{font-size:1rem}.correspondence-search{width:100%;max-width:none}.correspondence-history-table{display:none}.correspondence-history-cards{display:grid;gap:9px}.correspondence-record-card{padding:11px}}
</style>"""
    return page("Correspondence", body, "master_admin")


def register_routes(app):
    app.add_url_rule("/correspondence", endpoint="correspondence", view_func=permission_for("correspondence")(correspondence), methods=["GET", "POST"])

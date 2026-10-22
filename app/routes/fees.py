import os
import datetime
import json
from flask import request, redirect, url_for, flash, jsonify, abort, send_file, current_app
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from openpyxl import Workbook
from app.extensions import db
from app.models import User, Attendance, PendingCard, FeeTransaction, FeeRequirement, Book, DisciplineCase, SchoolMessage, Grade, TimetableEntry, Setting
from app.utils.constants import *
from app.utils.decorators import role_required
from app.utils.helpers import get_setting, set_setting, attendance_for_student, linked_children
from app.utils.rendering import page
from app.utils.decorators import permission_for
from markupsafe import escape
from app.utils.notifications import notify_student_and_parent

def fees():
    managers = {"master_admin", "accountant"}
    can_manage = current_user.role in managers
    if request.method == "POST" and not can_manage:
        abort(403)
    if request.method == "POST":
        action = request.form.get("action", "record_payment")
        student_id = request.form.get("student_id", type=int)
        student = db.session.get(User, student_id) if student_id else None
        if not student or student.role != "student":
            flash("Select a valid student account.", "danger")
        elif action == "add_requirement":
            title = request.form.get("title", "").strip()
            details = request.form.get("details", "").strip()
            try:
                amount = float(request.form.get("amount_due", 0))
                due_date = datetime.date.fromisoformat(request.form.get("due_date", ""))
            except (ValueError, TypeError):
                amount, due_date = 0, None
            if not title or amount <= 0 or not due_date:
                flash("Enter a fee name, positive amount, and valid payment deadline.", "danger")
            else:
                requirement = FeeRequirement(student_id=student.id, title=title, amount=amount,
                                             due_date=due_date, details=details or None,
                                             created_by=current_user.id)
                db.session.add(requirement)
                notify_student_and_parent(student, "Fees", f"Fee request: {title}",
                                          f"{title}: {amount:,.2f} RWF due by {due_date.isoformat()}. {details}".strip(), "/fees")
                db.session.commit()
                flash("Fee requirement and deadline saved.", "success")
                return redirect(url_for("fees"))
        else:
            try:
                amount = float(request.form.get("amount", 0))
            except ValueError:
                amount = 0
            method = request.form.get("method", "Cash")
            reference = request.form.get("reference", "").strip()
            requirement_id = request.form.get("requirement_id", type=int) or None
            requirement = db.session.get(FeeRequirement, requirement_id) if requirement_id else None
            if amount <= 0:
                flash("Enter a positive payment amount.", "danger")
            elif requirement_id and (not requirement or requirement.student_id != student.id):
                abort(400)
            else:
                tx = FeeTransaction(student_id=student.id, requirement_id=requirement_id,
                                    amount=amount, method=method, reference=reference,
                                    created_at=now_string())
                db.session.add(tx)
                db.session.commit()
                flash("Fee payment recorded successfully.", "success")
                return redirect(url_for("fees"))
    students = User.query.filter_by(role="student").order_by(User.full_name).all() if can_manage else []
    tx_query = FeeTransaction.query
    requirement_query = FeeRequirement.query
    if current_user.role == "student":
        tx_query = tx_query.filter_by(student_id=current_user.id)
        requirement_query = requirement_query.filter_by(student_id=current_user.id)
    elif current_user.role == "parent":
        child_ids = [child.id for child in linked_children(current_user.id)]
        tx_query = tx_query.filter(FeeTransaction.student_id.in_(child_ids))
        requirement_query = requirement_query.filter(FeeRequirement.student_id.in_(child_ids))
    transactions = tx_query.order_by(FeeTransaction.id.desc()).all()
    requirements = requirement_query.order_by(FeeRequirement.due_date, FeeRequirement.title).all()
    student_options = "".join(f"<option value='{s.id}'>{s.full_name} ({s.class_name or 'No class'})</option>" for s in students)
    all_requirements = FeeRequirement.query.order_by(FeeRequirement.due_date, FeeRequirement.title).all() if can_manage else requirements
    requirement_options = "".join(f"<option value='{r.id}'>{escape(r.student.full_name if r.student else '-')} — {escape(r.title)} ({r.amount:,.2f} RWF)</option>" for r in all_requirements)
    tx_rows = "".join(f"<tr><td>{escape(t.created_at)}</td><td>{escape(t.student.full_name if t.student else '-')}</td><td>{escape(t.requirement.title if t.requirement else 'Unallocated')}</td><td>{t.amount:,.2f} RWF</td><td>{escape(t.method)}</td><td>{escape(t.reference or '-')}</td></tr>" for t in transactions)
    paid_by_student = {}
    due_by_student = {}
    paid_by_requirement = {}
    for transaction in transactions:
        paid_by_student[transaction.student_id] = paid_by_student.get(transaction.student_id, 0) + (transaction.amount or 0)
        if transaction.requirement_id:
            paid_by_requirement[transaction.requirement_id] = paid_by_requirement.get(transaction.requirement_id, 0) + (transaction.amount or 0)
    for requirement in requirements:
        due_by_student[requirement.student_id] = due_by_student.get(requirement.student_id, 0) + (requirement.amount or 0)
    visible_students = students if can_manage else ([current_user] if current_user.role == "student" else linked_children(current_user.id) if current_user.role == "parent" else User.query.filter_by(role="student").order_by(User.full_name).all())
    balance_rows = []
    for student in visible_students:
        paid = paid_by_student.get(student.id, 0)
        due = due_by_student.get(student.id, 0)
        balance_rows.append(f"<tr><td>{escape(student.full_name)}</td><td>{due:,.2f} RWF</td><td>{paid:,.2f} RWF</td><td>{max(0, due - paid):,.2f} RWF</td></tr>")
    requirement_rows = "".join(f"<tr><td>{escape(r.student.full_name if r.student else '-')}</td><td>{escape(r.title)}</td><td>{r.amount:,.2f} RWF</td><td>{paid_by_requirement.get(r.id, 0):,.2f} RWF</td><td>{max(0, r.amount - paid_by_requirement.get(r.id, 0)):,.2f} RWF</td><td>{r.due_date.isoformat()}</td><td>{'Overdue' if r.due_date < datetime.date.today() and max(0, r.amount - paid_by_requirement.get(r.id, 0)) > 0 else 'Paid' if max(0, r.amount - paid_by_requirement.get(r.id, 0)) == 0 else 'Upcoming'}</td><td>{escape(r.details or '-')}</td></tr>" for r in requirements)
    requirement_form = f"""<section class="panel"><h2>Set Fee Requirement</h2><form method="post" class="row g-3"><input type="hidden" name="action" value="add_requirement"><div class="col-md-6"><label>Student</label><select class="form-select" name="student_id" required><option value="">Choose student...</option>{student_options}</select></div><div class="col-md-6"><label>Fee Name / Requirement</label><input class="form-control" name="title" placeholder="e.g. Term 1 tuition" required></div><div class="col-md-6"><label>Amount Due (RWF)</label><input type="number" min="0.01" step="any" class="form-control" name="amount_due" required></div><div class="col-md-6"><label>Payment Deadline</label><input type="date" class="form-control" name="due_date" required></div><div class="col-12"><label>Details</label><textarea class="form-control" name="details" rows="2"></textarea></div><div class="col-12"><button class="btn btn-accent">Save Requirement</button></div></form></section>
<section class="panel"><h2>Record Payment</h2><form method="post" class="row g-3"><input type="hidden" name="action" value="record_payment"><div class="col-md-6"><label>Student</label><select class="form-select" name="student_id" required><option value="">Choose student...</option>{student_options}</select></div><div class="col-md-6"><label>Apply to Fee Requirement (Optional)</label><select class="form-select" name="requirement_id"><option value="">Unallocated payment</option>{requirement_options}</select></div><div class="col-md-6"><label>Amount Paid (RWF)</label><input type="number" min="0.01" step="any" class="form-control" name="amount" required></div><div class="col-md-6"><label>Payment Method</label><select class="form-select" name="method"><option>Cash</option><option>Bank Transfer</option><option>Mobile Money</option></select></div><div class="col-md-6"><label>Reference / Receipt No.</label><input class="form-control" name="reference"></div><div class="col-12"><button class="btn btn-accent">Record Payment</button></div></form></section>""" if can_manage else ""
    body = f"""<section class="panel"><h1>School <span class="accent">Fees</span></h1><p class="muted">Requirements, paid amounts, outstanding balances, payment deadlines, and transaction history.</p></section>
{requirement_form}<section class="panel"><h2>Fee Balance Summary</h2><div class="table-responsive"><table class="table"><tr><th>Student</th><th>Required</th><th>Paid</th><th>Outstanding</th></tr>{''.join(balance_rows) or '<tr><td colspan="4" class="muted">No fee records are available.</td></tr>'}</table></div></section>
<section class="panel"><h2>Fee Requirements & Deadlines</h2><div class="table-responsive"><table class="table fee-requirements-table"><tr><th>Student</th><th>Requirement</th><th>Required</th><th>Paid to Requirement</th><th>Outstanding</th><th>Deadline</th><th>Status</th><th>Details</th></tr>{requirement_rows or '<tr><td colspan="8" class="muted">No fee requirements have been entered.</td></tr>'}</table></div></section>
<section class="panel"><h2>Payment History</h2><div class="table-responsive"><table class="table"><tr><th>Date</th><th>Student</th><th>Requirement</th><th>Amount Paid</th><th>Method</th><th>Reference</th></tr>{tx_rows or '<tr><td colspan="6" class="muted">No payments have been recorded.</td></tr>'}</table></div></section>"""
    return page("Fees", body, "accountant")

def register_routes(app):
    app.add_url_rule('/fees', endpoint='fees', view_func=permission_for('fees')(fees), methods=['GET', 'POST'])

import datetime

from flask import abort, request
from markupsafe import escape

from app.extensions import db
from app.models import User
from app.utils.decorators import permission_for
from app.utils.helpers import get_setting
from app.utils.rendering import page


LETTER_TYPES = {
    "enrollment": "Enrollment Letter",
    "confirmation": "Student Confirmation Letter",
    "other": "Other Approved Document",
}


def letters_certificates():
    students = User.query.filter_by(role="student").order_by(User.full_name).all()
    student_options = "".join(
        f"<option value='{student.id}'>{escape(student.full_name)} ({escape(student.class_name or 'No class')})</option>"
        for student in students
    )
    generated = ""
    if request.method == "POST":
        student = db.session.get(User, request.form.get("student_id", type=int))
        letter_type = request.form.get("letter_type", "")
        recipient = request.form.get("recipient", "").strip()
        details = request.form.get("details", "").strip()
        if not student or student.role != "student" or letter_type not in LETTER_TYPES:
            abort(400)
        if letter_type == "other" and not details:
            abort(400)
        school_name = escape(get_setting("school_name", "Lycée de Muhura"))
        date_text = datetime.date.today().strftime("%d %B %Y")
        if letter_type == "enrollment":
            letter_body = f"This letter confirms that {escape(student.full_name)} is enrolled at {school_name} in {escape(student.class_name or 'a class not yet assigned')}, {escape(student.level or 'level not recorded')}, in the {escape(student.trade or 'programme not recorded')} programme."
        elif letter_type == "confirmation":
            letter_body = f"This is to confirm the student status of {escape(student.full_name)}, student code {escape(student.sdms_code or 'not assigned')}, currently registered in {escape(student.class_name or 'a class not yet assigned')}."
        else:
            letter_body = escape(details)
        salutation = f"<p>To: {escape(recipient)}</p>" if recipient else ""
        generated = f"""<article class='official-letter'><div class='text-center mb-5'><h2>{school_name}</h2><p>Official Student Document</p></div><p class='text-end'>{escape(date_text)}</p>{salutation}<h3 class='text-center my-4'>{escape(LETTER_TYPES[letter_type])}</h3><p>To whom it may concern,</p><p style='white-space:pre-wrap'>{letter_body}</p><p>This document is issued by the school administration upon request.</p><div class='mt-5 pt-4'><p>____________________________</p><p>Authorized School Official</p><p>{school_name}</p></div></article><div class='text-center mt-3'><button type='button' class='btn btn-accent' onclick='window.print()'>Print / Save as PDF</button></div>"""
    body = f"""<section class='panel'><h1>Letters <span class='accent'>& Certificates</span></h1><p class='muted'>Generate approved student enrollment and confirmation letters for printing or saving as PDF.</p><form method='post' class='row g-3'><div class='col-md-6'><label class='form-label'>Student</label><select class='form-select' name='student_id' required><option value=''>Choose student</option>{student_options}</select></div><div class='col-md-6'><label class='form-label'>Document type</label><select class='form-select' name='letter_type' id='letter-type' required><option value=''>Choose document</option><option value='enrollment'>Enrollment Letter</option><option value='confirmation'>Student Confirmation Letter</option><option value='other'>Other Approved Document</option></select></div><div class='col-md-6'><label class='form-label'>Recipient (optional)</label><input class='form-control' name='recipient' placeholder='To whom it may concern'></div><div class='col-12' id='letter-details' hidden><label class='form-label'>Approved document text</label><textarea class='form-control' name='details' rows='4'></textarea></div><div class='col-12'><button class='btn btn-accent'>Generate Document</button></div></form></section>{generated}<style>@media print{{body *{{visibility:hidden}}.official-letter,.official-letter *{{visibility:visible}}.official-letter{{position:absolute;left:0;top:0;width:100%;padding:25mm;color:#111;font-family:serif;font-size:12pt}}.official-letter h2{{color:#111}}}}</style><script>document.getElementById('letter-type').addEventListener('change',function(){{const field=document.getElementById('letter-details');field.hidden=this.value!=='other';field.querySelector('textarea').required=this.value==='other';}});</script>"""
    return page("Letters & Certificates", body, "master_admin")


def register_routes(app):
    app.add_url_rule("/letters-certificates", endpoint="letters_certificates", view_func=permission_for("letters_certificates")(letters_certificates), methods=["GET", "POST"])

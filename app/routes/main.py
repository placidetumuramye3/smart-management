import os
import datetime
import json
from flask import request, redirect, url_for, flash, jsonify, abort, send_file, current_app
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from openpyxl import Workbook
from app.extensions import db
from app.models import User, Attendance, PendingCard, FeeTransaction, Book, DisciplineCase, SchoolMessage, Grade, TimetableEntry, Setting
from app.utils.constants import *
from app.utils.decorators import role_required
from app.utils.helpers import get_setting, set_setting, attendance_for_student
from app.utils.rendering import page

def school_logo():
    filename = get_setting("school_logo", "logo.png")
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    if os.path.isfile(path):
        return send_file(path)
    abort(404)

def home():
    body = """<section class="panel py-5"><div class="row align-items-center g-4"><div class="col-md-3 text-center"><img src="/school-logo" onerror="this.style.display='none'" alt="School logo" style="max-width:100%;width:210px;max-height:210px;object-fit:contain;background:#fff;border-radius:18px;padding:10px;border:2px solid var(--accent)"></div><div class="col-md-9"><span class="pill mb-3">EXCELLENCE • DISCIPLINE • INTEGRITY • INNOVATION</span><h1 class="mt-2">Welcome to <span class="accent">Lycée de Muhura</span></h1>{% if school_motto %}<p class="fs-4 fw-bold accent">“{{ school_motto }}”</p>{% endif %}<p class="muted fs-5">Empowering students through advanced RFID Attendance, Financial transparency, Library Management, Discipline Tracking, and seamless Parent Communications.</p><div class="d-flex gap-3 flex-wrap mt-4"><a class="btn btn-accent btn-lg" href="/login">Sign In to Portal</a><a class="btn btn-outline-success btn-lg" href="/student/rfid-login" style="border-color:var(--accent);color:var(--text)">RFID Scanner Endpoint</a></div></div></div></section>
<div class="row g-4 mt-2"><div class="col-md-3"><div class="panel h-100"><h3 class="accent">Excellence</h3><p class="muted">Striving for top academic achievement through digital report cards and structured timetables.</p></div></div><div class="col-md-3"><div class="panel h-100"><h3 class="accent">Discipline</h3><p class="muted">Automated RFID morning and afternoon time-window attendance tracking.</p></div></div><div class="col-md-3"><div class="panel h-100"><h3 class="accent">Integrity</h3><p class="muted">Transparent fee balances and secure role-based access control.</p></div></div><div class="col-md-3"><div class="panel h-100"><h3 class="accent">Innovation</h3><p class="muted">Modern school automation with instant messaging and live data exports.</p></div></div></div>"""
    return page("Home", body, "home", school_motto=get_setting("school_motto", "").strip())

def register_routes(app):
    app.add_url_rule('/school-logo', endpoint='school_logo', view_func=school_logo, methods=['GET'])
    app.add_url_rule('/', endpoint='home', view_func=home, methods=['GET'])

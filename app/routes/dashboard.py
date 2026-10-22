import os
import datetime
import json
from flask import request, redirect, url_for, flash, jsonify, abort, send_file, current_app
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from openpyxl import Workbook
from markupsafe import escape
from app.extensions import db
from app.models import User, Attendance, PendingCard, FeeTransaction, FeeRequirement, Book, BookLoan, DisciplineCase, SchoolMessage, Grade, TimetableEntry, DayScheduleEntry, Setting, Assessment, AssessmentSubmission, AdmissionApplicant, Appointment, UserNotification, ActivityLog
from app.utils.constants import *
from app.utils.decorators import role_required
from app.utils.helpers import get_setting, set_setting, attendance_for_student
from app.utils.rendering import page

from app.utils.helpers import linked_children
from app.utils.decorators import permission_for


def _office_activity_svg(labels, attendance, student_activity):
    left, right, top, bottom = 48, 620, 20, 196
    maximum = max(1, *attendance, *student_activity)
    grid = "".join(
        f"<line x1='{left}' y1='{top + (bottom - top) * index / 4}' x2='{right}' y2='{top + (bottom - top) * index / 4}' stroke='currentColor' stroke-opacity='.12'/><text x='{left - 10}' y='{top + (bottom - top) * index / 4 + 4}' text-anchor='end' fill='currentColor' font-size='11'>{round(maximum * (4 - index) / 4)}</text>"
        for index in range(5)
    )
    label_markup = "".join(
        f"<text class='activity-label' x='{left + (right - left) * index / max(1, len(labels) - 1)}' y='{bottom + 22}' text-anchor='middle' fill='currentColor' font-size='10'>{label}</text>"
        for index, label in enumerate(labels)
    )
    series_markup = []
    for series_id, values, color in (
        ("attendance", attendance, "#0b9b70"),
        ("student-changes", student_activity, "#d9ad2b"),
    ):
        points = [
            (left + (right - left) * index / max(1, len(values) - 1), bottom - (bottom - top) * value / maximum)
            for index, value in enumerate(values)
        ]
        point_string = " ".join(f"{x},{y}" for x, y in points)
        circles = "".join(f"<circle cx='{x}' cy='{y}' r='4' fill='{color}'/>" for x, y in points)
        series_markup.append(f"<polyline id='series-{series_id}' points='{point_string}' fill='none' stroke='{color}' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'/><g id='points-{series_id}'>{circles}</g>")
    return (
        f"<svg id='secretaryActivityChartSvg' viewBox='0 0 640 250' role='img' aria-label='Attendance and student activity during the last seven days' style='width:100%;height:100%;color:var(--muted)'>"
        f"{grid}{series_markup[0]}{series_markup[1]}{label_markup}"
        "<circle cx='55' cy='240' r='4' fill='#0b9b70'/><text x='64' y='244' fill='currentColor' font-size='11'>Attendance</text>"
        "<circle cx='210' cy='240' r='4' fill='#d9ad2b'/><text x='219' y='244' fill='currentColor' font-size='11'>Student changes</text></svg>"
    )


def dashboard():
    role = current_user.role
    if role == "master_admin":
        today = datetime.date.today(); today_iso = today.isoformat()
        total_students = User.query.filter_by(role="student").count()
        total_teachers = User.query.filter_by(role="teacher").count()
        total_accounts = User.query.count()
        today_attendance = Attendance.query.filter_by(day=today_iso).count()
        pending_cards = PendingCard.query.filter_by(state="Pending").count()
        recent_attendance = Attendance.query.order_by(Attendance.id.desc()).limit(7).all()
        recent_rows = "".join(f"<tr><td><strong>{a.student.full_name if a.student else 'Unknown student'}</strong></td><td>{a.day}</td><td><span class='pill'>{a.status}</span></td><td>{a.arrival_time or '-'}</td></tr>" for a in recent_attendance)
        labels=[]; attendance_series=[]
        for i in range(6,-1,-1):
            d=today-datetime.timedelta(days=i); labels.append(d.strftime("%d %b")); attendance_series.append(Attendance.query.filter_by(day=d.isoformat()).count())
        trade_rows = db.session.query(User.trade, db.func.count(User.id)).filter(User.role=="student").group_by(User.trade).all()
        trade_labels=[r[0] or "Not specified" for r in trade_rows]; trade_values=[int(r[1]) for r in trade_rows]
        timeline_items = "".join(f"<div class='timeline-item'><span class='timeline-dot'></span><strong>{a.student.full_name if a.student else 'Unknown student'} — {a.status}</strong><small>{a.day} {a.arrival_time or ''}</small></div>" for a in recent_attendance[:5])
        body = """
<div class="dashboard-heading"><div><span class="pill">SCHOOL OVERVIEW</span><h1 class="mt-2">Welcome back, <span class="accent">{{ current_user.full_name }}</span></h1><p>Live overview based only on records currently stored in the school database.</p></div><a class="btn btn-accent" href="/admin"><i class="bi bi-gear me-1"></i> Admin Center</a></div>
<section class="panel hero-card"><div class="row align-items-center g-4"><div class="col-lg-8"><div class="small text-uppercase fw-bold opacity-75">Lycée de Muhura</div><h2 class="display-6 fw-bold mt-2 mb-2">School Management Dashboard</h2><p class="muted mb-0">Attendance, students, academics, finance, library, discipline and communication in one place.</p></div><div class="col-lg-4 text-lg-end"><div class="small opacity-75">System accounts</div><div class="display-5 fw-bold">{{ total_accounts }}</div></div></div></section>
<section class="grid mb-4"><a class="stat text-decoration-none" href="/students"><div class="stat-top"><span class="stat-icon"><i class="bi bi-people"></i></span><i class="bi bi-arrow-up-right muted"></i></div><div class="stat-value">{{ total_students }}</div><div class="stat-label">Total Students</div><div class="stat-note">Registered in database</div></a><div class="stat"><div class="stat-top"><span class="stat-icon"><i class="bi bi-person-badge"></i></span></div><div class="stat-value">{{ total_teachers }}</div><div class="stat-label">Teachers</div><div class="stat-note">Teacher accounts</div></div><a class="stat text-decoration-none" href="/attendance"><div class="stat-top"><span class="stat-icon"><i class="bi bi-calendar-check"></i></span><i class="bi bi-arrow-up-right muted"></i></div><div class="stat-value">{{ today_attendance }}</div><div class="stat-label">Attendance Records Today</div><div class="stat-note">Saved for {{ today_iso }}</div></a><div class="stat"><div class="stat-top"><span class="stat-icon"><i class="bi bi-credit-card-2-front"></i></span></div><div class="stat-value">{{ pending_cards }}</div><div class="stat-label">Pending RFID Cards</div><div class="stat-note">Waiting for registration</div></div></section>
<div class="row g-4"><div class="col-xl-8"><section class="panel chart-card"><div class="dashboard-heading mb-2"><div><h2>Attendance Trend</h2><p>Actual attendance records saved during the last 7 calendar days.</p></div><span class="pill">LIVE DATA</span></div><div class="chart-wrap"><canvas id="attendanceChart"></canvas></div></section></div><div class="col-xl-4"><section class="panel chart-card"><div class="dashboard-heading mb-2"><div><h2>Students by Trade</h2><p>Only trades currently present in the database.</p></div></div><div class="chart-wrap"><canvas id="tradeChart"></canvas></div></section></div></div>
<div class="row g-4"><div class="col-xl-7"><section class="panel"><div class="dashboard-heading"><div><h2>Recent Attendance Activity</h2><p>Latest saved attendance records.</p></div><a href="/attendance" class="btn btn-sm btn-outline-success">View all</a></div><div class="table-responsive"><table class="table align-middle"><thead><tr><th>Student</th><th>Date</th><th>Status</th><th>Arrival</th></tr></thead><tbody>{{ recent_rows|safe or '<tr><td colspan="4" class="muted">No attendance records have been saved yet.</td></tr>'|safe }}</tbody></table></div></section></div><div class="col-xl-5"><section class="panel"><div class="dashboard-heading"><div><h2>Activity Timeline</h2><p>Latest attendance events.</p></div></div><div class="timeline">{{ timeline_items|safe or '<div class="muted">No recent activity is available.</div>'|safe }}</div></section></div></div>
<section class="panel"><div class="dashboard-heading"><div><h2>Quick Access</h2><p>Open the most-used school modules.</p></div></div><div class="row g-3 quick-access-grid"><div class="col-md-3"><a class="quick-link" href="/students"><i class="bi bi-people"></i><span><strong>Students</strong><small class="d-block muted">Registration & records</small></span></a></div><div class="col-md-3"><a class="quick-link" href="/grades"><i class="bi bi-journal-text"></i><span><strong>Report Cards</strong><small class="d-block muted">Academic results</small></span></a></div><div class="col-md-3"><a class="quick-link" href="/fees"><i class="bi bi-cash-stack"></i><span><strong>Fees</strong><small class="d-block muted">Payment records</small></span></a></div><div class="col-md-3"><a class="quick-link" href="/timetable"><i class="bi bi-calendar3"></i><span><strong>Timetable</strong><small class="d-block muted">Class schedules</small></span></a></div></div></section>
<script>const chartText=getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();new Chart(document.getElementById('attendanceChart'),{type:'line',data:{labels:{{ labels|safe }},datasets:[{label:'Attendance records',data:{{ attendance_series|safe }},borderColor:'#0b9b70',backgroundColor:'rgba(11,155,112,.12)',fill:true,tension:.38,pointRadius:4,pointHoverRadius:6}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,ticks:{precision:0,color:chartText},grid:{color:'rgba(120,135,140,.12)'}},x:{ticks:{color:chartText},grid:{display:false}}}}});new Chart(document.getElementById('tradeChart'),{type:'doughnut',data:{labels:{{ trade_labels|safe }},datasets:[{data:{{ trade_values|safe }},backgroundColor:['#0b9b70','#39bd72','#d9ad2b','#8cc63e','#7a8b85','#4f6f64'],borderWidth:0}]},options:{responsive:true,maintainAspectRatio:false,cutout:'68%',plugins:{legend:{position:'bottom',labels:{color:chartText,usePointStyle:true,padding:14}}}}});</script>
"""
        return page("Analytics Dashboard", body, "master_admin", total_accounts=total_accounts,total_students=total_students,total_teachers=total_teachers,today_attendance=today_attendance,pending_cards=pending_cards,today_iso=today_iso,recent_rows=recent_rows,timeline_items=timeline_items,labels=json.dumps(labels),attendance_series=json.dumps(attendance_series),trade_labels=json.dumps(trade_labels),trade_values=json.dumps(trade_values))
    if role == "head_teacher":
        student_count = User.query.filter_by(role="student").count()
        teacher_count = User.query.filter_by(role="teacher").count()
        today_count = Attendance.query.filter_by(day=datetime.date.today().isoformat()).count()
        case_count = DisciplineCase.query.count()
        return page("Head Teacher Dashboard", f"""<div class='dashboard-heading'><div><span class='pill'>SCHOOL LEADERSHIP</span><h1 class='mt-2'>Welcome, <span class='accent'>{current_user.full_name}</span></h1><p>School-wide overview of students, teaching staff, attendance, academics, and discipline.</p></div></div><section class='grid mb-4'><div class='stat'><div class='stat-value'>{student_count}</div><div class='stat-label'>Students</div></div><div class='stat'><div class='stat-value'>{teacher_count}</div><div class='stat-label'>Teachers</div></div><div class='stat'><div class='stat-value'>{today_count}</div><div class='stat-label'>Attendance records today</div></div><div class='stat'><div class='stat-value'>{case_count}</div><div class='stat-label'>Discipline cases</div></div></section><section class='panel'><h2>School Oversight</h2><p class='muted'>Review school activity and support teachers and students across academic and discipline matters.</p><div class='d-flex gap-2 flex-wrap'><a class='btn btn-accent' href='/academic-management'>Academic Management</a><a class='btn btn-outline-success' href='/students'>Students</a><a class='btn btn-outline-success' href='/attendance'>Attendance</a><a class='btn btn-outline-success' href='/grades'>Examination Results</a><a class='btn btn-outline-success' href='/assessments'>Assignments & Assessments</a><a class='btn btn-outline-success' href='/discipline'>Discipline Reports</a><a class='btn btn-outline-success' href='/timetable'>Timetables</a></div></section>""", "head_teacher")
    if role == "teacher":
        students_count=User.query.filter_by(role="student").count(); attendance_count=Attendance.query.filter_by(day=datetime.date.today().isoformat()).count(); grades_count=Grade.query.filter_by(teacher_id=current_user.id).count()
        assigned_assessments = Assessment.query.filter_by(teacher_id=current_user.id, is_published=True).all()
        assigned_total = completed_total = 0
        graded_percentages = []
        for assessment in assigned_assessments:
            if assessment.target_student_ids:
                try:
                    target_ids = {int(value) for value in json.loads(assessment.target_student_ids)}
                except (TypeError, ValueError, json.JSONDecodeError):
                    target_ids = set()
                assigned_students = User.query.filter(
                    User.id.in_(target_ids), User.role == "student", User.class_name == assessment.class_name
                ).all() if target_ids else []
            else:
                assigned_students = User.query.filter_by(role="student", class_name=assessment.class_name).all()
            assigned_ids = {student.id for student in assigned_students}
            assigned_total += len(assigned_ids)
            submissions = AssessmentSubmission.query.filter_by(assessment_id=assessment.id).all()
            visible_submissions = [submission for submission in submissions if submission.student_id in assigned_ids]
            completed_total += len(visible_submissions)
            if assessment.max_score > 0:
                graded_percentages.extend(
                    submission.score / assessment.max_score * 100 for submission in visible_submissions
                    if submission.is_graded and submission.score is not None)
        completion_rate = f"{completed_total / assigned_total * 100:.0f}%" if assigned_total else "—"
        average_assessment_grade = f"{sum(graded_percentages) / len(graded_percentages):.1f}%" if graded_percentages else "—"
        body=f"""<div class='dashboard-heading'><div><span class='pill'>TEACHER WORKSPACE</span><h1 class='mt-2'>Welcome, <span class='accent'>{current_user.full_name}</span></h1><p>Manage attendance, students, timetables and academic records.</p></div></div><section class='grid mb-4'><div class='stat'><div class='stat-top'><span class='stat-icon'><i class='bi bi-people'></i></span></div><div class='stat-value'>{students_count}</div><div class='stat-label'>Students</div></div><div class='stat'><div class='stat-top'><span class='stat-icon'><i class='bi bi-calendar-check'></i></span></div><div class='stat-value'>{attendance_count}</div><div class='stat-label'>Attendance records today</div></div><div class='stat'><div class='stat-top'><span class='stat-icon'><i class='bi bi-journal-check'></i></span></div><div class='stat-value'>{grades_count}</div><div class='stat-label'>Grades entered by you</div></div><a class='stat text-decoration-none' href='/assessments'><div class='stat-top'><span class='stat-icon'><i class='bi bi-clipboard-data'></i></span></div><div class='stat-value'>{completion_rate}</div><div class='stat-label'>Assessment completion</div><div class='stat-note'>Average graded score: {average_assessment_grade}</div></a></section><section class='panel'><h2>Quick Actions</h2><div class='row g-3 mt-1'><div class='col-md-3'><a class='quick-link' href='/attendance'><i class='bi bi-calendar-check'></i><strong>Attendance</strong></a></div><div class='col-md-3'><a class='quick-link' href='/students'><i class='bi bi-people'></i><strong>Students</strong></a></div><div class='col-md-3'><a class='quick-link' href='/grades'><i class='bi bi-journal-text'></i><strong>Report Cards</strong></a></div><div class='col-md-3'><a class='quick-link' href='/timetable'><i class='bi bi-calendar3'></i><strong>Timetable</strong></a></div><div class='col-md-3'><a class='quick-link' href='/assessments'><i class='bi bi-clipboard-data'></i><strong>Assessment Analytics</strong></a></div></div></section>"""
        return page("Teacher Dashboard",body,"teacher")
    if role == "accountant":
        tx_count=FeeTransaction.query.count(); total_paid=sum((t.amount or 0) for t in FeeTransaction.query.all())
        return page("Accountant Dashboard",f"""<div class='dashboard-heading'><div><span class='pill'>FINANCE</span><h1 class='mt-2'>Fee <span class='accent'>Management</span></h1><p>Manage payments and review financial transactions saved in the system.</p></div></div><section class='grid mb-4'><div class='stat'><div class='stat-top'><span class='stat-icon'><i class='bi bi-receipt'></i></span></div><div class='stat-value'>{tx_count}</div><div class='stat-label'>Fee Transactions</div></div><div class='stat'><div class='stat-top'><span class='stat-icon'><i class='bi bi-cash-stack'></i></span></div><div class='stat-value'>{total_paid:,.0f}</div><div class='stat-label'>Total Amount Recorded (RWF)</div></div></section><section class='panel'><a class='btn btn-accent' href='/fees'><i class='bi bi-cash-stack me-1'></i> Open Fee Records</a></section>""","accountant")
    if role == "librarian":
        books=Book.query.count(); available=sum((b.available or 0) for b in Book.query.all())
        return page("Librarian Dashboard",f"""<div class='dashboard-heading'><div><span class='pill'>LIBRARY</span><h1 class='mt-2'>Library <span class='accent'>Inventory</span></h1><p>Manage books, categories, quantities and availability.</p></div></div><section class='grid mb-4'><div class='stat'><div class='stat-top'><span class='stat-icon'><i class='bi bi-book'></i></span></div><div class='stat-value'>{books}</div><div class='stat-label'>Book Titles</div></div><div class='stat'><div class='stat-top'><span class='stat-icon'><i class='bi bi-bookmark-check'></i></span></div><div class='stat-value'>{available}</div><div class='stat-label'>Available Copies</div></div></section><section class='panel'><a class='btn btn-accent' href='/library'><i class='bi bi-book me-1'></i> Open Library Catalogue</a></section>""","librarian")
    if role == "discipline":
        cases=DisciplineCase.query.count(); students=User.query.filter_by(role="student").count()
        return page("Discipline Dashboard",f"""<div class='dashboard-heading'><div><span class='pill'>DISCIPLINE</span><h1 class='mt-2'>Discipline <span class='accent'>Records</span></h1><p>Record student conduct cases and review attendance.</p></div></div><section class='grid mb-4'><div class='stat'><div class='stat-top'><span class='stat-icon'><i class='bi bi-shield-check'></i></span></div><div class='stat-value'>{cases}</div><div class='stat-label'>Recorded Cases</div></div><div class='stat'><div class='stat-top'><span class='stat-icon'><i class='bi bi-people'></i></span></div><div class='stat-value'>{students}</div><div class='stat-label'>Students</div></div></section><section class='panel'><div class='d-flex gap-2 flex-wrap'><a class='btn btn-accent' href='/discipline'>Open Discipline Records</a><a class='btn btn-outline-success' href='/attendance'>View Attendance Register</a></div></section>""","discipline")
    if role == "auditor":
        return page(
            "Messaging Audit Workspace",
            "<section class='panel'><h1>Messaging <span class='accent'>Audit Workspace</span></h1><p class='muted'>Read-only access to the signed communication and moderation trail. This role cannot send messages, inspect private conversations, or issue school instructions.</p><a class='btn btn-accent' href='/messages?view=audit'>Open Messaging Audit Trail</a></section>",
            "auditor",
        )
    if role == "parent":
        children=linked_children(current_user.id); rows="".join(f"<tr><td><strong>{s.full_name}</strong></td><td>{s.class_name or '-'}</td><td>{s.level or '-'}</td><td>{s.trade or '-'}</td><td><div class='d-flex align-items-center gap-2 flex-nowrap'><a class='btn btn-sm btn-accent text-nowrap' href='/student/profile/{s.id}'>Student Profile</a><a class='btn btn-sm btn-outline-success text-nowrap' href='/parent/child/{s.id}'>View Details</a><a class='btn btn-sm btn-outline-success text-nowrap' href='/grades'>Academics</a></div></td></tr>" for s in children)
        return page("Parent Dashboard",f"""<div class='dashboard-heading'><div><span class='pill'>FAMILY PORTAL</span><h1 class='mt-2'>Family <span class='accent'>Overview</span></h1><p>Only students explicitly linked to your account are displayed here.</p></div></div><section class='panel'><div class='stat mb-4'><div class='stat-value'>{len(children)}</div><div class='stat-label'>Linked Students</div></div><div class='table-responsive'><table class='table align-middle'><thead><tr><th>Student</th><th>Class</th><th>Level</th><th>Trade</th><th>Action</th></tr></thead><tbody>{rows or '<tr><td colspan="5" class="muted">No student has been linked to your account yet.</td></tr>'}</tbody></table></div><a class='btn btn-accent mt-2' href='/timetable'><i class='bi bi-calendar3 me-1'></i> Children’s Timetables</a> <a class='btn btn-outline-success mt-2' href='/day-schedule'><i class='bi bi-calendar-event me-1'></i> Day Schedule</a> <a class='btn btn-outline-success mt-2' href='/messages'><i class='bi bi-chat-left-text me-1'></i> School Messages</a></section>""","parent")
    if role in {"secretary", "patron", "matron"}:
        titles = {"secretary": "School Office", "patron": "Patron Workspace", "matron": "Matron Workspace"}
        if role == "secretary":
            today_date = datetime.date.today()
            today = today_date.isoformat()
            student_count = User.query.filter_by(role="student").count()
            attendance_count = Attendance.query.filter_by(day=today).count()
            late_count = Attendance.query.filter_by(day=today, status="Late").count()
            pending_admissions = AdmissionApplicant.query.filter(AdmissionApplicant.status.in_({"Submitted", "Under Review"})).count()
            upcoming_appointments = Appointment.query.filter(Appointment.scheduled_date >= today, Appointment.status.in_({"Scheduled", "Rescheduled"})).count()
            unread_alerts = UserNotification.query.filter_by(user_id=current_user.id, is_read=False).count()
            trend_labels = []
            attendance_trend = []
            student_activity_trend = []
            for days_ago in range(6, -1, -1):
                date = today_date - datetime.timedelta(days=days_ago)
                date_label = date.strftime("%d %b")
                trend_labels.append(date_label)
                attendance_trend.append(Attendance.query.filter_by(day=date.isoformat()).count())
                student_activity_trend.append(ActivityLog.query.filter(
                    ActivityLog.created_at >= datetime.datetime.combine(date, datetime.time.min),
                    ActivityLog.created_at < datetime.datetime.combine(date + datetime.timedelta(days=1), datetime.time.min),
                    ActivityLog.action.in_({"Student registered", "Admission converted", "Student removed"}),
                ).count())
            recent_activity = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(6).all()
            activity_rows = "".join(
                f"<tr><td>{escape(item.created_at.strftime('%d %b %Y, %H:%M'))}</td><td>{escape(item.action)}</td><td>{escape(item.student_name)}</td></tr>"
                for item in recent_activity
            )
            admission_status_counts = db.session.query(
                AdmissionApplicant.status, db.func.count(AdmissionApplicant.id)
            ).group_by(AdmissionApplicant.status).order_by(AdmissionApplicant.status).all()
            admission_status_rows = "".join(
                f"<div class='admission-status-row'><span><i></i>{escape(status)}</span><strong>{count}</strong></div>"
                for status, count in admission_status_counts
            ) or "<p class='muted mb-0'>No admission applications have been received.</p>"
            body = f"""<section class='panel hero-card'><span class='pill'>SECRETARY WORKSPACE</span><h1 class='mt-2'>Welcome back, {escape(current_user.full_name)}</h1><p class='muted'>Your school office overview for {today}.</p></section><section class='grid mb-4'><a class='stat text-decoration-none' href='/students'><div class='stat-value'>{student_count}</div><div class='stat-label'>Registered Students</div></a><a class='stat text-decoration-none' href='/attendance'><div class='stat-value'>{attendance_count}</div><div class='stat-label'>Attendance Records Today</div></a><a class='stat text-decoration-none' href='/attendance'><div class='stat-value'>{late_count}</div><div class='stat-label'>Late Arrivals Today</div></a><a class='stat text-decoration-none' href='/admissions'><div class='stat-value'>{pending_admissions}</div><div class='stat-label'>Admissions To Review</div></a><a class='stat text-decoration-none' href='/appointments'><div class='stat-value'>{upcoming_appointments}</div><div class='stat-label'>Upcoming Appointments</div></a><a class='stat text-decoration-none' href='/notifications'><div class='stat-value'>{unread_alerts}</div><div class='stat-label'>Unread Alerts</div></a></section><div class='row g-4 mb-4'><div class='col-xl-7'><section class='panel chart-card'><div class='dashboard-heading mb-2'><div><h2>Office Activity</h2><p>Daily attendance records and student additions/removals over the last 7 days.</p></div><span class='pill'>LIVE DATA</span></div><div class='chart-wrap'><canvas id='secretaryActivityChart'></canvas></div></section></div><div class='col-xl-5'><section class='panel chart-card'><div class='dashboard-heading mb-2'><div><h2>Admission Status</h2><p>Applicants by their current status.</p></div></div><div class='chart-wrap'><canvas id='secretaryAdmissionsChart'></canvas></div></section></div></div><section class='panel'><div class='dashboard-heading'><div><h2>Recent Student Activity</h2><p>Recorded registrations, removals, and student changes.</p></div><a class='btn btn-sm btn-outline-success' href='/activity-history'>View history</a></div><div class='table-responsive'><table class='table align-middle'><thead><tr><th>Date</th><th>Activity</th><th>Student</th></tr></thead><tbody>{activity_rows or "<tr><td colspan='3' class='muted'>No student activity has been recorded yet.</td></tr>"}</tbody></table></div><p class='small muted mb-0'>Charts show records currently stored and activity explicitly logged by the system.</p></section><script>const chartText=getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();new Chart(document.getElementById('secretaryActivityChart'),{{type:'line',data:{{labels:{json.dumps(trend_labels)},datasets:[{{label:'Attendance records',data:{json.dumps(attendance_trend)},borderColor:'#0b9b70',backgroundColor:'rgba(11,155,112,.12)',fill:true,tension:.38}},{{label:'Student additions/removals',data:{json.dumps(student_activity_trend)},borderColor:'#d9ad2b',backgroundColor:'rgba(217,173,43,.12)',fill:true,tension:.38}}]}},options:{{responsive:true,maintainAspectRatio:false,scales:{{y:{{beginAtZero:true,ticks:{{precision:0,color:chartText}}}},x:{{ticks:{{color:chartText}}}}}}}}}});new Chart(document.getElementById('secretaryAdmissionsChart'),{{type:'doughnut',data:{{labels:{json.dumps([status for status, in db.session.query(AdmissionApplicant.status).distinct().all()])},datasets:[{{data:{json.dumps([AdmissionApplicant.query.filter_by(status=status).count() for status, in db.session.query(AdmissionApplicant.status).distinct().all()])},backgroundColor:['#0b9b70','#39bd72','#d9ad2b','#8cc63e','#7a8b85','#4f6f64'],borderWidth:0}}]}},options:{{responsive:true,maintainAspectRatio:false,cutout:'65%',plugins:{{legend:{{position:'bottom',labels:{{color:chartText,usePointStyle:true}}}}}}}}}});</script>"""
            body += """<script>(()=>{const canvas=document.getElementById('secretaryActivityChart');if(!canvas)return;const chartCard=canvas.closest('.chart-card');const badge=chartCard.querySelector('.pill');const status=document.createElement('p');status.className='small muted mb-0 mt-2';canvas.parentElement.after(status);async function refreshActivity(){try{const response=await fetch('/dashboard/secretary-analytics',{headers:{'Accept':'application/json'}});if(!response.ok)throw new Error('Analytics refresh failed');const data=await response.json();const chart=window.Chart&&Chart.getChart(canvas);if(!chart){status.textContent='Chart library unavailable; reload the page to try again.';return}chart.data.labels=data.labels;chart.data.datasets[0].data=data.attendance;chart.data.datasets[1].data=data.student_activity;chart.update('none');status.textContent='Updated at '+data.updated_at;badge.textContent='LIVE · 30 SEC REFRESH'}catch(error){status.textContent='Live refresh unavailable; showing the last loaded data.'}}refreshActivity();window.setInterval(refreshActivity,30000)})();</script>"""
            body += """<script>(()=>{const canvas=document.getElementById('secretaryActivityChart');if(!canvas||window.Chart&&Chart.getChart(canvas))return;const wrap=canvas.parentElement;const status=wrap.nextElementSibling;const namespace='http://www.w3.org/2000/svg';let svg;function node(name,attributes={},text=''){const item=document.createElementNS(namespace,name);Object.entries(attributes).forEach(([key,value])=>item.setAttribute(key,value));if(text)item.textContent=text;return item}function draw(data){canvas.hidden=true;if(!svg){svg=node('svg',{id:'secretaryActivityFallback',viewBox:'0 0 640 250',role:'img','aria-label':'Attendance and student activity during the last seven days'});svg.style.width='100%';svg.style.height='100%';wrap.appendChild(svg)}svg.replaceChildren();const left=48,right=620,top=20,bottom=196,maximum=Math.max(1,...data.attendance,...data.student_activity);for(let index=0;index<5;index++){const y=top+(bottom-top)*index/4;svg.appendChild(node('line',{x1:left,y1:y,x2:right,y2:y,stroke:'currentColor','stroke-opacity':'.12'}));svg.appendChild(node('text',{x:left-10,y:y+4,'text-anchor':'end',fill:'currentColor','font-size':'11'},String(Math.ceil(maximum*(4-index)/4))))}[data.attendance,data.student_activity].forEach((values,series)=>{const points=values.map((value,index)=>({x:left+(right-left)*(index/Math.max(1,values.length-1)),y:bottom-(bottom-top)*value/maximum}));svg.appendChild(node('polyline',{points:points.map(point=>`${point.x},${point.y}`).join(' '),fill:'none',stroke:series?'#d9ad2b':'#0b9b70','stroke-width':'3','stroke-linecap':'round','stroke-linejoin':'round'}));points.forEach(point=>svg.appendChild(node('circle',{cx:point.x,cy:point.y,r:4,fill:series?'#d9ad2b':'#0b9b70'})))});data.labels.forEach((label,index)=>{const x=left+(right-left)*(index/Math.max(1,data.labels.length-1));svg.appendChild(node('text',{x,y:bottom+22,'text-anchor':'middle',fill:'currentColor','font-size':'10'},label))});[['Attendance','#0b9b70'],['Student changes','#d9ad2b']].forEach(([label,color],index)=>{const x=left+index*155;svg.appendChild(node('circle',{cx:x,cy:240,r:4,fill:color}));svg.appendChild(node('text',{x:x+9,y:244,fill:'currentColor','font-size':'11'},label))})}async function refresh(){try{const response=await fetch('/dashboard/secretary-analytics',{headers:{Accept:'application/json'}});if(!response.ok)throw new Error();draw(await response.json());if(status)status.textContent='Updated just now · refreshes every 30 seconds'}catch(error){if(status)status.textContent='Could not refresh analytics. Showing latest available data.'}}refresh();window.setInterval(refresh,30000)})();</script>"""
            body = body.replace("<canvas id='secretaryActivityChart'></canvas>", _office_activity_svg(trend_labels, attendance_trend, student_activity_trend))
            body = body.replace("<div class='chart-wrap'><canvas id='secretaryAdmissionsChart'></canvas></div>", f"<div class='admission-status-list'>{admission_status_rows}</div>")
            body = body.replace("<script>const chartText=", "<script>if(document.getElementById('attendanceChart')){const chartText=").replace("</script>", "}</script>")
            body = body.replace("class='panel hero-card'", "class='panel office-welcome'")
            body = body.replace("class='stat text-decoration-none'", "class='stat office-stat text-decoration-none'")
            body = body.replace("SECRETARY WORKSPACE", "SCHOOL OFFICE")
            body = body.replace("Welcome back,", "Good morning,")
            body = "<style>.office-welcome{background:linear-gradient(115deg,#e5f5ed,#e8f3fb 72%,#f6f3e8);border:1px solid #dcece4;color:#26333a;padding:18px 22px;margin-bottom:14px}.office-welcome h1{color:#26333a;font-size:clamp(1.25rem,1.8vw,1.55rem);margin:.45rem 0}.office-welcome p{font-size:.9rem;margin:0}.office-welcome .muted{color:#718087!important}.office-welcome .accent{color:#0b9b70}.office-welcome .pill{background:#fff;border-color:#d8ebe0;color:#0b8061;padding:4px 9px;font-size:.62rem}.office-stat{border:0;min-height:94px;padding:14px 16px;display:flex;flex-direction:column;justify-content:center;box-shadow:0 4px 14px rgba(28,55,47,.045)}.office-stat:nth-child(6n+1){background:#e7f4ec}.office-stat:nth-child(6n+2){background:#e8f1fb}.office-stat:nth-child(6n+3){background:#fff2df}.office-stat:nth-child(6n+4){background:#f2edfb}.office-stat:nth-child(6n+5){background:#e4f5f3}.office-stat:nth-child(6n){background:#fff0ed}.office-stat .stat-value{font-size:1.55rem;color:#26333a}.office-stat .stat-label{font-size:.75rem;font-weight:650;color:#607078;margin-top:5px}.office-dashboard-panel{border:1px solid #e7ecea;box-shadow:0 7px 24px rgba(28,55,47,.045)}.admission-status-list{display:grid;gap:10px;padding:14px 4px}.admission-status-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 12px;border-radius:12px;background:var(--page)}.admission-status-row span{display:flex;align-items:center;gap:10px}.admission-status-row i{width:10px;height:10px;border-radius:50%;background:#0b9b70}.admission-status-row:nth-child(3n+2) i{background:#d9ad2b}.admission-status-row:nth-child(3n) i{background:#5795d2}.office-welcome+.grid{margin-top:0}@media(max-width:600px){.office-welcome{padding:15px 16px}.office-stat{min-height:78px;padding:11px 13px}.office-stat .stat-value{font-size:1.35rem}}</style>" + body
            body += """<script>(()=>{const svg=document.getElementById('secretaryActivityChartSvg');if(!svg)return;const status=document.createElement('p');status.className='small muted mb-0 mt-2';svg.parentElement.after(status);function update(data){const left=48,right=620,top=20,bottom=196,maximum=Math.max(1,...data.attendance,...data.student_activity);[['attendance',data.attendance],['student-changes',data.student_activity]].forEach(([name,values])=>{const points=values.map((value,index)=>({x:left+(right-left)*index/Math.max(1,values.length-1),y:bottom-(bottom-top)*value/maximum}));svg.querySelector('#series-'+name).setAttribute('points',points.map(point=>point.x+','+point.y).join(' '));const group=svg.querySelector('#points-'+name);group.replaceChildren(...points.map(point=>{const circle=document.createElementNS('http://www.w3.org/2000/svg','circle');circle.setAttribute('cx',point.x);circle.setAttribute('cy',point.y);circle.setAttribute('r','4');circle.setAttribute('fill',name==='attendance'?'#0b9b70':'#d9ad2b');return circle}))});svg.querySelectorAll('.activity-label').forEach((label,index)=>{if(data.labels[index])label.textContent=data.labels[index]})}async function refresh(){try{const response=await fetch('/dashboard/secretary-analytics',{headers:{Accept:'application/json'}});if(!response.ok)throw new Error();update(await response.json());status.textContent='Live · updated just now · refreshes every 30 seconds'}catch(error){status.textContent='Could not refresh analytics; chart shows the latest loaded data.'}}refresh();window.setInterval(refresh,30000)})();</script>"""
            return page(titles[role], body, role)
        admissions_link = "<a class='btn btn-outline-success' href='/admissions'>Admissions</a>" if role == "secretary" else ""
        documents_link = "<a class='btn btn-outline-success' href='/documents'>Documents</a>" if role == "secretary" else ""
        parent_contacts_link = "<a class='btn btn-outline-success' href='/parents'>Parents / Guardians</a>" if role == "secretary" else ""
        appointments_link = "<a class='btn btn-outline-success' href='/appointments'>Appointments</a>" if role == "secretary" else ""
        announcements_link = "<a class='btn btn-outline-success' href='/announcements'>Announcements</a>" if role == "secretary" else ""
        correspondence_link = "<a class='btn btn-outline-success' href='/correspondence'>Correspondence</a>" if role == "secretary" else ""
        letters_link = "<a class='btn btn-outline-success' href='/letters-certificates'>Letters & Certificates</a>" if role == "secretary" else ""
        lost_card_link = "<a class='btn btn-outline-danger' href='/rfid/report-lost'>Report Lost Card</a>" if role == "secretary" else ""
        reports_link = "<a class='btn btn-outline-success' href='/reports'>Reports</a>" if role == "secretary" else ""
        activity_history_link = "<a class='btn btn-outline-success' href='/activity-history'>Activity History</a>" if role == "secretary" else ""
        return page(titles[role], f"<section class='panel'><h1>{titles[role]}</h1><p>Welcome, {current_user.full_name}. Use the modules available to your role.</p><div class='d-flex gap-2 flex-wrap'><a class='btn btn-accent' href='/students'>Students</a>{admissions_link}{documents_link}{parent_contacts_link}{appointments_link}{announcements_link}{correspondence_link}{letters_link}{lost_card_link}{reports_link}{activity_history_link}<a class='btn btn-outline-success' href='/attendance'>Attendance</a><a class='btn btn-outline-success' href='/day-schedule'>Day Schedule</a><a class='btn btn-outline-success' href='/timetable'>Academic Timetable</a><a class='btn btn-outline-success' href='/messages'>Messages</a></div></section>", role)
    records=attendance_for_student(current_user.id); rows="".join(f"<tr><td>{a.day}</td><td><span class='pill'>{a.status}</span></td><td>{a.arrival_time or '-'}</td><td>{a.afternoon_time or '-'}</td></tr>" for a in records)
    return page("Student Dashboard",f"""<div class='dashboard-heading'><div><span class='pill'>STUDENT PORTAL</span><h1 class='mt-2'>My <span class='accent'>Attendance</span></h1><p>Read-only attendance records for your account.</p></div><div class='d-flex gap-2 flex-wrap'><a class='btn btn-accent' href='/student/profile'>My Student Profile</a><a class='btn btn-outline-success' href='/timetable'>My Timetable</a><a class='btn btn-outline-success' href='/day-schedule'>Day Schedule</a><a class='btn btn-outline-success' href='/grades'>My Academic Report</a></div></div><section class='panel'><div class='table-responsive'><table class='table'><thead><tr><th>Date</th><th>Status</th><th>Morning Time</th><th>Afternoon Time</th></tr></thead><tbody>{rows or '<tr><td colspan="4" class="muted">No attendance records have been saved yet.</td></tr>'}</tbody></table></div></section>""","student")


def secretary_analytics():
    if current_user.role != "secretary":
        abort(403)
    today = datetime.date.today()
    labels = []
    attendance = []
    student_activity = []
    for days_ago in range(6, -1, -1):
        date = today - datetime.timedelta(days=days_ago)
        labels.append(date.strftime("%d %b"))
        attendance.append(Attendance.query.filter_by(day=date.isoformat()).count())
        start = datetime.datetime.combine(date, datetime.time.min)
        end = start + datetime.timedelta(days=1)
        student_activity.append(ActivityLog.query.filter(
            ActivityLog.created_at >= start,
            ActivityLog.created_at < end,
            ActivityLog.action.in_({"Student registered", "Admission converted", "Student removed"}),
        ).count())
    return jsonify({"labels": labels, "attendance": attendance, "student_activity": student_activity,
                    "updated_at": datetime.datetime.now().strftime("%H:%M:%S")})

def parent_child(student_id):
    student = db.session.get(User, student_id)
    if not student or student.role != "student" or student.parent_id != current_user.id:
        abort(403)
    records = attendance_for_student(student.id)
    txs = FeeTransaction.query.filter_by(student_id=student.id).all()
    total_paid = sum(t.amount for t in txs)
    fee_requirements = FeeRequirement.query.filter_by(student_id=student.id).order_by(FeeRequirement.due_date).all()
    total_required = sum(requirement.amount for requirement in fee_requirements)
    balance_remaining = max(0.0, total_required - total_paid)
    book_loans = BookLoan.query.filter_by(student_id=student.id).order_by(BookLoan.borrowed_date.desc(), BookLoan.id.desc()).all()
    discipline_cases = DisciplineCase.query.filter_by(student_id=student.id, is_published=True).order_by(DisciplineCase.id.desc()).all()

    att_rows = "".join(f"<tr><td>{a.day}</td><td><span class='pill'>{a.status}</span></td><td>{a.arrival_time or '-'}</td><td>{a.afternoon_time or '-'}</td><td>{a.source or 'Manual'}</td></tr>" for a in records)
    attendance_counts = {status: sum(1 for record in records if record.status == status) for status in ("Present", "Absent", "Late")}
    attendance_summary = "".join(f"<div class='stat'><div class='stat-value'>{attendance_counts[status]}</div><div class='stat-label'>{status} days</div></div>" for status in ("Present", "Absent", "Late"))
    tx_rows = "".join(f"<tr><td>{t.created_at}</td><td>{t.amount:,.2f} RWF</td><td>{t.method}</td><td>{t.reference or '-'}</td></tr>" for t in txs)
    fee_rows = "".join(f"<tr><td>{requirement.title}</td><td>{requirement.amount:,.2f} RWF</td><td>{requirement.due_date.isoformat()}</td><td>{requirement.details or '-'}</td></tr>" for requirement in fee_requirements)
    book_loan_rows = "".join(f"<tr><td>{loan.book.title if loan.book else '-'}</td><td>{loan.borrowed_date.isoformat()}</td><td>{loan.due_date.isoformat()}</td><td>{loan.returned_date.isoformat() if loan.returned_date else '-'}</td><td>{'Returned' if loan.returned_date else 'Overdue' if loan.due_date < datetime.date.today() else 'Borrowed'}</td></tr>" for loan in book_loans)
    discipline_rows = "".join(f"<tr><td>{escape(case.created_at)}</td><td>{escape(case.record_type or 'Concern')}</td><td>{escape(case.details)}</td><td>{escape(case.action_taken or '-')}</td><td>{escape(case.remarks or '-')}</td></tr>" for case in discipline_cases)
    timetable_entries = TimetableEntry.query.filter_by(class_name=student.class_name).order_by(TimetableEntry.day_of_week, TimetableEntry.time_slot).all() if student.class_name else []
    timetable_rows = "".join(f"<tr><td>{entry.day_of_week}</td><td>{entry.time_slot or '-'}</td><td>{entry.subject}</td><td>{entry.teacher_name or '-'}</td></tr>" for entry in timetable_entries)
    day_schedule_query = DayScheduleEntry.query.filter(DayScheduleEntry.is_published.is_(True), DayScheduleEntry.schedule_date >= datetime.date.today())
    day_schedule_query = day_schedule_query.filter(db.or_(DayScheduleEntry.class_name.is_(None), DayScheduleEntry.class_name == student.class_name))
    day_schedule_entries = day_schedule_query.order_by(DayScheduleEntry.schedule_date, DayScheduleEntry.start_time).all()
    day_schedule_rows = "".join(f"<tr><td>{entry.schedule_date.isoformat()}</td><td>{entry.start_time or '-'}{f' - {entry.end_time}' if entry.end_time else ''}</td><td>{entry.activity}</td><td>{entry.details or '-'}</td></tr>" for entry in day_schedule_entries)

    body = f"""<section class="panel"><h1>Child Overview: <span class="accent">{student.full_name}</span></h1><p class="muted">Class: {student.class_name or '-'} | Level: {student.level or '-'} | Trade: {student.trade or '-'}</p><div class="grid my-4"><div class="stat"><strong>{total_required:,.2f} RWF</strong><div>Total Fees Required</div></div><div class="stat"><strong>{total_paid:,.2f} RWF</strong><div>Total Fees Paid</div></div><div class="stat"><strong>{balance_remaining:,.2f} RWF</strong><div>Outstanding Balance</div></div></div><h3 class="mt-4">Fee Requirements & Deadlines</h3><div class="table-responsive"><table class="table"><tr><th>Requirement</th><th>Amount</th><th>Deadline</th><th>Details</th></tr>{fee_rows or '<tr><td colspan="4" class="muted">No fee requirements have been entered.</td></tr>'}</table></div><h3 class="mt-4">Library Loans</h3><div class="table-responsive"><table class="table"><tr><th>Book</th><th>Borrowed</th><th>Due Date</th><th>Returned</th><th>Status</th></tr>{book_loan_rows or '<tr><td colspan="5" class="muted">No book loans are recorded.</td></tr>'}</table></div><h3 class="mt-4">Published Behavior Reports</h3><div class="table-responsive"><table class="table"><tr><th>Date</th><th>Type</th><th>Details</th><th>Action Taken</th><th>Remarks</th></tr>{discipline_rows or '<tr><td colspan="5" class="muted">No published behavior reports are available.</td></tr>'}</table></div><h3 class="mt-4">Academic Timetable</h3><p class="muted">The class schedule for this child.</p><div class="table-responsive"><table class="table"><tr><th>Day</th><th>Time Slot</th><th>Subject</th><th>Teacher</th></tr>{timetable_rows or '<tr><td colspan="4" class="muted">No timetable has been published for this class yet.</td></tr>'}</table></div><h3 class="mt-4">Published Day Schedule</h3><p class="muted">Daily program and activities for this child.</p><div class="table-responsive"><table class="table"><tr><th>Date</th><th>Time</th><th>Activity</th><th>Details</th></tr>{day_schedule_rows or '<tr><td colspan="4" class="muted">No published activities are available.</td></tr>'}</table></div><h3 class="mt-4">Attendance Summary</h3><div class="grid mb-4">{attendance_summary}</div><h3 class="mt-4">Attendance History & RFID / Scan Times</h3><p class="muted">Daily status, school arrival time, and afternoon attendance for this student.</p><div class="table-responsive"><table class="table"><tr><th>Date</th><th>Attendance</th><th>Arrival Time</th><th>Afternoon Attendance</th><th>Source</th></tr>{att_rows or '<tr><td colspan="5" class="muted">No attendance history is available for this student.</td></tr>'}</table></div><h3 class="mt-4">Payment History</h3><table class="table"><tr><th>Date</th><th>Amount</th><th>Method</th><th>Reference</th></tr>{tx_rows or '<tr><td colspan="4" class="muted">No payments have been recorded.</td></tr>'}</table><a class="btn btn-outline-success mt-3" href="/dashboard" style="border-color:var(--accent);color:var(--text)">Back to Dashboard</a></section>"""
    published_grades = Grade.query.filter_by(student_id=student.id, is_published=True).order_by(Grade.academic_year.desc(), Grade.term, Grade.subject).all()
    grade_rows = "".join(f"<tr><td>{escape(grade.academic_year or '-')}</td><td>{escape(grade.term or '-')}</td><td>{escape(grade.subject)}</td><td>{grade.marks:g} / {grade.max_marks:g}</td><td>{escape(grade.remarks or '-')}</td></tr>" for grade in published_grades)
    assessment_submissions = AssessmentSubmission.query.filter_by(student_id=student.id).order_by(AssessmentSubmission.submitted_at.desc()).all()
    assessment_submissions = [submission for submission in assessment_submissions if submission.assessment.is_published]
    assessment_rows = "".join(f"<tr><td>{escape(submission.assessment.title)}</td><td>{escape(submission.assessment.subject)}</td><td>{escape(submission.submitted_at)}</td><td>{f'{submission.score:g} / {submission.assessment.max_score:g}' if submission.is_graded and submission.score is not None else 'Awaiting teacher score'}</td><td>{escape(submission.feedback or '-')}</td></tr>" for submission in assessment_submissions)
    no_grade_rows = '<tr><td colspan="5" class="muted">No published academic results are available.</td></tr>'
    no_assessment_rows = '<tr><td colspan="5" class="muted">No assessment submissions are available.</td></tr>'
    academic_html = f"<h3 class='mt-4'>Published Academic Results</h3><div class='table-responsive'><table class='table'><tr><th>Year</th><th>Term</th><th>Lesson</th><th>Marks</th><th>Teacher Remarks</th></tr>{grade_rows or no_grade_rows}</table></div>"
    assessment_html = f"<h3 class='mt-4'>e-Learning Assessment Results</h3><div class='table-responsive'><table class='table'><tr><th>Assessment</th><th>Subject</th><th>Submitted</th><th>Score</th><th>Feedback</th></tr>{assessment_rows or no_assessment_rows}</table></div><a class='btn btn-outline-success mb-3' href='/assessments'>Open Assessments</a>"
    body = body.replace('<h3 class="mt-4">Academic Timetable</h3>', academic_html + assessment_html + '<h3 class="mt-4">Academic Timetable</h3>')
    return page("Child Details", body, "parent")

def register_routes(app):
    app.add_url_rule('/dashboard', endpoint='dashboard', view_func=permission_for('dashboard')(dashboard), methods=['GET'])
    app.add_url_rule('/dashboard/secretary-analytics', endpoint='secretary_analytics', view_func=permission_for('dashboard')(secretary_analytics), methods=['GET'])
    app.add_url_rule('/parent/child/<int:student_id>', endpoint='parent_child', view_func=permission_for('parent_child')(parent_child), methods=['GET'])

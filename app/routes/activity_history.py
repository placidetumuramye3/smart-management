from flask import request
from markupsafe import escape

from app.models import ActivityLog
from app.utils.decorators import permission_for
from app.utils.rendering import page


def activity_history():
    search = request.args.get("q", "").strip()
    query = ActivityLog.query
    if search:
        pattern = f"%{search}%"
        query = query.filter(ActivityLog.student_name.ilike(pattern))
    records = query.order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc()).limit(500).all()
    rows = "".join(
        f"<tr><td>{escape(record.created_at.strftime('%Y-%m-%d %H:%M'))}</td><td>{escape(record.actor_name)}</td>"
        f"<td>{escape(record.student_name)}</td><td>{escape(record.action)}</td><td>{escape(record.details)}</td></tr>"
        for record in records
    )
    body = f"""<style>.activity-search-form{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;margin:20px 0}}.activity-search-form .form-control{{min-width:0}}.activity-search-form button{{min-width:94px;white-space:nowrap}}.activity-history-table{{min-width:760px}}.activity-history-table th{{white-space:nowrap}}.activity-history-table td:last-child{{min-width:220px;white-space:normal;overflow-wrap:anywhere}}@media(max-width:420px){{.activity-search-form{{grid-template-columns:minmax(0,1fr) auto;gap:7px}}.activity-search-form button{{min-width:82px;padding-inline:10px}}}}</style><section class='panel'><h1>Activity <span class='accent'>History</span></h1><p class='muted'>Student profile and RFID card changes recorded by the system.</p><form method='get' class='activity-search-form'><input class='form-control' name='q' value='{escape(search)}' placeholder='Search student name'><button class='btn btn-outline-success' type='submit'><i class='bi bi-search me-1'></i>Search</button></form><div class='table-responsive'><table class='table align-middle activity-history-table'><thead><tr><th>Time</th><th>Changed by</th><th>Student</th><th>Activity</th><th>Details</th></tr></thead><tbody>{rows or '<tr><td colspan="5" class="muted">No student or card activity recorded yet.</td></tr>'}</tbody></table></div></section>"""
    return page("Activity History", body, "master_admin")


def register_routes(app):
    app.add_url_rule("/activity-history", endpoint="activity_history", view_func=permission_for("activity_history")(activity_history), methods=["GET"])

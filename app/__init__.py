import os
from flask import Flask
from sqlalchemy import inspect, text
from config import Config
from app.extensions import db, login_manager
from migrations.versions.v20261022_01_add_school_message_type import upgrade as upgrade_school_message_type
from migrations.versions.v20261022_02_add_messaging_conversations import upgrade as upgrade_messaging_conversations
from migrations.versions.v20261022_03_add_official_notice_workflow import upgrade as upgrade_official_notice_workflow
from migrations.versions.v20261023_01_expand_parent_school_meetings import upgrade as upgrade_parent_school_meetings
from migrations.versions.v20261023_02_add_meeting_workflows import upgrade as upgrade_meeting_workflows
from migrations.versions.v20261023_04_add_room_reservations import upgrade as upgrade_room_reservations
from migrations.versions.v20261023_05_add_meeting_room_checkin import upgrade as upgrade_meeting_room_checkin
from migrations.versions.v20261023_06_add_microsoft_meeting_sync import upgrade as upgrade_microsoft_meeting_sync
from migrations.versions.v20261023_07_add_panel_discussion_format import upgrade as upgrade_panel_discussion_format
from migrations.versions.v20261024_01_add_department_and_class_section import upgrade as upgrade_department_and_class_section
from migrations.versions.v20261024_02_fix_official_notice_boolean_defaults import upgrade as upgrade_official_notice_boolean_defaults

def create_app(config_class=Config):
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.from_object(config_class)
    app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "images")
    app.config["PRIVATE_UPLOAD_FOLDER"] = os.path.join(app.instance_path, "private_uploads")
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "login"

    from app.models import (User, GradingRubric, GradingRubricCriterion, AssessmentAttempt,
                            AdmissionApplicant, StudentDocument, Appointment, Announcement,
                            Correspondence, ActivityLog, Setting, SchoolMessage, UserNotification,
                            MessageConversation, MessageParticipant, MessageReport,
                            MessagePermission, MessageAuditEvent, OfficialNoticePermission,
                            OfficialNotice, OfficialNoticeAuditEvent, StudentGuardian,
                            Meeting, MeetingParticipant, MeetingAttendance, MeetingAgendaItem,
                            MeetingMinutes, MeetingMinutesRevision, MeetingActionItem,
                            MeetingActionComment, MeetingAuditEvent, MeetingNotificationLog,
                            RoomReservation)
    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from app.routes import register_all_routes
    register_all_routes(app)

    # Keep learning activities available when the app is launched through a
    # WSGI server or Flask CLI, not only through run.py. Older databases need
    # this column before SQLAlchemy can load LearningMaterial records.
    with app.app_context():
        with db.engine.begin() as connection:
            upgrade_school_message_type(connection)
            upgrade_messaging_conversations(connection)
            upgrade_official_notice_workflow(connection)
            upgrade_parent_school_meetings(connection)
            upgrade_meeting_workflows(connection)
            upgrade_room_reservations(connection)
            upgrade_meeting_room_checkin(connection)
            upgrade_microsoft_meeting_sync(connection)
            upgrade_panel_discussion_format(connection)
            upgrade_department_and_class_section(connection)
            upgrade_official_notice_boolean_defaults(connection)
        if inspect(db.engine).has_table("user"):
            db.metadata.create_all(
                bind=db.engine,
                tables=[GradingRubric.__table__, GradingRubricCriterion.__table__,
                        AdmissionApplicant.__table__, StudentDocument.__table__,
                        Appointment.__table__, Announcement.__table__, Correspondence.__table__,
                        ActivityLog.__table__, Setting.__table__, SchoolMessage.__table__, UserNotification.__table__,
                        MessageConversation.__table__, MessageParticipant.__table__,
                        MessageReport.__table__, MessagePermission.__table__,
                        MessageAuditEvent.__table__, OfficialNoticePermission.__table__,
                        OfficialNotice.__table__, OfficialNoticeAuditEvent.__table__,
                        StudentGuardian.__table__, Meeting.__table__, MeetingParticipant.__table__,
                        MeetingAttendance.__table__, MeetingAgendaItem.__table__, MeetingMinutes.__table__,
                        MeetingMinutesRevision.__table__, MeetingActionItem.__table__,
                        MeetingActionComment.__table__, MeetingAuditEvent.__table__,
                        MeetingNotificationLog.__table__,
                        RoomReservation.__table__],
            )
            user_columns = {column["name"] for column in inspect(db.engine).get_columns("user")}
            for column_name, column_type in (
                ("date_of_birth", "DATE"),
                ("gender", "VARCHAR(30)"),
                ("academic_year", "VARCHAR(9)"),
                ("guardian_name", "VARCHAR(160)"),
                ("guardian_relationship", "VARCHAR(80)"),
                ("guardian_phone", "VARCHAR(40)"),
                ("address", "VARCHAR(500)"),
                ("emergency_contact_name", "VARCHAR(160)"),
                ("emergency_contact_phone", "VARCHAR(40)"),
                ("emergency_contact_relationship", "VARCHAR(80)"),
            ):
                if column_name not in user_columns:
                    db.session.execute(text(f"ALTER TABLE user ADD COLUMN {column_name} {column_type}"))
            if any(name not in user_columns for name in (
                "date_of_birth", "gender", "academic_year", "guardian_name",
                "guardian_relationship", "guardian_phone", "address",
                "emergency_contact_name", "emergency_contact_phone", "emergency_contact_relationship"
            )):
                db.session.commit()
        if inspect(db.engine).has_table("pending_card"):
            pending_card_columns = {
                column["name"] for column in inspect(db.engine).get_columns("pending_card")
            }
            if "last_seen" not in pending_card_columns:
                db.session.execute(text("ALTER TABLE pending_card ADD COLUMN last_seen VARCHAR(26)"))
                db.session.commit()
        if inspect(db.engine).has_table("assessment"):
            db.metadata.create_all(bind=db.engine, tables=[AssessmentAttempt.__table__])
            attempt_columns = {
                column["name"] for column in inspect(db.engine).get_columns("assessment_attempt")
            }
            if "question_order_json" not in attempt_columns:
                db.session.execute(text("ALTER TABLE assessment_attempt ADD COLUMN question_order_json TEXT"))
                db.session.commit()
        if inspect(db.engine).has_table("learning_material"):
            material_columns = {
                column["name"] for column in inspect(db.engine).get_columns("learning_material")
            }
            if "activity_data" not in material_columns:
                db.session.execute(text("ALTER TABLE learning_material ADD COLUMN activity_data TEXT"))
                db.session.commit()
        if inspect(db.engine).has_table("assessment_question"):
            question_columns = {
                column["name"] for column in inspect(db.engine).get_columns("assessment_question")
            }
            if "question_type" not in question_columns:
                db.session.execute(text(
                    "ALTER TABLE assessment_question ADD COLUMN question_type VARCHAR(30) NOT NULL DEFAULT 'multiple_choice'"
                ))
            if "media_url" not in question_columns:
                db.session.execute(text("ALTER TABLE assessment_question ADD COLUMN media_url VARCHAR(1000)"))
            if db.engine.dialect.name == "postgresql":
                correct_answer_column = next(
                    column for column in inspect(db.engine).get_columns("assessment_question")
                    if column["name"] == "correct_answer"
                )
                if getattr(correct_answer_column["type"], "length", None) == 1:
                    db.session.execute(text(
                        "ALTER TABLE assessment_question ALTER COLUMN correct_answer TYPE TEXT USING correct_answer::text"
                    ))
            db.session.commit()
        if inspect(db.engine).has_table("assessment"):
            assessment_columns = {
                column["name"] for column in inspect(db.engine).get_columns("assessment")
            }
            if "media_url" not in assessment_columns:
                db.session.execute(text("ALTER TABLE assessment ADD COLUMN media_url VARCHAR(1000)"))
            if "target_student_ids" not in assessment_columns:
                db.session.execute(text("ALTER TABLE assessment ADD COLUMN target_student_ids TEXT"))
            if "rubric_id" not in assessment_columns:
                db.session.execute(text("ALTER TABLE assessment ADD COLUMN rubric_id INTEGER"))
            if "time_limit_minutes" not in assessment_columns:
                db.session.execute(text("ALTER TABLE assessment ADD COLUMN time_limit_minutes INTEGER"))
            if "randomize_questions" not in assessment_columns:
                db.session.execute(text("ALTER TABLE assessment ADD COLUMN randomize_questions BOOLEAN NOT NULL DEFAULT FALSE"))
            if "focus_mode" not in assessment_columns:
                db.session.execute(text("ALTER TABLE assessment ADD COLUMN focus_mode BOOLEAN NOT NULL DEFAULT FALSE"))
            if any(name not in assessment_columns for name in (
                "media_url", "target_student_ids", "rubric_id", "time_limit_minutes",
                "randomize_questions", "focus_mode"
            )):
                db.session.commit()
        if inspect(db.engine).has_table("assessment_submission"):
            submission_columns = {
                column["name"] for column in inspect(db.engine).get_columns("assessment_submission")
            }
            if "auto_score" not in submission_columns:
                db.session.execute(text("ALTER TABLE assessment_submission ADD COLUMN auto_score FLOAT"))
            if "rubric_results_json" not in submission_columns:
                db.session.execute(text("ALTER TABLE assessment_submission ADD COLUMN rubric_results_json TEXT"))
            if "focus_warnings" not in submission_columns:
                db.session.execute(text("ALTER TABLE assessment_submission ADD COLUMN focus_warnings INTEGER NOT NULL DEFAULT 0"))
            if any(name not in submission_columns for name in ("auto_score", "rubric_results_json", "focus_warnings")):
                db.session.commit()

    @app.errorhandler(403)
    def forbidden(_):
        from app.utils.rendering import page
        return page("Access Denied", "<section class='panel'><h1 class='accent'>Access Denied</h1><p>Your role does not have permission to access this module.</p><a class='btn btn-accent' href='/dashboard'>Return to Dashboard</a></section>", "error"), 403

    @app.cli.command("send-meeting-reminders")
    def send_meeting_reminders_command():
        """Queue deduplicated in-app reminders for meetings within 24 hours."""
        from click import echo
        from app.services.meeting_service import meeting_reminders
        echo(f"Queued {meeting_reminders()} meeting reminders.")

    @app.cli.command("process-meeting-actions")
    def process_meeting_actions_command():
        """Mark overdue follow-up actions and notify school leadership."""
        from click import echo
        from app.services.meeting_service import escalate_overdue_actions
        echo(f"Escalated {escalate_overdue_actions()} overdue meeting actions.")

    @app.cli.command("reconcile-daily-attendance")
    def reconcile_daily_attendance_command():
        """Save absences for students without an arrival scan after the late deadline."""
        from click import echo
        from app.services.attendance_service import reconcile_daily_absences
        try:
            absentees = reconcile_daily_absences()
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        echo(f"Recorded {len(absentees)} new absence(s).")

    return app

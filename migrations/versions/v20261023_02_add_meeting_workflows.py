"""Add verified guardians and the full parent-school meeting workflow."""

import datetime

from sqlalchemy import inspect, text


def upgrade(connection):
    inspector = inspect(connection)
    if not inspector.has_table("user"):
        return

    if inspector.has_table("message_conversation"):
        columns = {column["name"] for column in inspector.get_columns("message_conversation")}
        if "meeting_id" not in columns:
            connection.execute(text(
                "ALTER TABLE message_conversation ADD COLUMN meeting_id INTEGER"
            ))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_message_conversation_meeting_id "
            "ON message_conversation (meeting_id)"
        ))

    from app.extensions import db
    from app.models import (
        Meeting, MeetingActionComment, MeetingActionItem, MeetingAgendaItem,
        MeetingAttendance, MeetingAuditEvent, MeetingMinutes, MeetingMinutesRevision,
        MeetingNotificationLog, MeetingParticipant, StudentGuardian,
    )

    db.metadata.create_all(bind=connection, tables=[
        StudentGuardian.__table__, Meeting.__table__, MeetingParticipant.__table__,
        MeetingAttendance.__table__, MeetingAgendaItem.__table__, MeetingMinutes.__table__,
        MeetingMinutesRevision.__table__, MeetingActionItem.__table__,
        MeetingActionComment.__table__, MeetingAuditEvent.__table__,
        MeetingNotificationLog.__table__,
    ])

    guardian_columns = {column["name"] for column in inspect(connection).get_columns("student_guardian")}
    required = {"student_id", "guardian_id", "relationship", "status", "verified_at"}
    if required.issubset(guardian_columns):
        connection.execute(text("""
            INSERT INTO student_guardian
                (student_id, guardian_id, relationship, status, verified_by_id, verified_at)
            SELECT student.id, student.parent_id,
                   COALESCE(NULLIF(student.guardian_relationship, ''), 'Parent / Guardian'),
                   'verified', NULL, :timestamp
            FROM user AS student
            JOIN user AS guardian ON guardian.id = student.parent_id
            WHERE student.role = 'student'
              AND guardian.role = 'parent'
              AND student.parent_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM student_guardian AS link
                  WHERE link.student_id = student.id AND link.guardian_id = student.parent_id
              )
        """), {"timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

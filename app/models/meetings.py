from sqlalchemy import text

from app.extensions import db


class StudentGuardian(db.Model):
    """A verified, revocable link between a parent account and student."""
    __tablename__ = "student_guardian"
    __table_args__ = (db.UniqueConstraint("student_id", "guardian_id", name="uq_student_guardian"),)

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    guardian_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    relationship = db.Column(db.String(80), nullable=False, default="Parent / Guardian")
    status = db.Column(db.String(20), nullable=False, default="verified", server_default=text("'verified'"), index=True)
    verified_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    verified_at = db.Column(db.String(19), nullable=False)
    revoked_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    revoked_at = db.Column(db.String(19))
    student = db.relationship("User", foreign_keys=[student_id])
    guardian = db.relationship("User", foreign_keys=[guardian_id])
    verifier = db.relationship("User", foreign_keys=[verified_by_id])
    revoker = db.relationship("User", foreign_keys=[revoked_by_id])


class Meeting(db.Model):
    __tablename__ = "meeting"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text)
    meeting_type = db.Column(db.String(32), nullable=False, default="general", index=True)
    class_name = db.Column(db.String(100), index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    organizer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    chairperson_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    secretary_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    minutes_approver_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    scheduled_date = db.Column(db.String(10), nullable=False, index=True)
    start_time = db.Column(db.String(5), nullable=False)
    end_time = db.Column(db.String(5), nullable=False)
    location = db.Column(db.String(160))
    status = db.Column(db.String(24), nullable=False, default="Scheduled", index=True)
    approval_status = db.Column(db.String(24), nullable=False, default="not_required", index=True)
    confidentiality = db.Column(db.String(24), nullable=False, default="participants")
    qa_duration_minutes = db.Column(db.Integer)
    panel_perspectives = db.Column(db.String(1000))
    room_released_at = db.Column(db.String(19))
    room_release_reason = db.Column(db.String(160))
    microsoft_event_id = db.Column(db.String(255))
    microsoft_join_url = db.Column(db.String(1000))
    microsoft_sync_status = db.Column(db.String(24), nullable=False, default="not_configured")
    microsoft_sync_error = db.Column(db.String(500))
    created_at = db.Column(db.String(19), nullable=False)
    updated_at = db.Column(db.String(19), nullable=False)
    organizer = db.relationship("User", foreign_keys=[organizer_id])
    student = db.relationship("User", foreign_keys=[student_id])
    chairperson = db.relationship("User", foreign_keys=[chairperson_id])
    secretary = db.relationship("User", foreign_keys=[secretary_id])
    minutes_approver = db.relationship("User", foreign_keys=[minutes_approver_id])
    participants = db.relationship("MeetingParticipant", back_populates="meeting", cascade="all, delete-orphan")
    agenda_items = db.relationship(
        "MeetingAgendaItem", back_populates="meeting", cascade="all, delete-orphan",
        order_by="MeetingAgendaItem.position, MeetingAgendaItem.id",
    )
    minutes = db.relationship("MeetingMinutes", back_populates="meeting", uselist=False, cascade="all, delete-orphan")
    actions = db.relationship("MeetingActionItem", back_populates="meeting", cascade="all, delete-orphan")


class RoomReservation(db.Model):
    __tablename__ = "room_reservation"

    id = db.Column(db.Integer, primary_key=True)
    room_name = db.Column(db.String(160), nullable=False, index=True)
    purpose = db.Column(db.String(160), nullable=False, default="Quick room booking")
    reserved_by_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    starts_at = db.Column(db.DateTime, nullable=False, index=True)
    ends_at = db.Column(db.DateTime, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    created_at = db.Column(db.DateTime, nullable=False)
    released_at = db.Column(db.DateTime)
    reserved_by = db.relationship("User", foreign_keys=[reserved_by_id])


class MeetingParticipant(db.Model):
    __tablename__ = "meeting_participant"
    __table_args__ = (db.UniqueConstraint("meeting_id", "user_id", name="uq_meeting_participant_user"),)

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey("meeting.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    participant_role = db.Column(db.String(32), nullable=False)
    invited_at = db.Column(db.String(19), nullable=False)
    invitation_status = db.Column(db.String(24), nullable=False, default="invited", index=True)
    response_comment = db.Column(db.String(1000))
    responded_at = db.Column(db.String(19))
    meeting = db.relationship("Meeting", back_populates="participants")
    user = db.relationship("User", foreign_keys=[user_id])
    student = db.relationship("User", foreign_keys=[student_id])
    attendance = db.relationship("MeetingAttendance", back_populates="participant", uselist=False,
                                 cascade="all, delete-orphan")


class MeetingAttendance(db.Model):
    __tablename__ = "meeting_attendance"
    __table_args__ = (db.UniqueConstraint("participant_id", name="uq_meeting_attendance_participant"),)

    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("meeting_participant.id", ondelete="CASCADE"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="Absent")
    representative_name = db.Column(db.String(160))
    representative_relationship = db.Column(db.String(80))
    verified_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.String(19), nullable=False)
    participant = db.relationship("MeetingParticipant", back_populates="attendance")
    verifier = db.relationship("User", foreign_keys=[verified_by_id])


class MeetingAgendaItem(db.Model):
    __tablename__ = "meeting_agenda_item"

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey("meeting.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    details = db.Column(db.Text)
    position = db.Column(db.Integer, nullable=False, default=0)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.String(19), nullable=False)
    meeting = db.relationship("Meeting", back_populates="agenda_items")
    creator = db.relationship("User", foreign_keys=[created_by_id])


class MeetingMinutes(db.Model):
    __tablename__ = "meeting_minutes"
    __table_args__ = (db.UniqueConstraint("meeting_id", name="uq_meeting_minutes_meeting"),)

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey("meeting.id", ondelete="CASCADE"), nullable=False, index=True)
    status = db.Column(db.String(24), nullable=False, default="draft", index=True)
    current_version = db.Column(db.Integer, nullable=False, default=0)
    submitted_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    submitted_at = db.Column(db.String(19))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    approved_at = db.Column(db.String(19))
    approval_note = db.Column(db.String(1000))
    meeting = db.relationship("Meeting", back_populates="minutes")
    revisions = db.relationship("MeetingMinutesRevision", back_populates="minutes", cascade="all, delete-orphan",
                                order_by="MeetingMinutesRevision.version")
    submitter = db.relationship("User", foreign_keys=[submitted_by_id])
    approver = db.relationship("User", foreign_keys=[approved_by_id])


class MeetingMinutesRevision(db.Model):
    __tablename__ = "meeting_minutes_revision"
    __table_args__ = (db.UniqueConstraint("minutes_id", "version", name="uq_meeting_minutes_revision"),)

    id = db.Column(db.Integer, primary_key=True)
    minutes_id = db.Column(db.Integer, db.ForeignKey("meeting_minutes.id", ondelete="CASCADE"), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    content = db.Column(db.Text, nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.String(19), nullable=False)
    event = db.Column(db.String(24), nullable=False, default="saved")
    decision_note = db.Column(db.String(1000))
    minutes = db.relationship("MeetingMinutes", back_populates="revisions")
    author = db.relationship("User", foreign_keys=[author_id])


class MeetingActionItem(db.Model):
    __tablename__ = "meeting_action_item"

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey("meeting.id", ondelete="CASCADE"), nullable=False, index=True)
    agenda_item_id = db.Column(db.Integer, db.ForeignKey("meeting_agenda_item.id", ondelete="SET NULL"), index=True)
    title = db.Column(db.String(200), nullable=False)
    details = db.Column(db.Text)
    assignee_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    assigned_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    deadline = db.Column(db.String(10), nullable=False, index=True)
    status = db.Column(db.String(24), nullable=False, default="Not Started", index=True)
    completion_evidence = db.Column(db.Text)
    review_outcome = db.Column(db.String(1000))
    is_parent_visible = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.String(19), nullable=False)
    updated_at = db.Column(db.String(19), nullable=False)
    meeting = db.relationship("Meeting", back_populates="actions")
    agenda_item = db.relationship("MeetingAgendaItem")
    assignee = db.relationship("User", foreign_keys=[assignee_id])
    assigner = db.relationship("User", foreign_keys=[assigned_by_id])
    comments = db.relationship("MeetingActionComment", back_populates="action", cascade="all, delete-orphan",
                               order_by="MeetingActionComment.id")


class MeetingActionComment(db.Model):
    __tablename__ = "meeting_action_comment"

    id = db.Column(db.Integer, primary_key=True)
    action_id = db.Column(db.Integer, db.ForeignKey("meeting_action_item.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    body = db.Column(db.String(2000), nullable=False)
    is_parent_visible = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.String(19), nullable=False)
    action = db.relationship("MeetingActionItem", back_populates="comments")
    author = db.relationship("User", foreign_keys=[author_id])


class MeetingAuditEvent(db.Model):
    __tablename__ = "meeting_audit_event"

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey("meeting.id", ondelete="SET NULL"), index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    actor_name = db.Column(db.String(160), nullable=False)
    actor_role = db.Column(db.String(30), nullable=False)
    event_type = db.Column(db.String(48), nullable=False, index=True)
    details = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.String(19), nullable=False)
    previous_hash = db.Column(db.String(64), nullable=False)
    event_hash = db.Column(db.String(64), nullable=False, unique=True)


class MeetingNotificationLog(db.Model):
    __tablename__ = "meeting_notification_log"
    __table_args__ = (db.UniqueConstraint("dedupe_key", "user_id", name="uq_meeting_notice_dedupe_user"),)

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey("meeting.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    dedupe_key = db.Column(db.String(120), nullable=False)
    in_app_status = db.Column(db.String(20), nullable=False, default="queued")
    external_status = db.Column(db.String(20), nullable=False, default="delegated")
    created_at = db.Column(db.String(19), nullable=False)
    user = db.relationship("User")

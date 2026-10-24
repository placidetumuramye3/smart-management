from app.extensions import db
from sqlalchemy import false, text


class SchoolMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    sender_name = db.Column(db.String(160))
    sender_role = db.Column(db.String(30))
    recipient_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    recipient_role = db.Column(db.String(30))
    subject = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    message_type = db.Column(db.String(24), nullable=False, default="general", server_default=text("'general'"))
    conversation_id = db.Column(db.Integer, db.ForeignKey("message_conversation.id"), index=True)
    in_reply_to_id = db.Column(db.Integer, db.ForeignKey("school_message.id"), index=True)
    approval_status = db.Column(db.String(20), nullable=False, default="approved", server_default=text("'approved'"))
    approved_by_id = db.Column(db.Integer)
    approved_by_name = db.Column(db.String(160))
    approved_at = db.Column(db.String(19))
    created_at = db.Column(db.String(19), nullable=False)
    sender = db.relationship("User", foreign_keys=[sender_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])
    conversation = db.relationship("MessageConversation", back_populates="messages")
    in_reply_to = db.relationship("SchoolMessage", remote_side=[id], uselist=False)


class MessageConversation(db.Model):
    __tablename__ = "message_conversation"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    conversation_type = db.Column(db.String(30), nullable=False, default="direct")
    created_by_id = db.Column(db.Integer, nullable=False)
    created_by_name = db.Column(db.String(160), nullable=False)
    created_by_role = db.Column(db.String(30), nullable=False)
    created_at = db.Column(db.String(19), nullable=False)
    meeting_id = db.Column(db.Integer, db.ForeignKey("meeting.id", ondelete="SET NULL"), unique=True, index=True)
    participants = db.relationship(
        "MessageParticipant", back_populates="conversation", cascade="all, delete-orphan"
    )
    messages = db.relationship(
        "SchoolMessage", back_populates="conversation", order_by="SchoolMessage.id"
    )


class MessageParticipant(db.Model):
    __tablename__ = "message_participant"
    __table_args__ = (db.UniqueConstraint("conversation_id", "user_id", name="uq_message_participant"),)

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("message_conversation.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    user_name = db.Column(db.String(160), nullable=False)
    user_role = db.Column(db.String(30), nullable=False)
    added_at = db.Column(db.String(19), nullable=False)
    conversation = db.relationship("MessageConversation", back_populates="participants")
    user = db.relationship("User")


class MessageReport(db.Model):
    __tablename__ = "message_report"
    __table_args__ = (db.UniqueConstraint("message_id", "reporter_id", name="uq_message_reporter"),)

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("school_message.id"), nullable=False, index=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True)
    reporter_name = db.Column(db.String(160), nullable=False)
    reporter_role = db.Column(db.String(30), nullable=False)
    reason = db.Column(db.String(1000), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="open", server_default=text("'open'"))
    resolution = db.Column(db.String(1000))
    reviewed_by_id = db.Column(db.Integer)
    reviewed_by_name = db.Column(db.String(160))
    created_at = db.Column(db.String(19), nullable=False)
    reviewed_at = db.Column(db.String(19))
    message = db.relationship("SchoolMessage")
    reporter = db.relationship("User", foreign_keys=[reporter_id])


class MessagePermission(db.Model):
    __tablename__ = "message_permission"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True, index=True)
    can_send = db.Column(db.Boolean, nullable=False)
    updated_by_id = db.Column(db.Integer, nullable=False)
    updated_by_name = db.Column(db.String(160), nullable=False)
    updated_at = db.Column(db.String(19), nullable=False)
    user = db.relationship("User", foreign_keys=[user_id])


class MessageAuditEvent(db.Model):
    """Append-only, hash-chained record of messaging and moderation actions."""
    __tablename__ = "message_audit_event"

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer)
    actor_name = db.Column(db.String(160), nullable=False)
    actor_role = db.Column(db.String(30), nullable=False)
    event_type = db.Column(db.String(40), nullable=False, index=True)
    conversation_id = db.Column(db.Integer, index=True)
    message_id = db.Column(db.Integer, index=True)
    details = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.String(19), nullable=False)
    previous_hash = db.Column(db.String(64), nullable=False)
    event_hash = db.Column(db.String(64), nullable=False, unique=True)


class OfficialNoticePermission(db.Model):
    """Explicit, per-account grants for the official notice workflow."""
    __tablename__ = "official_notice_permission"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    can_create = db.Column(db.Boolean, nullable=False, default=False, server_default=false())
    can_approve = db.Column(db.Boolean, nullable=False, default=False, server_default=false())
    can_approve_sensitive = db.Column(db.Boolean, nullable=False, default=False, server_default=false())
    can_publish = db.Column(db.Boolean, nullable=False, default=False, server_default=false())
    can_revoke = db.Column(db.Boolean, nullable=False, default=False, server_default=false())
    updated_by_id = db.Column(db.Integer)
    updated_by_name = db.Column(db.String(160))
    updated_at = db.Column(db.String(19))
    user = db.relationship("User", foreign_keys=[user_id])


class OfficialNotice(db.Model):
    """Official notices are stored separately from ordinary conversations."""
    __tablename__ = "official_notice"

    id = db.Column(db.Integer, primary_key=True)
    created_by_id = db.Column(db.Integer, index=True)
    created_by_name = db.Column(db.String(160), nullable=False)
    created_by_role = db.Column(db.String(30), nullable=False)
    subject = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    recipient_ids_json = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(40), nullable=False, default="general", server_default=text("'general'"))
    is_sensitive = db.Column(db.Boolean, nullable=False, default=False, server_default=false())
    status = db.Column(db.String(32), nullable=False, default="pending_approval", server_default=text("'pending_approval'"), index=True)
    created_at = db.Column(db.String(19), nullable=False)
    approved_by_id = db.Column(db.Integer)
    approved_by_name = db.Column(db.String(160))
    approved_by_role = db.Column(db.String(30))
    approved_at = db.Column(db.String(19))
    sensitive_approved_by_id = db.Column(db.Integer)
    sensitive_approved_by_name = db.Column(db.String(160))
    sensitive_approved_by_role = db.Column(db.String(30))
    sensitive_approved_at = db.Column(db.String(19))
    published_by_id = db.Column(db.Integer)
    published_by_name = db.Column(db.String(160))
    published_by_role = db.Column(db.String(30))
    published_at = db.Column(db.String(19))
    revoked_by_id = db.Column(db.Integer)
    revoked_by_name = db.Column(db.String(160))
    revoked_by_role = db.Column(db.String(30))
    revoked_at = db.Column(db.String(19))
    rejected_by_id = db.Column(db.Integer)
    rejected_by_name = db.Column(db.String(160))
    rejected_by_role = db.Column(db.String(30))
    rejected_at = db.Column(db.String(19))
    decision_note = db.Column(db.String(1000))


class OfficialNoticeAuditEvent(db.Model):
    """Append-only, hash-chained audit history for notice and grant actions."""
    __tablename__ = "official_notice_audit_event"

    id = db.Column(db.Integer, primary_key=True)
    notice_id = db.Column(db.Integer, index=True)
    actor_id = db.Column(db.Integer)
    actor_name = db.Column(db.String(160), nullable=False)
    actor_role = db.Column(db.String(30), nullable=False)
    event_type = db.Column(db.String(40), nullable=False, index=True)
    details = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.String(19), nullable=False)
    previous_hash = db.Column(db.String(64), nullable=False)
    event_hash = db.Column(db.String(64), nullable=False, unique=True)


class UserNotification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    category = db.Column(db.String(40), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    link = db.Column(db.String(255), default="/notifications")
    created_at = db.Column(db.String(19), nullable=False)
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    user = db.relationship("User", backref="notifications")


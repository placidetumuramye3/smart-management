"""Add conversation, approval, and moderation metadata to school messages."""

from sqlalchemy import inspect, text


def upgrade(connection):
    inspector = inspect(connection)
    if inspector.has_table("school_message"):
        columns = {column["name"] for column in inspector.get_columns("school_message")}
        additions = {
            "conversation_id": "INTEGER",
            "in_reply_to_id": "INTEGER",
            "approval_status": "VARCHAR(20) NOT NULL DEFAULT 'approved'",
            "approved_by_id": "INTEGER",
            "approved_by_name": "VARCHAR(160)",
            "approved_at": "VARCHAR(19)",
        }
        for name, column_type in additions.items():
            if name not in columns:
                connection.execute(text(
                    f"ALTER TABLE school_message ADD COLUMN {name} {column_type}"
                ))

    if inspector.has_table("user"):
        # Create the new messaging tables through the migration path for existing
        # databases. The app startup create_all call remains an idempotent fallback.
        from app.extensions import db
        from app.models import (
            SchoolMessage,
            MessageConversation,
            MessageParticipant,
            MessageReport,
            MessagePermission,
            MessageAuditEvent,
        )

        db.metadata.create_all(
            bind=connection,
            tables=[
                SchoolMessage.__table__,
                MessageConversation.__table__,
                MessageParticipant.__table__,
                MessageReport.__table__,
                MessagePermission.__table__,
                MessageAuditEvent.__table__,
            ],
        )

"""Add separate official notices, explicit user grants, and an audit trail."""


def upgrade(connection):
    from sqlalchemy import inspect, text

    if not inspect(connection).has_table("user"):
        return

    from app.extensions import db
    from app.models import OfficialNotice, OfficialNoticeAuditEvent, OfficialNoticePermission

    db.metadata.create_all(
        bind=connection,
        tables=[
            OfficialNoticePermission.__table__,
            OfficialNotice.__table__,
            OfficialNoticeAuditEvent.__table__,
        ],
    )
    columns = {
        column["name"] for column in inspect(connection).get_columns("official_notice")
    }
    for name, column_type in (
        ("category", "VARCHAR(40) NOT NULL DEFAULT 'general'"),
        ("rejected_by_id", "INTEGER"),
        ("rejected_by_name", "VARCHAR(160)"),
        ("rejected_by_role", "VARCHAR(30)"),
        ("rejected_at", "VARCHAR(19)"),
    ):
        if name not in columns:
            connection.execute(text(
                f"ALTER TABLE official_notice ADD COLUMN {name} {column_type}"
            ))

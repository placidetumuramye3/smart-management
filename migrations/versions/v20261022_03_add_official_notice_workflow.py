"""Add separate official notices, explicit user grants, and an audit trail."""


def upgrade(connection):
    from sqlalchemy import inspect

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

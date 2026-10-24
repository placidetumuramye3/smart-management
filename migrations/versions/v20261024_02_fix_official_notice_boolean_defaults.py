"""Use PostgreSQL boolean literals for official notice defaults."""


def upgrade(connection):
    """Correct existing server defaults without changing stored notice data."""
    if connection.dialect.name != "postgresql":
        return

    from sqlalchemy import inspect, text

    inspector = inspect(connection)
    defaults_by_table = {
        "official_notice_permission": (
            "can_create",
            "can_approve",
            "can_approve_sensitive",
            "can_publish",
            "can_revoke",
        ),
        "official_notice": ("is_sensitive",),
    }

    for table_name, column_names in defaults_by_table.items():
        if not inspector.has_table(table_name):
            continue
        existing_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        for column_name in column_names:
            if column_name in existing_columns:
                connection.execute(text(
                    f'ALTER TABLE "{table_name}" '
                    f'ALTER COLUMN "{column_name}" SET DEFAULT FALSE'
                ))

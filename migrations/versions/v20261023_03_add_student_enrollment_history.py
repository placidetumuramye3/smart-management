"""Preserve current student class placement as dated enrollment history."""

import datetime

from sqlalchemy import inspect, text


def upgrade(connection):
    if not inspect(connection).has_table("user"):
        return

    from app.extensions import db
    from app.models import StudentEnrollment

    db.metadata.create_all(bind=connection, tables=[StudentEnrollment.__table__])
    columns = {column["name"] for column in inspect(connection).get_columns("user")}
    required = {"role", "class_name", "trade", "level", "academic_year"}
    if not required.issubset(columns):
        return

    connection.execute(text("""
        INSERT INTO student_enrollment
            (student_id, class_name, trade, level, academic_year, start_date,
             end_date, status, reason, recorded_by_id, created_at)
        SELECT student.id, student.class_name, student.trade, student.level,
               student.academic_year, NULL, NULL, 'active',
               'Legacy placement imported; original enrollment date is unknown.',
               NULL, :created_at
        FROM user AS student
        WHERE student.role = 'student'
          AND NOT EXISTS (
              SELECT 1 FROM student_enrollment AS enrollment
              WHERE enrollment.student_id = student.id AND enrollment.status = 'active'
          )
    """), {"created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

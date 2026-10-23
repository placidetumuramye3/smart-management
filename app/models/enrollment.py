from sqlalchemy import CheckConstraint, text

from app.extensions import db


class StudentEnrollment(db.Model):
    """A dated record of the student's official class enrollment."""

    __tablename__ = "student_enrollment"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'transferred', 'withdrawn', 'graduated')",
            name="ck_student_enrollment_status",
        ),
        db.Index("ix_student_enrollment_student_status", "student_id", "status"),
        db.Index(
            "uq_student_enrollment_active", "student_id", unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    class_name = db.Column(db.String(100), index=True)
    trade = db.Column(db.String(100))
    level = db.Column(db.String(50))
    academic_year = db.Column(db.String(9), index=True)
    start_date = db.Column(db.String(10))
    end_date = db.Column(db.String(10))
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    reason = db.Column(db.String(500))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.String(19), nullable=False)

    student = db.relationship("User", foreign_keys=[student_id], backref="enrollment_history")
    recorded_by = db.relationship("User", foreign_keys=[recorded_by_id])

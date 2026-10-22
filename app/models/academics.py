from app.extensions import db
from datetime import datetime


class AcademicSubject(db.Model):
    """A subject offered to a class, with an optional responsible teacher."""
    id = db.Column(db.Integer, primary_key=True)
    trade = db.Column(db.String(100), nullable=False)
    level = db.Column(db.String(50), nullable=False)
    class_name = db.Column(db.String(100), nullable=False, index=True)
    subject = db.Column(db.String(160), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    academic_year = db.Column(db.String(9))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    teacher = db.relationship("User", foreign_keys=[teacher_id])
    __table_args__ = (db.UniqueConstraint("class_name", "subject", "academic_year", name="uq_academic_subject_class_year"),)


class SyllabusCoverage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("academic_subject.id"), nullable=False, index=True)
    unit_title = db.Column(db.String(180), nullable=False)
    planned_lessons = db.Column(db.Integer, nullable=False, default=1)
    completed_lessons = db.Column(db.Integer, nullable=False, default=0)
    notes = db.Column(db.Text)
    updated_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    subject = db.relationship("AcademicSubject", backref=db.backref("coverage", lazy=True, cascade="all, delete-orphan"))
    updater = db.relationship("User", foreign_keys=[updated_by])
    __table_args__ = (db.UniqueConstraint("subject_id", "unit_title", name="uq_syllabus_unit_subject"),)


class LessonPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("academic_subject.id"), nullable=False, index=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    lesson_date = db.Column(db.Date, nullable=False)
    topic = db.Column(db.String(180), nullable=False)
    objectives = db.Column(db.Text, nullable=False)
    activities = db.Column(db.Text)
    resources = db.Column(db.Text)
    status = db.Column(db.String(30), nullable=False, default="Pending")
    review_comment = db.Column(db.Text)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    submitted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime)
    subject = db.relationship("AcademicSubject", backref=db.backref("lesson_plans", lazy=True, cascade="all, delete-orphan"))
    teacher = db.relationship("User", foreign_keys=[teacher_id])
    reviewer = db.relationship("User", foreign_keys=[reviewed_by])


class ReportCardApproval(db.Model):
    """School leadership's confirmation that a learner's yearly report is ready."""
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    academic_year = db.Column(db.String(9), nullable=False)
    confirmed_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    confirmed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    student = db.relationship("User", foreign_keys=[student_id])
    confirmer = db.relationship("User", foreign_keys=[confirmed_by])
    __table_args__ = (db.UniqueConstraint("student_id", "academic_year", name="uq_report_approval_student_year"),)
class Grade(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    term = db.Column(db.String(50), default="Term 1")
    academic_year = db.Column(db.String(9))
    module_code = db.Column(db.String(40))
    module_category = db.Column(db.String(32), default="Specific")
    module_weight = db.Column(db.Float)
    marks = db.Column(db.Float, nullable=False)
    max_marks = db.Column(db.Float, default=100)
    formative_marks = db.Column(db.Float)
    integrated_marks = db.Column(db.Float)
    comprehensive_marks = db.Column(db.Float)
    reassessment_marks = db.Column(db.Float)
    remarks = db.Column(db.Text)
    is_published = db.Column(db.Boolean, nullable=False, default=False)
    student = db.relationship("User", foreign_keys=[student_id], backref="grades")
    teacher = db.relationship("User", foreign_keys=[teacher_id])


class TimetableEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_name = db.Column(db.String(100), nullable=False)
    day_of_week = db.Column(db.String(20), nullable=False)
    time_slot = db.Column(db.String(50), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    teacher_name = db.Column(db.String(160))


class DayScheduleEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    schedule_date = db.Column(db.Date, nullable=False, index=True)
    class_name = db.Column(db.String(100))
    start_time = db.Column(db.String(20))
    end_time = db.Column(db.String(20))
    activity = db.Column(db.String(160), nullable=False)
    details = db.Column(db.Text)
    is_published = db.Column(db.Boolean, nullable=False, default=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    creator = db.relationship("User", foreign_keys=[created_by])


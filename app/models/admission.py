from app.extensions import db


class AdmissionApplicant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(160), nullable=False)
    guardian_name = db.Column(db.String(160))
    phone = db.Column(db.String(40))
    email = db.Column(db.String(160))
    desired_department = db.Column(db.String(100))
    desired_trade = db.Column(db.String(100), nullable=False)
    desired_level = db.Column(db.String(50), nullable=False)
    desired_class = db.Column(db.String(100), nullable=False)
    desired_section = db.Column(db.String(50))
    documents_submitted = db.Column(db.Text)
    documents_verified = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String(30), nullable=False, default="Submitted", index=True)
    applied_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    converted_student_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True)
    converted_student = db.relationship("User", foreign_keys=[converted_student_id])

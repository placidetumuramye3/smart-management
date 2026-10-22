from app.extensions import db


class StudentDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(80), nullable=False, unique=True)
    category = db.Column(db.String(80), nullable=False)
    uploaded_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    student = db.relationship("User", foreign_keys=[student_id])
    uploader = db.relationship("User", foreign_keys=[uploaded_by])

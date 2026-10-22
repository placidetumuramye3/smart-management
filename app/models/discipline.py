from app.extensions import db
class DisciplineCase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    record_type = db.Column(db.String(30), nullable=False, default="Concern")
    details = db.Column(db.Text, nullable=False)
    action_taken = db.Column(db.Text)
    remarks = db.Column(db.Text)
    mark_deduction = db.Column(db.Float, default=0)
    is_published = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.String(19), nullable=False)
    student = db.relationship("User", backref="discipline_cases")


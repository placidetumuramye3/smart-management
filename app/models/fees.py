from app.extensions import db
class FeeTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    requirement_id = db.Column(db.Integer, db.ForeignKey("fee_requirement.id"))
    total_fees = db.Column(db.Float, default=150000)
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(40), default="Cash")
    reference = db.Column(db.String(100))
    created_at = db.Column(db.String(19), nullable=False)
    student = db.relationship("User", backref="fee_transactions")
    requirement = db.relationship("FeeRequirement", backref="payments")


class FeeRequirement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title = db.Column(db.String(120), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    details = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    student = db.relationship("User", foreign_keys=[student_id], backref="fee_requirements")
    creator = db.relationship("User", foreign_keys=[created_by])


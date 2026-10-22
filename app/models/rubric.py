from app.extensions import db


class GradingRubric(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.String(19), nullable=False)
    teacher = db.relationship("User", foreign_keys=[teacher_id])
    criteria = db.relationship(
        "GradingRubricCriterion", backref="rubric", cascade="all, delete-orphan",
        order_by="GradingRubricCriterion.position",
    )


class GradingRubricCriterion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    rubric_id = db.Column(db.Integer, db.ForeignKey("grading_rubric.id"), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text)
    max_points = db.Column(db.Float, nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)

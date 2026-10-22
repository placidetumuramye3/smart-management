from app.extensions import db


class Assessment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    assessment_type = db.Column(db.String(20), nullable=False)
    class_name = db.Column(db.String(100), nullable=False, index=True)
    target_student_ids = db.Column(db.Text)
    rubric_id = db.Column(db.Integer, db.ForeignKey("grading_rubric.id"))
    time_limit_minutes = db.Column(db.Integer)
    randomize_questions = db.Column(db.Boolean, nullable=False, default=False)
    focus_mode = db.Column(db.Boolean, nullable=False, default=False)
    subject = db.Column(db.String(160), nullable=False)
    term = db.Column(db.String(30), nullable=False, default="Term 1")
    academic_year = db.Column(db.String(9))
    instructions = db.Column(db.Text)
    media_url = db.Column(db.String(1000))
    due_at = db.Column(db.String(19))
    max_score = db.Column(db.Float, nullable=False, default=100)
    attachment_path = db.Column(db.String(255))
    attachment_name = db.Column(db.String(255))
    is_published = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.String(19), nullable=False)
    teacher = db.relationship("User", foreign_keys=[teacher_id])
    rubric = db.relationship("GradingRubric", foreign_keys=[rubric_id])
    questions = db.relationship("AssessmentQuestion", backref="assessment", cascade="all, delete-orphan", order_by="AssessmentQuestion.id")
    submissions = db.relationship("AssessmentSubmission", backref="assessment", cascade="all, delete-orphan")


class AssessmentQuestion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(db.Integer, db.ForeignKey("assessment.id"), nullable=False, index=True)
    prompt = db.Column(db.Text, nullable=False)
    question_type = db.Column(db.String(30), nullable=False, default="multiple_choice")
    options_json = db.Column(db.Text, nullable=False)
    correct_answer = db.Column(db.Text, nullable=False, default="")
    media_url = db.Column(db.String(1000))
    points = db.Column(db.Float, nullable=False, default=1)


class AssessmentSubmission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(db.Integer, db.ForeignKey("assessment.id"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    submitted_at = db.Column(db.String(19), nullable=False)
    answers_json = db.Column(db.Text)
    auto_score = db.Column(db.Float)
    rubric_results_json = db.Column(db.Text)
    focus_warnings = db.Column(db.Integer, nullable=False, default=0)
    attachment_path = db.Column(db.String(255))
    attachment_name = db.Column(db.String(255))
    score = db.Column(db.Float)
    feedback = db.Column(db.Text)
    is_graded = db.Column(db.Boolean, nullable=False, default=False)
    student = db.relationship("User", foreign_keys=[student_id])
    __table_args__ = (db.UniqueConstraint("assessment_id", "student_id", name="uq_assessment_student_submission"),)


class AssessmentAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(db.Integer, db.ForeignKey("assessment.id"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    started_at = db.Column(db.String(19), nullable=False)
    question_order_json = db.Column(db.Text)
    __table_args__ = (db.UniqueConstraint("assessment_id", "student_id", name="uq_assessment_student_attempt"),)

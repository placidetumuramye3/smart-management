import datetime
import json
import math
import os
import random
import secrets
from urllib.parse import urlsplit

from flask import abort, current_app, redirect, request, send_file, url_for, flash
from flask_login import current_user
from markupsafe import escape
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import (Assessment, AssessmentQuestion, AssessmentSubmission, AssessmentAttempt, GradingRubric,
                        GradingRubricCriterion, User, TimetableEntry)
from app.utils.constants import CLASS_TRADE_LEVEL, PROGRAM_LEVELS, ROLE_ACCESS, TRADES
from app.utils.decorators import permission_for
from app.utils.helpers import linked_children, now_string, teacher_assignments, teacher_assigned_classes, teacher_can_access_class
from app.utils.notifications import notify_student_and_parent, notify_users
from app.utils.rendering import page


ALLOWED_ASSIGNMENT_EXTENSIONS = {"pdf", "doc", "docx", "odt", "ppt", "pptx", "txt", "rtf", "jpg", "jpeg", "png"}
QUESTION_ASSESSMENT_TYPES = {"Quiz", "Test"}
HOMEWORK_TYPES = {"Assignment", "Homework"}
QUESTION_TYPES = {"multiple_choice", "true_false", "open_ended", "short_answer", "matching"}


def _parse_matching_pairs(raw):
    pairs = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|", 1)]
        if len(parts) != 2 or not all(parts):
            raise ValueError("Enter matching pairs one per line as Term | Match.")
        pairs.append({"term": parts[0], "match": parts[1]})
    if len(pairs) < 2:
        raise ValueError("A matching question needs at least two Term | Match pairs.")
    if len({pair["term"].casefold() for pair in pairs}) != len(pairs):
        raise ValueError("Matching terms must be unique within a question.")
    if len({pair["match"].casefold() for pair in pairs}) != len(pairs):
        raise ValueError("Matching answers must be unique within a question.")
    return pairs


def _valid_https_url(value):
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def _question_media_html(media_url):
    if not media_url:
        return ""
    try:
        parsed = urlsplit(media_url)
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    extension = os.path.splitext(parsed.path)[1].lower()
    safe_url = escape(media_url)
    if extension in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return f"<img class='img-fluid rounded mb-3' src='{safe_url}' alt='Question media' loading='lazy' style='max-height:420px'>"
    if extension in {".mp4", ".webm", ".ogv"}:
        mime = {".mp4": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg"}[extension]
        return f"<video class='w-100 rounded mb-3' controls preload='metadata' style='max-height:480px'><source src='{safe_url}' type='{mime}'>Your browser does not support this video.</video>"
    return f"<p><a href='{safe_url}' target='_blank' rel='noopener noreferrer'>Open question resource</a></p>"


def _rubric_results_html(assessment, submission):
    if not assessment.rubric or not submission.rubric_results_json:
        return ""
    try:
        results = json.loads(submission.rubric_results_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    rows = []
    for criterion in assessment.rubric.criteria:
        result = results.get(str(criterion.id), {})
        score = result.get("score")
        if score is None:
            continue
        comment = result.get("comment", "")
        rows.append(
            f"<li><strong>{escape(criterion.title)}</strong>: {score:g} / {criterion.max_points:g}"
            f"{f' Â· {escape(comment)}' if comment else ''}</li>")
    if not rows:
        return ""
    return "<details class='mt-2'><summary>Rubric results</summary><ul>" + "".join(rows) + "</ul></details>"


def _rubric_criteria_html(assessment):
    if not assessment.rubric:
        return ""
    criteria_rows = []
    for criterion in assessment.rubric.criteria:
        description = f": {escape(criterion.description)}" if criterion.description else ""
        criteria_rows.append(
            f"<li><strong>{escape(criterion.title)}</strong> Â· up to {criterion.max_points:g} points{description}</li>")
    criteria = "".join(criteria_rows)
    return f"<details class='mt-2'><summary>Grading rubric ({sum(item.max_points for item in assessment.rubric.criteria):g} points)</summary><ul>{criteria}</ul></details>"


def _teacher_subjects(teacher):
    return {(class_name, subject) for class_name, subject in teacher_assignments(teacher)}


def _store_upload(upload, folder, allowed_extensions):
    if not upload or not upload.filename:
        return None, None
    original_name = secure_filename(upload.filename)
    extension = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    if extension not in allowed_extensions:
        abort(400, description="This file type is not allowed.")
    stored_name = f"{secrets.token_hex(12)}.{extension}"
    directory = os.path.join(current_app.config["UPLOAD_FOLDER"], folder)
    os.makedirs(directory, exist_ok=True)
    upload.save(os.path.join(directory, stored_name))
    return f"{folder}/{stored_name}", original_name


def _assessment_for_staff(assessment_id):
    assessment = db.session.get(Assessment, assessment_id) or abort(404)
    if current_user.role == "teacher" and (assessment.teacher_id != current_user.id or not teacher_can_access_class(current_user, assessment.class_name)):
        abort(403)
    return assessment


def _student_can_access(student, assessment):
    if student.role != "student":
        return False
    if student.class_name != assessment.class_name:
        return False
    if student.id == current_user.id:
        return True
    return current_user.role == "parent" and student.parent_id == current_user.id


def _assessment_targets_student(assessment, student):
    if not assessment.target_student_ids:
        return True
    try:
        target_ids = {int(student_id) for student_id in json.loads(assessment.target_student_ids)}
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return student.id in target_ids


def _attempt_deadline(assessment, attempt):
    if not assessment.time_limit_minutes or not attempt:
        return None
    try:
        started = datetime.datetime.strptime(attempt.started_at, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    return started + datetime.timedelta(minutes=assessment.time_limit_minutes)


def _record_timed_out_attempt(assessment, student):
    submission = AssessmentSubmission(
        assessment_id=assessment.id, student_id=student.id, submitted_at=now_string(),
        answers_json="{}", score=0, auto_score=0, is_graded=True)
    db.session.add(submission)
    notify_student_and_parent(
        student, "Academics", f"Time expired: {assessment.title}",
        f"The time limit expired. Your recorded score is 0 / {assessment.max_score:g}.", "/assessments")
    db.session.commit()
    return submission


def _assessment_analytics_html(assessments):
    rows = []
    for assessment in assessments:
        if not assessment.is_published:
            continue
        if assessment.target_student_ids:
            try:
                target_ids = {int(value) for value in json.loads(assessment.target_student_ids)}
            except (TypeError, ValueError, json.JSONDecodeError):
                target_ids = set()
            assigned = User.query.filter(
                User.id.in_(target_ids), User.role == "student", User.class_name == assessment.class_name
            ).all() if target_ids else []
        else:
            assigned = User.query.filter_by(role="student", class_name=assessment.class_name).all()
        assigned_ids = {student.id for student in assigned}
        submissions = [submission for submission in AssessmentSubmission.query.filter_by(
            assessment_id=assessment.id).all() if submission.student_id in assigned_ids]
        submitted_count = len(submissions)
        completion = f"{submitted_count}/{len(assigned)} ({submitted_count / len(assigned) * 100:.0f}%)" if assigned else "0/0"
        graded = [submission for submission in submissions
                  if submission.is_graded and submission.score is not None]
        average = (sum(submission.score / assessment.max_score * 100 for submission in graded) / len(graded)
                   if graded and assessment.max_score > 0 else None)
        gaps = []
        if assessment.questions:
            parsed_answers = []
            for submission in submissions:
                try:
                    parsed_answers.append(json.loads(submission.answers_json or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    parsed_answers.append({})
            for question in assessment.questions:
                if question.question_type == "open_ended":
                    continue
                correct_count = 0
                attempts = len(parsed_answers)
                for answers in parsed_answers:
                    key = str(question.id)
                    answer = answers.get(key)
                    if question.question_type in {"multiple_choice", "true_false"}:
                        correct = answer == question.correct_answer
                    elif question.question_type == "short_answer":
                        correct = " ".join(str(answer).casefold().split()) == " ".join(
                            question.correct_answer.casefold().split())
                    elif question.question_type == "matching":
                        pairs = json.loads(question.options_json or "[]")
                        correct = isinstance(answer, dict) and len(pairs) == len(answer) and all(
                            answer.get(str(index)) == pair["match"] for index, pair in enumerate(pairs))
                    else:
                        correct = False
                    correct_count += int(correct)
                if attempts:
                    accuracy = correct_count / attempts * 100
                    gaps.append((accuracy, question.prompt, attempts))
        gap_rows = "".join(
            f"<li class={'text-danger' if accuracy < 60 else ''}>{accuracy:.0f}% correct across {attempts} responses â€” {escape(prompt)}</li>"
            for accuracy, prompt, attempts in sorted(gaps, key=lambda item: item[0]))
        gap_summary = ("<details><summary>Question results (" + str(len(gaps)) + ")</summary><ul>" + gap_rows + "</ul></details>"
                       if gaps else "<span class='muted'>No auto-graded question data yet</span>")
        rows.append(
            f"<tr><td>{escape(assessment.title)}</td><td>{escape(assessment.class_name)} Â· {escape(assessment.subject)}</td>"
            f"<td>{completion}</td><td>{f'{average:.1f}%' if average is not None else 'Awaiting grades'}</td>"
            f"<td>{gap_summary}</td></tr>")
    if not rows:
        return "<section class='panel'><h2>Performance Analytics</h2><p class='muted'>Published assessments will appear here with completion, grade, and question results.</p></section>"
    return ("<section class='panel'><h2>Performance Analytics</h2><p class='muted'>Completion counts include all assigned students. Average grades use graded submissions only; question accuracy excludes open-ended responses. Results below 60% are highlighted for review.</p>"
            "<div class='table-responsive'><table class='table'><thead><tr><th>Assessment</th><th>Class Â· Subject</th><th>Completed</th><th>Average grade</th><th>Question results / possible gaps</th></tr></thead><tbody>" +
            "".join(rows) + "</tbody></table></div></section>")


def create_rubric():
    if current_user.role not in {"master_admin", "head_teacher", "teacher"}:
        abort(403)
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    titles = request.form.getlist("criterion_title[]")
    descriptions = request.form.getlist("criterion_description[]")
    point_values = request.form.getlist("criterion_points[]")
    criteria = []
    error = None
    if not name or len(name) > 160:
        error = "Enter a rubric name of at most 160 characters."
    elif len(titles) == 0 or len(titles) > 20:
        error = "Add between 1 and 20 rubric criteria."
    else:
        for index, title in enumerate(titles):
            title = title.strip()
            criterion_description = descriptions[index].strip() if index < len(descriptions) else ""
            try:
                points = float(point_values[index]) if index < len(point_values) else 0
            except ValueError:
                points = 0
            if not title or len(title) > 160 or len(criterion_description) > 1000:
                error = "Each rubric criterion needs a title; descriptions must be 1000 characters or fewer."
                break
            if not math.isfinite(points) or points <= 0:
                error = "Each rubric criterion needs a positive maximum point value."
                break
            criteria.append((title, criterion_description, points))
        if not error and not math.isfinite(sum(points for _, _, points in criteria)):
            error = "The rubric's total maximum score must be a finite number."
    if len(description) > 2000:
        error = "Rubric description must be 2000 characters or fewer."
    if error:
        flash(error, "danger")
    else:
        rubric = GradingRubric(
            teacher_id=current_user.id, name=name, description=description or None,
            created_at=now_string())
        rubric.criteria = [
            GradingRubricCriterion(title=title, description=criterion_description or None,
                                   max_points=points, position=index)
            for index, (title, criterion_description, points) in enumerate(criteria)
        ]
        db.session.add(rubric)
        db.session.commit()
        flash("Reusable grading rubric created.", "success")
    return redirect(url_for("assessments"))


def assessments():
    subjects = _teacher_subjects(current_user) if current_user.role == "teacher" else set()
    if request.method == "POST":
        if current_user.role not in {"master_admin", "head_teacher", "teacher"}:
            abort(403)
        title = request.form.get("title", "").strip()
        assessment_type = request.form.get("assessment_type", "").strip()
        homework_online = assessment_type == "Homework" and request.form.get("homework_online") == "yes"
        class_name = request.form.get("class_name", "").strip()
        assignment_trade = request.form.get("trade", "").strip()
        assignment_level = request.form.get("level", "").strip()
        if current_user.role == "teacher":
            assignment_trade, assignment_level = CLASS_TRADE_LEVEL.get(class_name, (None, None))
        target_mode = request.form.get("target_mode", "class").strip()
        raw_target_ids = request.form.getlist("target_student_ids")
        try:
            selected_target_ids = {int(student_id) for student_id in raw_target_ids}
            target_error = None
        except ValueError:
            selected_target_ids = set()
            target_error = "Choose valid students for the target group."
        subject = request.form.get("subject", "").strip()
        term = request.form.get("term", "Term 1").strip()
        academic_year = request.form.get("academic_year", "").strip()
        instructions = request.form.get("instructions", "").strip()
        assessment_media_url = request.form.get("assessment_media_url", "").strip()
        due_at = request.form.get("due_at", "").strip()
        raw_time_limit = request.form.get("time_limit_minutes", "").strip()
        try:
            time_limit_minutes = int(raw_time_limit) if raw_time_limit else None
        except ValueError:
            time_limit_minutes = -1
        randomize_questions = request.form.get("randomize_questions") == "yes"
        focus_mode = request.form.get("focus_mode") == "yes"
        homework_file = request.files.get("assignment_file")
        has_homework_file = bool(homework_file and homework_file.filename)
        is_published = request.form.get("publish") == "yes"
        try:
            max_score = float(request.form.get("max_score", 100) or 100)
        except ValueError:
            max_score = 0
        rubric_id_raw = request.form.get("rubric_id", "").strip()
        try:
            rubric_id = int(rubric_id_raw) if rubric_id_raw else None
        except ValueError:
            rubric_id = -1
        selected_rubric = db.session.get(GradingRubric, rubric_id) if rubric_id and rubric_id > 0 else None
        if selected_rubric and current_user.role == "teacher" and selected_rubric.teacher_id != current_user.id:
            selected_rubric = None
        class_students = User.query.filter_by(role="student", class_name=class_name).all() if class_name else []
        class_exists = bool(class_students) and CLASS_TRADE_LEVEL.get(class_name) == (assignment_trade, assignment_level)
        class_exists = class_exists and (current_user.role != "teacher" or teacher_can_access_class(current_user, class_name))
        if target_mode not in {"class", "selected"}:
            target_error = "Choose a valid assignment audience."
        elif target_mode == "selected" and not selected_target_ids:
            target_error = "Select at least one student, or assign the work to the whole class."
        target_students = []
        if target_mode == "selected" and selected_target_ids and not target_error:
            target_students = User.query.filter(
                User.id.in_(selected_target_ids), User.role == "student", User.class_name == class_name
            ).all()
            if {student.id for student in target_students} != selected_target_ids:
                target_error = "Every selected student must belong to the chosen class."
        if not title or not class_name or not class_exists or not subject or assessment_type not in {"Homework", "Quiz", "Test"}:
            flash("Enter a title, class, subject, and choose Homework, Quiz, or Test.", "danger")
        elif target_error:
            flash(target_error, "danger")
        elif (time_limit_minutes is not None and not 1 <= time_limit_minutes <= 300) or (
            assessment_type not in QUESTION_ASSESSMENT_TYPES and
            (time_limit_minutes is not None or randomize_questions or focus_mode)
        ):
            flash("Security settings apply only to quizzes and tests. Time limits must be from 1 to 300 minutes.", "danger")
        elif rubric_id_raw and (rubric_id <= 0 or not selected_rubric):
            flash("Choose one of your available reusable rubrics.", "danger")
        elif selected_rubric and (assessment_type != "Homework" or homework_online):
            flash("Reusable rubrics can be attached to file-based Homework only.", "danger")
        elif assessment_media_url and (len(assessment_media_url) > 1000 or not _valid_https_url(assessment_media_url)):
            flash("Assessment media must be a valid HTTPS URL under 1000 characters.", "danger")
        elif max_score <= 0:
            flash("Maximum score must be greater than zero.", "danger")
        elif assessment_type == "Homework" and not homework_online and not instructions and not has_homework_file:
            flash("Add homework instructions or attach a homework file.", "danger")
        elif current_user.role == "teacher" and (class_name.casefold(), subject.casefold()) not in {
            (assigned_class.casefold(), assigned_subject.casefold()) for assigned_class, assigned_subject in subjects
        }:
            flash("You can create assessments only for lessons assigned to you for that class.", "danger")
        else:
            if selected_rubric:
                max_score = sum(criterion.max_points for criterion in selected_rubric.criteria)
            due_value = None
            if due_at:
                try:
                    due_value = datetime.datetime.fromisoformat(due_at).strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    flash("Enter a valid assessment deadline.", "danger")
                    return redirect(url_for("assessments"))
            if assessment_type in QUESTION_ASSESSMENT_TYPES:
                attachment_path, attachment_name = None, None
            else:
                attachment_path, attachment_name = _store_upload(
                    homework_file, "assessments", ALLOWED_ASSIGNMENT_EXTENSIONS)
            assessment = Assessment(
                teacher_id=current_user.id, title=title, assessment_type=assessment_type,
                class_name=class_name,
                target_student_ids=json.dumps(sorted(selected_target_ids)) if target_mode == "selected" else None,
                rubric_id=selected_rubric.id if selected_rubric else None,
                subject=subject, term=term or "Term 1",
                academic_year=academic_year or None, instructions=instructions or None,
                media_url=assessment_media_url or None,
                time_limit_minutes=time_limit_minutes if assessment_type in QUESTION_ASSESSMENT_TYPES else None,
                randomize_questions=randomize_questions if assessment_type in QUESTION_ASSESSMENT_TYPES else False,
                focus_mode=focus_mode if assessment_type in QUESTION_ASSESSMENT_TYPES else False,
                due_at=due_value, max_score=max_score, attachment_path=attachment_path,
                attachment_name=attachment_name, is_published=is_published, created_at=now_string())
            db.session.add(assessment)
            if assessment_type in QUESTION_ASSESSMENT_TYPES or homework_online:
                prompts = request.form.getlist("question[]")
                question_types = request.form.getlist("question_type[]")
                options = {key: request.form.getlist(f"option_{key.lower()}[]") for key in ("A", "B", "C", "D")}
                answers = request.form.getlist("correct[]")
                true_false_answers = request.form.getlist("true_false_correct[]")
                expected_answers = request.form.getlist("expected_answer[]")
                matching_data = request.form.getlist("matching_data[]")
                media_urls = request.form.getlist("media_url[]")
                points = request.form.getlist("points[]")
                question_rows = []
                invalid_question = None
                option_index = true_false_index = short_answer_index = matching_index = 0
                for index, prompt in enumerate(prompts):
                    prompt = prompt.strip()
                    question_type = question_types[index] if index < len(question_types) else ""
                    choices = {}
                    answer = ""
                    extra_data = None
                    try:
                        question_points = float(points[index]) if index < len(points) and points[index] else 1
                    except ValueError:
                        question_points = 0
                    media_url = media_urls[index].strip() if index < len(media_urls) else ""
                    if media_url:
                        if len(media_url) > 1000 or not _valid_https_url(media_url):
                            invalid_question = "Question media must be a valid HTTPS URL under 1000 characters."
                            break
                    if not prompt or question_type not in QUESTION_TYPES or not math.isfinite(question_points) or question_points <= 0:
                        invalid_question = "Every question needs a prompt, supported type, and positive points."
                        break
                    if question_type == "multiple_choice":
                        choices = {key: options[key][option_index].strip() if option_index < len(options[key]) else "" for key in options}
                        answer = answers[option_index].strip().upper() if option_index < len(answers) else ""
                        option_index += 1
                        if any(not value for value in choices.values()) or answer not in choices:
                            invalid_question = "Multiple-choice questions need four choices and a correct choice."
                            break
                    elif question_type == "true_false":
                        choices = {"True": "True", "False": "False"}
                        answer = true_false_answers[true_false_index].strip() if true_false_index < len(true_false_answers) else ""
                        true_false_index += 1
                        if answer not in choices:
                            invalid_question = "Choose True or False as the correct answer."
                            break
                    elif question_type == "short_answer":
                        answer = expected_answers[short_answer_index].strip() if short_answer_index < len(expected_answers) else ""
                        short_answer_index += 1
                        if not answer or len(answer) > 500:
                            invalid_question = "Short-answer questions need an expected answer of at most 500 characters."
                            break
                    elif question_type == "matching":
                        raw_pairs = matching_data[matching_index] if matching_index < len(matching_data) else ""
                        matching_index += 1
                        try:
                            extra_data = _parse_matching_pairs(raw_pairs)
                        except ValueError as error:
                            invalid_question = str(error)
                            break
                    question_rows.append((prompt, question_type, choices, answer, question_points, media_url, extra_data))
                if invalid_question or not question_rows:
                    db.session.rollback()
                    flash(invalid_question or "Add at least one valid question.", "danger")
                    return redirect(url_for("assessments"))
                assessment.max_score = sum(row[4] for row in question_rows)
                for prompt, question_type, choices, answer, question_points, media_url, extra_data in question_rows:
                    assessment.questions.append(AssessmentQuestion(
                        prompt=prompt, question_type=question_type,
                        options_json=json.dumps(extra_data if question_type == "matching" else choices),
                        correct_answer=answer, media_url=media_url or None, points=question_points))
            db.session.commit()
            if is_published:
                assigned_students = target_students if target_mode == "selected" else User.query.filter_by(
                    role="student", class_name=class_name).all()
                recipient_ids = {student.id for student in assigned_students}
                recipient_ids.update(student.parent_id for student in assigned_students if student.parent_id)
                notify_users(
                    recipient_ids,
                    "Academics",
                    f"New {assessment_type.lower()}: {title}",
                    f"{subject} {assessment_type.lower()} assigned. Due: {due_value or 'No deadline set'}.",
                    "/assessments",
                )
                db.session.commit()
            published_message = "Assessment published to selected students." if target_mode == "selected" else "Assessment published to the class."
            flash(published_message if is_published else "Assessment saved as a draft.", "success")
            return redirect(url_for("assessments"))

    if current_user.role in {"master_admin", "head_teacher"}:
        staff_assessments = Assessment.query.order_by(Assessment.created_at.desc()).all()
        classes = sorted({
            (student.trade, student.level, student.class_name)
            for student in User.query.filter(User.role == "student", User.class_name.isnot(None)).all()
            if student.trade and student.level and student.class_name
        })
    elif current_user.role == "teacher":
        staff_assessments = Assessment.query.filter_by(teacher_id=current_user.id).order_by(Assessment.created_at.desc()).all()
        classes = sorted({(None, None, name) for name in teacher_assigned_classes(current_user)}, key=lambda item: item[2].casefold())
    elif current_user.role == "student":
        staff_assessments = [assessment for assessment in Assessment.query.filter_by(
            class_name=current_user.class_name, is_published=True
        ).order_by(Assessment.due_at, Assessment.id.desc()).all()
                             if _assessment_targets_student(assessment, current_user)]
        classes = []
    elif current_user.role == "parent":
        children = linked_children(current_user.id)
        child_classes = {child.class_name for child in children if child.class_name}
        available_assessments = Assessment.query.filter(
            Assessment.class_name.in_(child_classes), Assessment.is_published.is_(True)
        ).order_by(Assessment.due_at, Assessment.id.desc()).all() if child_classes else []
        staff_assessments = [assessment for assessment in available_assessments if any(
            child.class_name == assessment.class_name and _assessment_targets_student(assessment, child)
            for child in children)]
        classes = []
    else:
        staff_assessments = Assessment.query.filter_by(is_published=True).order_by(Assessment.created_at.desc()).all()
        classes = []

    if current_user.role in {"master_admin", "head_teacher"}:
        target_candidates = User.query.filter_by(role="student").order_by(User.class_name, User.full_name).all()
    elif current_user.role == "teacher":
        target_candidates = User.query.filter(
            User.role == "student",
            db.func.lower(User.class_name).in_({name.casefold() for name in teacher_assigned_classes(current_user)}),
        ).order_by(User.class_name, User.full_name).all()
    else:
        target_candidates = []
    class_options = "".join(
        f"<option value='{escape(class_name)}' data-trade='{escape(trade)}' data-level='{escape(level)}'>{escape(class_name)}</option>"
        for trade, level, class_name in classes
    )
    assessment_trade_options = "".join(f"<option value='{escape(trade)}'>{escape(trade)}</option>" for trade in TRADES)
    assessment_level_options = "".join(
        f"<option value='{escape(level)}' data-trade='{escape(trade)}'>{escape(level)}</option>"
        for trade, levels in PROGRAM_LEVELS.items() for level in levels
    )
    target_options = "".join(
        f"<option value='{student.id}' data-class-name='{escape(student.class_name or '')}'>{escape(student.full_name)} Â· {escape(student.class_name or 'No class')} Â· {escape(student.sdms_code or student.username)}</option>"
        for student in target_candidates)
    available_rubrics = GradingRubric.query.order_by(GradingRubric.name).all() if current_user.role == "master_admin" else (
        GradingRubric.query.filter_by(teacher_id=current_user.id).order_by(GradingRubric.name).all()
        if current_user.role == "teacher" else [])
    rubric_options = "<option value=''>No rubric</option>" + "".join(
        f"<option value='{rubric.id}' data-total='{sum(criterion.max_points for criterion in rubric.criteria):g}'>{escape(rubric.name)} Â· {sum(criterion.max_points for criterion in rubric.criteria):g} points</option>"
        for rubric in available_rubrics)
    rubric_section = ""
    if current_user.role in {"master_admin", "head_teacher", "teacher"}:
        rubric_cards = "".join(
            f"<li><strong>{escape(rubric.name)}</strong> Â· {sum(criterion.max_points for criterion in rubric.criteria):g} points Â· {len(rubric.criteria)} criteria</li>"
            for rubric in available_rubrics)
        rubric_section = f"""<section class='panel'><h2>Reusable Grading Rubrics</h2><p class='muted'>Create rubrics once, then attach them to homework for consistent criterion-by-criterion scoring.</p><details><summary>Create a rubric</summary><form method='post' action='{url_for('create_rubric')}' class='row g-3 mt-2'><div class='col-md-6'><label>Rubric name</label><input class='form-control' name='name' maxlength='160' required></div><div class='col-12'><label>Description (optional)</label><textarea class='form-control' name='description' maxlength='2000' rows='2'></textarea></div><div class='col-12'><h3>Criteria</h3><div id='rubric-criteria'><div class='rubric-criterion row g-2 mb-3'><div class='col-md-4'><input class='form-control' name='criterion_title[]' placeholder='Criterion' maxlength='160' required></div><div class='col-md-5'><input class='form-control' name='criterion_description[]' placeholder='What good work demonstrates' maxlength='1000'></div><div class='col-md-2'><input class='form-control' type='number' name='criterion_points[]' min='0.01' step='any' placeholder='Max points' required></div><div class='col-md-1'><button type='button' class='btn btn-outline-danger remove-rubric-criterion'>Ã—</button></div></div></div><template id='rubric-criterion-template'><div class='rubric-criterion row g-2 mb-3'><div class='col-md-4'><input class='form-control' name='criterion_title[]' placeholder='Criterion' maxlength='160' required></div><div class='col-md-5'><input class='form-control' name='criterion_description[]' placeholder='What good work demonstrates' maxlength='1000'></div><div class='col-md-2'><input class='form-control' type='number' name='criterion_points[]' min='0.01' step='any' placeholder='Max points' required></div><div class='col-md-1'><button type='button' class='btn btn-outline-danger remove-rubric-criterion'>Ã—</button></div></div></template><button type='button' class='btn btn-outline-success' id='add-rubric-criterion'>Add criterion</button><button class='btn btn-accent ms-2'>Save reusable rubric</button></div></form></details>{'<ul class="mt-3">' + rubric_cards + '</ul>' if rubric_cards else '<p class="muted mt-3">No rubrics saved yet.</p>'}</section><script>(function(){{const list=document.getElementById('rubric-criteria');const template=document.getElementById('rubric-criterion-template');document.getElementById('add-rubric-criterion').addEventListener('click',()=>list.appendChild(template.content.cloneNode(true)));list.addEventListener('click',event=>{{if(event.target.closest('.remove-rubric-criterion')&&list.querySelectorAll('.rubric-criterion').length>1)event.target.closest('.rubric-criterion').remove();}});}})();</script>"""
    subject_options = "".join(
        f"<option value='{escape(subject)}' data-class='{escape(class_name)}'>{escape(subject)}</option>"
        for class_name, subject in sorted(subjects, key=lambda item: (item[0].casefold(), item[1].casefold()))
    )
    year_start = datetime.date.today().year if datetime.date.today().month >= 9 else datetime.date.today().year - 1
    default_year = f"{year_start}-{year_start + 1}"
    subject_input = (f"<select class='form-select' name='subject' required><option value=''>Choose assigned lesson...</option>{subject_options}</select>"
                     if current_user.role == "teacher" else "<input class='form-control' name='subject' required>")
    teacher_form = ""
    if current_user.role in {"master_admin", "head_teacher", "teacher"}:
        if current_user.role == "teacher":
            teacher_class_options = "".join(
                f"<option value='{escape(name)}' data-trade='{escape(CLASS_TRADE_LEVEL.get(name, ('', ''))[0])}' data-level='{escape(CLASS_TRADE_LEVEL.get(name, ('', ''))[1])}'>{escape(name)}</option>"
                for name in sorted(teacher_assigned_classes(current_user), key=str.casefold)
            )
            class_placeholder = "Choose assigned class..." if teacher_class_options else "No classes assigned"
            class_help = ("Trade and level fill in after you choose a class."
                          if teacher_class_options else
                          "Ask the school administrator to assign a class before creating an assessment.")
            class_disabled = "" if teacher_class_options else " disabled"
            class_input = ("<div class='col-md-4'><label>Trade / Department</label><input class='form-control' id='assessment-trade-display' placeholder='Choose a class to fill this' readonly aria-describedby='assessment-class-help'></div>"
                           "<div class='col-md-4'><label>Level</label><input class='form-control' id='assessment-level-display' placeholder='Choose a class to fill this' readonly aria-describedby='assessment-class-help'></div>"
                           "<input type='hidden' name='trade' id='assessment-trade'><input type='hidden' name='level' id='assessment-level'>"
                           f"<div class='col-md-4'><label>Assigned Class</label><select class='form-select' id='assessment-class' name='class_name' required{class_disabled}><option value=''>{class_placeholder}</option>{teacher_class_options}</select><small class='muted' id='assessment-class-help'>{class_help}</small></div>")
        else:
            class_input = (f"<div class='col-md-4'><label>Trade / Department</label><select class='form-select' id='assessment-trade' name='trade' required><option value=''>Choose trade...</option>{assessment_trade_options}</select></div>"
                           f"<div class='col-md-4'><label>Level</label><select class='form-select' id='assessment-level' name='level' required disabled><option value=''>Choose trade first...</option>{assessment_level_options}</select></div>"
                           f"<div class='col-md-4'><label>Class</label><select class='form-select' id='assessment-class' name='class_name' required disabled><option value=''>Choose level first...</option>{class_options}</select></div>")
        question_editor = """<div class='question-entry border rounded p-3 mb-3'><div class='d-flex justify-content-between align-items-center'><strong>Question</strong><button type='button' class='btn btn-sm btn-outline-danger remove-question'>Remove</button></div><label>Prompt</label><textarea class='form-control mb-2' name='question[]' rows='2' required></textarea><div class='row g-2'><div class='col-md-6'><label>Question type</label><select class='form-select question-type' name='question_type[]'><option value='multiple_choice'>Multiple choice</option><option value='true_false'>True / False</option><option value='open_ended'>Open-ended</option><option value='short_answer'>Short answer</option><option value='matching'>Matching</option></select></div><div class='col-md-3'><label>Points</label><input class='form-control' type='number' min='0.01' step='any' name='points[]' value='1' required></div><div class='col-md-12'><label>Image, chart, or video URL (HTTPS)</label><input class='form-control' type='url' name='media_url[]' maxlength='1000' placeholder='https://...'></div></div><div class='question-config mt-3' data-kind='multiple_choice'><div class='row g-2'><div class='col-md-6'><input class='form-control' name='option_a[]' placeholder='Choice A' required></div><div class='col-md-6'><input class='form-control' name='option_b[]' placeholder='Choice B' required></div><div class='col-md-6'><input class='form-control' name='option_c[]' placeholder='Choice C' required></div><div class='col-md-6'><input class='form-control' name='option_d[]' placeholder='Choice D' required></div><div class='col-md-6'><label>Correct choice</label><select class='form-select' name='correct[]'><option>A</option><option>B</option><option>C</option><option>D</option></select></div></div></div><div class='question-config mt-3 d-none' data-kind='true_false'><label>Correct answer</label><select class='form-select' name='true_false_correct[]'><option>True</option><option>False</option></select></div><div class='question-config mt-3 d-none' data-kind='short_answer'><label>Expected answer (case and spacing are ignored)</label><input class='form-control' name='expected_answer[]' maxlength='500' required></div><div class='question-config mt-3 d-none' data-kind='matching'><label>Matching pairs</label><textarea class='form-control font-monospace' name='matching_data[]' rows='4' placeholder='Term | Match&#10;Term | Match' required></textarea><small class='muted'>Enter at least two pairs, one per line.</small></div></div>"""
        question_script = """<script>(function(){const rows=document.getElementById('question-rows');const template=document.getElementById('question-template');const type=document.getElementById('assessment-type');const onlineToggle=document.getElementById('homework-online');const toggleWrap=document.getElementById('homework-question-toggle');let questionEnabled=false;function syncEntry(entry){const selected=entry.querySelector('.question-type').value;entry.querySelectorAll('.question-config').forEach(config=>{const active=questionEnabled&&config.dataset.kind===selected;config.classList.toggle('d-none',!active);config.querySelectorAll('input,select,textarea').forEach(control=>{if(control.dataset.requiredState===undefined)control.dataset.requiredState=String(control.required);control.disabled=!active;control.required=active&&control.dataset.requiredState==='true';});});}function syncAll(){rows.querySelectorAll('.question-entry').forEach(syncEntry);}function syncType(){const quiz=['Quiz','Test'].includes(type.value);const homework=type.value==='Homework';questionEnabled=quiz||(homework&&onlineToggle.checked);toggleWrap.classList.toggle('d-none',!homework);document.getElementById('assignment-file').classList.toggle('d-none',quiz);document.getElementById('assignment-maximum').classList.toggle('d-none',questionEnabled);document.getElementById('assignment-maximum').querySelector('input').disabled=questionEnabled;document.getElementById('quiz-questions').classList.toggle('d-none',!questionEnabled);const security=document.getElementById('assessment-security-settings');security.querySelectorAll('input').forEach(control=>control.disabled=!quiz);rows.querySelectorAll('.question-entry').forEach(entry=>entry.querySelectorAll('input,select,textarea').forEach(control=>{if(!control.closest('.question-config'))control.disabled=!questionEnabled;}));syncAll();}type.addEventListener('change',syncType);onlineToggle.addEventListener('change',syncType);rows.addEventListener('change',event=>{if(event.target.matches('.question-type'))syncEntry(event.target.closest('.question-entry'));});rows.addEventListener('click',event=>{if(event.target.closest('.remove-question')){const entries=rows.querySelectorAll('.question-entry');if(entries.length>1)event.target.closest('.question-entry').remove();}});document.getElementById('add-question').addEventListener('click',()=>{rows.appendChild(template.content.cloneNode(true));syncAll();});syncType();})();</script>"""
        question_script += """<script>(function(){const mode=document.getElementById('target-mode');const picker=document.getElementById('target-picker');const studentSelect=document.getElementById('target-students');const classSelect=document.getElementById('assessment-class');function syncTargets(){const specific=mode.value==='selected';picker.classList.toggle('d-none',!specific);studentSelect.disabled=!specific;studentSelect.required=specific;Array.from(studentSelect.options).forEach(option=>{const available=!classSelect.value||option.dataset.className===classSelect.value;if(!available)option.selected=false;option.disabled=!available;option.hidden=!available;});}mode.addEventListener('change',syncTargets);classSelect.addEventListener('change',syncTargets);syncTargets();})();</script>"""
        question_script += """<script>(function(){const type=document.getElementById('assessment-type');const picker=document.getElementById('rubric-picker');const rubric=document.getElementById('assessment-rubric');const maximum=document.getElementById('assignment-max-score');const onlineToggle=document.getElementById('homework-online');function syncRubric(){const homework=type.value==='Homework'&&!onlineToggle.checked;picker.classList.toggle('d-none',!homework);if(!homework)rubric.value='';const total=rubric.selectedOptions[0].dataset.total;maximum.readOnly=homework&&Boolean(total);if(maximum.readOnly)maximum.value=total;}type.addEventListener('change',syncRubric);onlineToggle.addEventListener('change',syncRubric);rubric.addEventListener('change',syncRubric);syncRubric();})();</script>"""
        assignment_scope_script = """<script>(function(){const trade=document.getElementById('assessment-trade');const level=document.getElementById('assessment-level');const classSelect=document.getElementById('assessment-class');if(!trade||!level||!classSelect)return;const levels=Array.from(level.querySelectorAll('option[data-trade]'));const classes=Array.from(classSelect.querySelectorAll('option[data-trade]'));function updateClasses(){const selectedTrade=trade.value;const selectedLevel=level.value;classSelect.innerHTML='<option value="">Choose class...</option>';classes.filter(option=>option.dataset.trade===selectedTrade&&option.dataset.level===selectedLevel).forEach(option=>classSelect.appendChild(option.cloneNode(true)));classSelect.disabled=!selectedTrade||!selectedLevel;}function updateLevels(){const selectedTrade=trade.value;level.innerHTML='<option value="">Choose level...</option>';levels.filter(option=>option.dataset.trade===selectedTrade).forEach(option=>level.appendChild(option.cloneNode(true)));level.disabled=!selectedTrade;updateClasses();}trade.addEventListener('change',updateLevels);level.addEventListener('change',updateClasses);updateLevels();})();</script>"""
        teacher_assignment_script = """<script>(function(){const classSelect=document.getElementById('assessment-class');if(!classSelect)return;const trade=document.getElementById('assessment-trade'),level=document.getElementById('assessment-level'),tradeDisplay=document.getElementById('assessment-trade-display'),levelDisplay=document.getElementById('assessment-level-display'),subject=document.querySelector('select[name="subject"]');function sync(){const option=classSelect.selectedOptions[0];trade.value=option?.dataset.trade||'';level.value=option?.dataset.level||'';tradeDisplay.value=trade.value;levelDisplay.value=level.value;if(subject){subject.value='';const matching=[...subject.options].filter(item=>item.dataset.class&&classSelect.value&&item.dataset.class===classSelect.value);[...subject.options].forEach(item=>{if(item.dataset.class)item.hidden=!classSelect.value||item.dataset.class!==classSelect.value;});subject.disabled=!classSelect.value||matching.length===0;subject.options[0].textContent=!classSelect.value?'Choose class first...':matching.length?'Choose assigned lesson...':'No assigned lessons for this class';}}classSelect.addEventListener('change',sync);sync();})();</script>"""
        class_scope_script = teacher_assignment_script if current_user.role == "teacher" else assignment_scope_script
        teacher_form = f"""<section class='panel'><h2>Create Homework, Quiz, or Test</h2><p class='muted'>Add multiple-choice, true / false, open-ended, short-answer, or matching questions. HTTPS images, charts, and video files can be embedded in questions. Homework can be a file assignment or an online question assignment. Objective questions are auto-marked from teacher-provided answer keys; open-ended answers still need teacher review.</p><form method='post' enctype='multipart/form-data' class='row g-3'><div class='col-md-4'><label>Title</label><input class='form-control' name='title' required></div>{class_input}<div class='col-md-4'><label>Subject / Lesson</label>{subject_input}</div><div class='col-md-4'><label>Type</label><select class='form-select' id='assessment-type' name='assessment_type' required><option>Homework</option><option>Quiz</option><option>Test</option></select></div><div class='col-md-3'><label>Academic Year</label><input class='form-control' name='academic_year' value='{default_year}' pattern='[0-9]{{4}}-[0-9]{{4}}'></div><div class='col-md-3'><label>Term</label><select class='form-select' name='term'><option>Term 1</option><option>Term 2</option><option>Term 3</option></select></div><div class='col-md-3'><label>Deadline (optional)</label><input class='form-control' type='datetime-local' name='due_at'></div><div class='col-md-3' id='assignment-maximum'><label>Homework Maximum Score</label><input class='form-control' id='assignment-max-score' type='number' min='0.01' step='any' name='max_score' value='100'></div><div class='col-md-6 d-none' id='rubric-picker'><label>Attach reusable rubric (optional)</label><select class='form-select' id='assessment-rubric' name='rubric_id'>{rubric_options}</select><small class='muted'>When attached, the rubric total sets the homework maximum score.</small></div><div class='col-12'><label>Instructions</label><textarea class='form-control' name='instructions' rows='2'></textarea></div><div class='col-md-6'><label>Assignment image, chart, or video URL (HTTPS)</label><input class='form-control' type='url' name='assessment_media_url' maxlength='1000' placeholder='https://...'></div><div class='col-12' id='assignment-file'><label>Homework file (PDF, Word, PowerPoint, text, or image; optional)</label><input class='form-control' type='file' name='assignment_file' accept='.pdf,.doc,.docx,.odt,.ppt,.pptx,.txt,.rtf,.jpg,.jpeg,.png'></div><div class='col-md-4'><label>Assign audience</label><select class='form-select' id='target-mode' name='target_mode'><option value='class'>Entire class</option><option value='selected'>Selected students / small group</option></select></div><div class='col-12 d-none' id='target-picker'><label>Students to include</label><select class='form-select' id='target-students' name='target_student_ids' multiple size='6'>{target_options}</select><small class='muted'>Use Ctrl or Command to select more than one student.</small></div><div class='col-12 d-none' id='homework-question-toggle'><label><input type='checkbox' id='homework-online' name='homework_online' value='yes'> Add online questions with automatic marking</label></div><div class='col-12 d-none' id='quiz-questions'><div class='row g-3 mb-3' id='assessment-security-settings'><div class='col-md-4'><label>Time limit in minutes (optional)</label><input class='form-control' type='number' name='time_limit_minutes' min='1' max='300' placeholder='No limit'></div><div class='col-md-4'><label class='d-block'>Question order</label><label><input type='checkbox' name='randomize_questions' value='yes'> Randomize for each student</label></div><div class='col-md-4'><label class='d-block'>Focus mode</label><label><input type='checkbox' name='focus_mode' value='yes'> Fullscreen and focus warnings</label></div><div class='col-12 muted'>Focus mode discourages tab switching but cannot lock the browser or prevent other apps from opening.</div></div><h3>Questions</h3><div id='question-rows'>""" + question_editor + f"""</div><template id='question-template'>{question_editor}</template><button type='button' class='btn btn-outline-success' id='add-question'>Add Question</button></div><div class='col-12'><label><input type='checkbox' name='publish' value='yes' checked> Publish and notify the class now</label></div><div class='col-12'><button class='btn btn-accent'>Save and Assign</button></div></form></section>""" + question_script + class_scope_script

    teacher_form = teacher_form.replace("Selected students / small group", "Selected students")
    rows = []
    for assessment in staff_assessments:
        assessment_label = "Homework" if assessment.assessment_type in HOMEWORK_TYPES else assessment.assessment_type
        assessment_media_html = _question_media_html(assessment.media_url)
        rubric_criteria_html = _rubric_criteria_html(assessment)
        security_labels = []
        if assessment.time_limit_minutes:
            security_labels.append(f"Time limit: {assessment.time_limit_minutes} minutes")
        if assessment.randomize_questions:
            security_labels.append("Questions randomized")
        if assessment.focus_mode:
            security_labels.append("Focus mode")
        security_html = "<p class='muted'>" + " Â· ".join(escape(label) for label in security_labels) + "</p>" if security_labels else ""
        if assessment.target_student_ids:
            try:
                target_ids = [int(student_id) for student_id in json.loads(assessment.target_student_ids)]
            except (TypeError, ValueError, json.JSONDecodeError):
                target_ids = []
            target_names = [student.full_name for student in User.query.filter(User.id.in_(target_ids)).all()] if target_ids else []
            audience_text = "Selected students: " + (", ".join(target_names) if target_names else "No valid students")
        else:
            audience_text = "Entire class"
        if current_user.role in {"master_admin", "head_teacher", "teacher"}:
            action_html = ""
            if assessment.attachment_path:
                action_html += f" <a class='btn btn-sm btn-outline-success' href='{url_for('assessment_file', assessment_id=assessment.id)}'>Download file</a>"
            submissions = AssessmentSubmission.query.filter_by(assessment_id=assessment.id).order_by(AssessmentSubmission.submitted_at.desc()).all()
            submission_rows = []
            for submission in submissions:
                score_text = f"{submission.score:g} / {assessment.max_score:g}" if submission.is_graded and submission.score is not None else "Awaiting teacher review"
                if not submission.is_graded and submission.auto_score is not None:
                    score_text += f" (auto-marked points: {submission.auto_score:g} / {assessment.max_score:g})"
                if submission.focus_warnings:
                    score_text += f" Â· focus warnings: {submission.focus_warnings}"
                student_file = (f" <a href='{url_for('assessment_submission_file', assessment_id=assessment.id, submission_id=submission.id)}'>Download submission</a>"
                                if submission.attachment_path else "")
                rubric_results = _rubric_results_html(assessment, submission)
                response_details = ""
                if assessment.questions and submission.answers_json:
                    answers = json.loads(submission.answers_json)
                    response_items = []
                    for question in assessment.questions:
                        response = answers.get(str(question.id), "")
                        if question.question_type == "matching" and isinstance(response, dict):
                            pairs = json.loads(question.options_json or "[]")
                            response = "; ".join(
                                f"{pair['term']} â†’ {response.get(str(index), '')}"
                                for index, pair in enumerate(pairs))
                        answer_key = ""
                        if question.question_type in {"multiple_choice", "true_false", "short_answer"}:
                            answer_key = f" <small>(expected: {escape(question.correct_answer)})</small>"
                        elif question.question_type == "matching":
                            pairs = json.loads(question.options_json or "[]")
                            answer_key = " <small>(key: " + escape("; ".join(
                                f"{pair['term']} â†’ {pair['match']}" for pair in pairs)) + ")</small>"
                        response_items.append(
                            f"<li><strong>{escape(question.prompt)}</strong>: {escape(str(response))}{answer_key}</li>")
                    response_details = "<details class='mt-2'><summary>View student responses</summary><ul>" + "".join(response_items) + "</ul></details>"
                grading = ""
                needs_manual_grade = assessment.assessment_type in HOMEWORK_TYPES or any(
                    question.question_type == "open_ended" for question in assessment.questions)
                if needs_manual_grade and not submission.is_graded:
                    if assessment.rubric and assessment.rubric.criteria:
                        rubric_inputs = "".join(
                            f"<div class='row align-items-center g-2 mb-2'><label class='col-md-5'><strong>{escape(criterion.title)}</strong> ({criterion.max_points:g} points)<small class='d-block muted'>{escape(criterion.description or '')}</small></label><div class='col-md-2'><input class='form-control form-control-sm' type='number' name='rubric_score_{criterion.id}' min='0' max='{criterion.max_points:g}' step='any' placeholder='Score' required></div><div class='col-md-5'><input class='form-control form-control-sm' name='rubric_comment_{criterion.id}' maxlength='1000' placeholder='Criterion feedback'></div></div>"
                            for criterion in assessment.rubric.criteria)
                        grading = (f"<form method='post' action='{url_for('grade_assessment', assessment_id=assessment.id, student_id=submission.student_id)}' class='mt-3'><strong>Score each rubric criterion</strong>{rubric_inputs}"
                                   "<input class='form-control form-control-sm mb-2' name='feedback' placeholder='Overall feedback'><button class='btn btn-sm btn-accent'>Save Rubric Scores</button></form>")
                    else:
                        grading = (f"<form method='post' action='{url_for('grade_assessment', assessment_id=assessment.id, student_id=submission.student_id)}' class='d-flex gap-2 mt-2'>"
                                  f"<input type='number' class='form-control form-control-sm' name='score' min='0' max='{assessment.max_score}' step='any' placeholder='Score' required>"
                                  "<input class='form-control form-control-sm' name='feedback' placeholder='Feedback'><button class='btn btn-sm btn-accent'>Record Score</button></form>")
                submission_rows.append(f"<tr><td>{escape(submission.student.full_name)}</td><td>{escape(submission.submitted_at)}</td><td>{score_text}{student_file}{response_details}{rubric_results}{grading}</td></tr>")
            sub_table = ("<div class='table-responsive mt-3'><table class='table'><tr><th>Student</th><th>Submitted</th><th>Result / Action</th></tr>" +
                         "".join(submission_rows) + "</table></div>") if submission_rows else "<p class='muted mt-2'>No student submissions yet.</p>"
            rows.append(f"<article class='panel'><h3>{escape(assessment.title)} <span class='pill'>{escape(assessment_label)}</span></h3>"
                        f"<p><strong>Class:</strong> {escape(assessment.class_name)} Ã‚Â· <strong>Audience:</strong> {escape(audience_text)} Ã‚Â· <strong>Subject:</strong> {escape(assessment.subject)} Ã‚Â· <strong>Due:</strong> {escape(assessment.due_at or 'No deadline')}</p>"
                        f"{assessment_media_html}<p>{escape(assessment.instructions or '')}</p>{security_html}{rubric_criteria_html}<p><strong>Maximum score:</strong> {assessment.max_score:g}{action_html}</p>{sub_table}</article>")
        else:
            child_list = [current_user] if current_user.role == "student" else [
                child for child in linked_children(current_user.id) if child.class_name == assessment.class_name]
            for student in child_list:
                if not _student_can_access(student, assessment) or not _assessment_targets_student(assessment, student):
                    continue
                submission = AssessmentSubmission.query.filter_by(assessment_id=assessment.id, student_id=student.id).first()
                attempt = None
                remaining_seconds = None
                if (current_user.role == "student" and not submission
                        and (assessment.time_limit_minutes or assessment.randomize_questions)
                        and assessment.questions):
                    attempt = AssessmentAttempt.query.filter_by(
                        assessment_id=assessment.id, student_id=student.id).first()
                    deadline = _attempt_deadline(assessment, attempt)
                    if deadline:
                        remaining_seconds = max(0, math.ceil((deadline - datetime.datetime.now()).total_seconds()))
                        if remaining_seconds == 0 and datetime.datetime.now() > deadline + datetime.timedelta(seconds=15):
                            submission = _record_timed_out_attempt(assessment, student)
                result = "Not submitted"
                rubric_results = ""
                if submission:
                    result = f"Score: {submission.score:g} / {assessment.max_score:g}" if submission.is_graded and submission.score is not None else "Submitted Ã‚Â· awaiting teacher score"
                    if submission.feedback:
                        result += f" Ã‚Â· {escape(submission.feedback)}"
                    rubric_results = _rubric_results_html(assessment, submission)
                action_html = ""
                if current_user.role == "student" and not submission:
                    if assessment.due_at and assessment.due_at < now_string():
                        action_html = "<span class='muted'>Deadline passed.</span>"
                    elif (assessment.time_limit_minutes or assessment.randomize_questions) and not attempt:
                        action_html = (f"<form method='post' action='{url_for('start_assessment', assessment_id=assessment.id)}' class='mt-2'>"
                                       "<p>This timed assessment starts when you press the button.</p><button class='btn btn-accent'>Start Timed Assessment</button></form>")
                    elif assessment.questions:
                        question_html = []
                        question_order = list(assessment.questions)
                        if attempt and attempt.question_order_json:
                            try:
                                saved_order = [int(question_id) for question_id in json.loads(attempt.question_order_json)]
                                by_id = {question.id: question for question in question_order}
                                question_order = [by_id[question_id] for question_id in saved_order if question_id in by_id]
                                question_order += [question for question in assessment.questions if question.id not in saved_order]
                            except (TypeError, ValueError, json.JSONDecodeError):
                                pass
                        elif assessment.randomize_questions:
                            random.SystemRandom().shuffle(question_order)
                        for question in question_order:
                            options = json.loads(question.options_json or "{}")
                            media_html = _question_media_html(question.media_url)
                            answer_html = ""
                            if question.question_type in {"multiple_choice", "true_false"}:
                                answer_html = "".join(
                                    f"<label class='d-block'><input type='radio' name='answer_{question.id}' value='{escape(key)}' required> {escape(key)}. {escape(value)}</label>"
                                    for key, value in options.items())
                            elif question.question_type == "short_answer":
                                answer_html = f"<input class='form-control' name='answer_{question.id}' maxlength='500' required aria-label='Answer to {escape(question.prompt)}'>"
                            elif question.question_type == "open_ended":
                                answer_html = f"<textarea class='form-control' name='answer_{question.id}' rows='4' maxlength='10000' required aria-label='Answer to {escape(question.prompt)}'></textarea>"
                            elif question.question_type == "matching":
                                pairs = options if isinstance(options, list) else []
                                matches = [pair["match"] for pair in pairs]
                                random.SystemRandom().shuffle(matches)
                                match_options = "".join(
                                    f"<option value='{escape(match)}'>{escape(match)}</option>" for match in matches)
                                answer_html = "".join(
                                    f"<label class='row align-items-center mb-2'><span class='col-md-5'>{escape(pair['term'])}</span><span class='col-md-7'><select class='form-select' name='match_{question.id}_{index}' required><option value=''>Choose a match...</option>{match_options}</select></span></label>"
                                    for index, pair in enumerate(pairs))
                            question_html.append(f"<fieldset class='mb-4 border rounded p-3'><legend>{escape(question.prompt)} ({question.points:g} points)</legend>{media_html}{answer_html}</fieldset>")
                        submit_label = ("Submit Assignment" if assessment.assessment_type == "Homework" else
                                        "Submit Test" if assessment.assessment_type == "Test" else "Submit Quiz")
                        timer_html = (f"<p class='pill' id='assessment-timer-{assessment.id}' data-seconds='{remaining_seconds}'>Time remaining: --:--</p>"
                                      if remaining_seconds is not None else "")
                        focus_html = (f"<div class='mb-3'><button type='button' class='btn btn-outline-success' id='focus-start-{assessment.id}'>Enter focus mode</button> <span id='focus-status-{assessment.id}' class='muted'>Fullscreen and tab-switch warnings are enabled.</span></div>"
                                      if assessment.focus_mode else "")
                        action_html = (f"{timer_html}{focus_html}<form id='assessment-form-{assessment.id}' method='post' action='{url_for('submit_assessment', assessment_id=assessment.id)}'>"
                                       f"{''.join(question_html)}<button class='btn btn-accent'>{submit_label}</button></form>")
                        if remaining_seconds is not None or assessment.focus_mode:
                            script_parts = []
                            if remaining_seconds is not None:
                                script_parts.append(
                                    f"(function(){{const timer=document.getElementById('assessment-timer-{assessment.id}');const form=document.getElementById('assessment-form-{assessment.id}');let remaining=Number(timer.dataset.seconds);let interval=null;function tick(){{const mins=Math.floor(remaining/60);const secs=remaining%60;timer.textContent='Time remaining: '+mins+':'+String(secs).padStart(2,'0');if(remaining<=0){{if(interval)clearInterval(interval);timer.textContent='Time limit reached. Submittingâ€¦';form.submit();return;}}remaining--;}}interval=setInterval(tick,1000);tick();}})();")
                            if assessment.focus_mode:
                                script_parts.append(
                                    f"(function(){{const form=document.getElementById('assessment-form-{assessment.id}');const button=document.getElementById('focus-start-{assessment.id}');const status=document.getElementById('focus-status-{assessment.id}');const warnings=document.createElement('input');warnings.type='hidden';warnings.name='focus_warnings';form.appendChild(warnings);const controls=form.querySelectorAll('input,select,textarea,button[type=submit]');let active=false;let strikes=0;controls.forEach(control=>control.disabled=true);function warn(){{strikes++;warnings.value=String(strikes);status.textContent='Focus warning '+strikes+'. Return to the assessment.';}}button.addEventListener('click',async()=>{{active=true;try{{await document.documentElement.requestFullscreen();status.textContent='Focus mode active. Leaving the page is recorded as a focus warning.';}}catch(error){{status.textContent='Fullscreen was unavailable; focus warnings are still active.';}}controls.forEach(control=>control.disabled=false);button.disabled=true;}});document.addEventListener('visibilitychange',()=>{{if(active&&document.hidden)warn();}});document.addEventListener('fullscreenchange',()=>{{if(active&&!document.fullscreenElement&&!document.hidden)warn();}});document.addEventListener('contextmenu',event=>{{if(active)event.preventDefault();}});document.addEventListener('copy',event=>{{if(active)event.preventDefault();}});document.addEventListener('paste',event=>{{if(active)event.preventDefault();}});}})();")
                            action_html += "<script>" + "".join(script_parts) + "</script>"
                    else:
                        action_html = (f"<form method='post' enctype='multipart/form-data' action='{url_for('submit_assessment', assessment_id=assessment.id)}' class='mt-2'>"
                                       "<label>Upload your completed homework</label><input class='form-control' type='file' name='submission_file' required accept='.pdf,.doc,.docx,.odt,.ppt,.pptx,.txt,.rtf,.jpg,.jpeg,.png'><button class='btn btn-accent mt-2'>Submit Homework</button></form>")
                file_link = f"<p><a href='{url_for('assessment_file', assessment_id=assessment.id)}'>Download assignment file: {escape(assessment.attachment_name)}</a></p>" if assessment.attachment_path else ""
                rows.append(f"<article class='panel'><h3>{escape(assessment.title)} <span class='pill'>{escape(assessment_label)}</span></h3>"
                            f"<p><strong>Student:</strong> {escape(student.full_name)} Ã‚Â· <strong>Subject:</strong> {escape(assessment.subject)} Ã‚Â· <strong>Due:</strong> {escape(assessment.due_at or 'No deadline')}</p>"
                            f"{assessment_media_html}<p>{escape(assessment.instructions or '')}</p>{security_html}{rubric_criteria_html}{file_link}<p><strong>Result:</strong> {result}</p>{rubric_results}{action_html}</article>")
    create_note = "<section class='panel'><h1>e-Learning <span class='accent'>Assessments</span></h1><p class='muted'>Teachers assign homework, quizzes, and tests here. Answer-key questions are auto-marked in online homework, quizzes, and tests; teachers review open-ended answers and file-based homework.</p></section>"
    analytics_section = _assessment_analytics_html(staff_assessments) if current_user.role in {"master_admin", "head_teacher", "teacher"} else ""
    body = create_note + analytics_section + rubric_section + teacher_form + ("".join(rows) or "<section class='panel muted'>No assessments are available.</section>")
    return page("e-Learning Assessments", body, "teacher")


def start_assessment(assessment_id):
    if current_user.role != "student":
        abort(403)
    assessment = db.session.get(Assessment, assessment_id) or abort(404)
    if (not assessment.is_published or assessment.assessment_type not in (QUESTION_ASSESSMENT_TYPES | HOMEWORK_TYPES)
            or not assessment.questions
            or current_user.class_name != assessment.class_name
            or not _assessment_targets_student(assessment, current_user)):
        abort(403)
    if assessment.due_at and assessment.due_at < now_string():
        flash("The deadline for this assessment has passed.", "danger")
        return redirect(url_for("assessments"))
    if AssessmentSubmission.query.filter_by(assessment_id=assessment.id, student_id=current_user.id).first():
        abort(409, description="You have already submitted this assessment.")
    attempt = AssessmentAttempt.query.filter_by(
        assessment_id=assessment.id, student_id=current_user.id).first()
    if not attempt:
        question_order = list(assessment.questions)
        if assessment.randomize_questions:
            random.SystemRandom().shuffle(question_order)
        attempt = AssessmentAttempt(
            assessment_id=assessment.id, student_id=current_user.id, started_at=now_string(),
            question_order_json=json.dumps([question.id for question in question_order]))
        db.session.add(attempt)
        db.session.commit()
    return redirect(url_for("assessments"))


def submit_assessment(assessment_id):
    if current_user.role != "student":
        abort(403)
    assessment = db.session.get(Assessment, assessment_id) or abort(404)
    if (not assessment.is_published or current_user.class_name != assessment.class_name
            or not _assessment_targets_student(assessment, current_user)):
        abort(403)
    if assessment.due_at and assessment.due_at < now_string():
        flash("The deadline for this assessment has passed.", "danger")
        return redirect(url_for("assessments"))
    if AssessmentSubmission.query.filter_by(assessment_id=assessment.id, student_id=current_user.id).first():
        abort(409, description="You have already submitted this assessment.")
    timed_submission = False
    attempt = AssessmentAttempt.query.filter_by(
        assessment_id=assessment.id, student_id=current_user.id).first()
    if (assessment.time_limit_minutes or assessment.randomize_questions) and not attempt:
        abort(403, description="Start the assessment before submitting answers.")
    if assessment.time_limit_minutes:
        deadline = _attempt_deadline(assessment, attempt)
        if deadline and datetime.datetime.now() > deadline + datetime.timedelta(seconds=15):
            _record_timed_out_attempt(assessment, current_user)
            flash("Time expired. The assessment was recorded with a score of zero.", "danger")
            return redirect(url_for("assessments"))
        timed_submission = bool(deadline and datetime.datetime.now() >= deadline)
    if assessment.questions:
        answers = {}
        score = 0.0
        has_open_response = False
        try:
            focus_warnings = max(0, min(100, int(request.form.get("focus_warnings", "0"))))
        except ValueError:
            focus_warnings = 0
        for question in assessment.questions:
            options = json.loads(question.options_json or "{}")
            if question.question_type in {"multiple_choice", "true_false"}:
                answer = request.form.get(f"answer_{question.id}", "")
                if answer not in options:
                    if not timed_submission:
                        abort(400)
                    answer = ""
                answers[str(question.id)] = answer
                correct = answer == question.correct_answer
            elif question.question_type == "short_answer":
                answer = request.form.get(f"answer_{question.id}", "").strip()
                if len(answer) > 500 or (not answer and not timed_submission):
                    abort(400)
                answers[str(question.id)] = answer
                normalized_answer = " ".join(answer.casefold().split())
                normalized_expected = " ".join(question.correct_answer.casefold().split())
                correct = normalized_answer == normalized_expected
            elif question.question_type == "matching":
                pairs = options if isinstance(options, list) else []
                selected_matches = [request.form.get(f"match_{question.id}_{index}", "") for index in range(len(pairs))]
                if not pairs or any(not match for match in selected_matches):
                    if not timed_submission:
                        abort(400)
                answers[str(question.id)] = {str(index): match for index, match in enumerate(selected_matches)}
                correct = len(selected_matches) == len(pairs) and all(
                    selected_matches[index] == pair["match"] for index, pair in enumerate(pairs))
            elif question.question_type == "open_ended":
                answer = request.form.get(f"answer_{question.id}", "").strip()
                if len(answer) > 10000 or (not answer and not timed_submission):
                    abort(400)
                answers[str(question.id)] = answer
                correct = False
                has_open_response = has_open_response or bool(answer)
            else:
                abort(400)
            if correct:
                score += question.points
        submission = AssessmentSubmission(assessment_id=assessment.id, student_id=current_user.id,
                                          submitted_at=now_string(), answers_json=json.dumps(answers),
                                          score=score, auto_score=score, focus_warnings=focus_warnings,
                                          is_graded=not has_open_response)
        db.session.add(submission)
        if not has_open_response:
            result_name = "Homework assignment" if assessment.assessment_type == "Homework" else (
                "Test" if assessment.assessment_type == "Test" else "Quiz")
            notify_student_and_parent(current_user, "Academics", f"{result_name} marked: {assessment.title}",
                                      f"Your score is {score:g} / {assessment.max_score:g}.", "/assessments")
    else:
        path, name = _store_upload(request.files.get("submission_file"), "assessment_submissions",
                                   ALLOWED_ASSIGNMENT_EXTENSIONS)
        if not path:
            flash("Choose a file to submit your assignment.", "danger")
            return redirect(url_for("assessments"))
        submission = AssessmentSubmission(assessment_id=assessment.id, student_id=current_user.id,
                                          submitted_at=now_string(), attachment_path=path,
                                          attachment_name=name, is_graded=False)
        db.session.add(submission)
    db.session.commit()
    if assessment.questions:
        if has_open_response:
            flash(f"{assessment.assessment_type} submitted; your teacher will review the open-ended answer.", "success")
        else:
            flash(f"{assessment.assessment_type} submitted and marked.", "success")
    else:
        flash("Homework submitted.", "success")
    return redirect(url_for("assessments"))


def grade_assessment(assessment_id, student_id):
    assessment = _assessment_for_staff(assessment_id)
    submission = AssessmentSubmission.query.filter_by(assessment_id=assessment.id, student_id=student_id).first() or abort(404)
    has_open_response = any(question.question_type == "open_ended" for question in assessment.questions)
    if assessment.assessment_type not in HOMEWORK_TYPES and not (
        assessment.assessment_type in QUESTION_ASSESSMENT_TYPES and has_open_response
    ):
        abort(400)
    if submission.is_graded:
        abort(409, description="This submission has already been graded.")
    rubric_results = {}
    if assessment.rubric and assessment.rubric.criteria:
        score = 0.0
        for criterion in assessment.rubric.criteria:
            try:
                criterion_score = float(request.form.get(f"rubric_score_{criterion.id}", ""))
            except ValueError:
                abort(400)
            if not math.isfinite(criterion_score) or criterion_score < 0 or criterion_score > criterion.max_points:
                abort(400)
            comment = request.form.get(f"rubric_comment_{criterion.id}", "").strip()
            if len(comment) > 1000:
                abort(400)
            rubric_results[str(criterion.id)] = {"score": criterion_score, "comment": comment}
            score += criterion_score
        submission.rubric_results_json = json.dumps(rubric_results)
    else:
        try:
            score = float(request.form.get("score", ""))
        except ValueError:
            abort(400)
    if not math.isfinite(score) or score < 0 or score > assessment.max_score:
        abort(400)
    submission.score = score
    submission.feedback = request.form.get("feedback", "").strip() or None
    submission.is_graded = True
    result_name = "Homework" if assessment.assessment_type in HOMEWORK_TYPES else assessment.assessment_type
    notify_student_and_parent(submission.student, "Academics", f"{result_name} scored: {assessment.title}",
                              f"Your score is {score:g} / {assessment.max_score:g}. {submission.feedback or ''}".strip(),
                              "/assessments")
    db.session.commit()
    flash(f"{result_name} score recorded.", "success")
    return redirect(url_for("assessments"))


def assessment_file(assessment_id):
    assessment = db.session.get(Assessment, assessment_id) or abort(404)
    if current_user.role in {"master_admin", "head_teacher", "teacher"}:
        _assessment_for_staff(assessment_id)
    elif current_user.role == "parent":
        if not assessment.is_published or not any(
            _student_can_access(child, assessment) and _assessment_targets_student(assessment, child)
            for child in linked_children(current_user.id)
        ):
            abort(403)
    elif current_user.role == "student":
        if (not assessment.is_published or current_user.class_name != assessment.class_name
                or not _assessment_targets_student(assessment, current_user)):
            abort(403)
    else:
        abort(403)
    if not assessment.attachment_path:
        abort(404)
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], assessment.attachment_path)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=assessment.attachment_name)


def assessment_submission_file(assessment_id, submission_id):
    assessment = db.session.get(Assessment, assessment_id) or abort(404)
    submission = AssessmentSubmission.query.filter_by(id=submission_id, assessment_id=assessment.id).first() or abort(404)
    if current_user.role in {"master_admin", "head_teacher", "teacher"}:
        _assessment_for_staff(assessment_id)
    elif current_user.role == "student":
        if (current_user.id != submission.student_id or not _student_can_access(current_user, assessment)
                or not _assessment_targets_student(assessment, current_user)):
            abort(403)
    elif current_user.role == "parent":
        if (submission.student.parent_id != current_user.id
                or not _student_can_access(submission.student, assessment)
                or not _assessment_targets_student(assessment, submission.student)):
            abort(403)
    else:
        abort(403)
    if not submission.attachment_path:
        abort(404)
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], submission.attachment_path)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=submission.attachment_name)


def register_routes(app):
    app.add_url_rule('/assessments', endpoint='assessments', view_func=permission_for('assessments')(assessments), methods=['GET', 'POST'])
    app.add_url_rule('/assessments/rubrics', endpoint='create_rubric', view_func=permission_for('create_rubric')(create_rubric), methods=['POST'])
    app.add_url_rule('/assessments/<int:assessment_id>/start', endpoint='start_assessment', view_func=permission_for('start_assessment')(start_assessment), methods=['POST'])
    app.add_url_rule('/assessments/<int:assessment_id>/submit', endpoint='submit_assessment', view_func=permission_for('submit_assessment')(submit_assessment), methods=['POST'])
    app.add_url_rule('/assessments/<int:assessment_id>/grade/<int:student_id>', endpoint='grade_assessment', view_func=permission_for('grade_assessment')(grade_assessment), methods=['POST'])
    app.add_url_rule('/assessments/<int:assessment_id>/file', endpoint='assessment_file', view_func=permission_for('assessment_file')(assessment_file), methods=['GET'])
    app.add_url_rule('/assessments/<int:assessment_id>/submissions/<int:submission_id>/file', endpoint='assessment_submission_file', view_func=permission_for('assessment_submission_file')(assessment_submission_file), methods=['GET'])

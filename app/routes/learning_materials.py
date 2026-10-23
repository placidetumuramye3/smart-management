import math
import os
import random
import secrets
from urllib.parse import urlsplit

from flask import abort, current_app, flash, redirect, request, send_file, url_for
from flask_login import current_user
from markupsafe import escape
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import LearningMaterial, TimetableEntry, User
from app.services.enrollment_service import current_enrollment
from app.utils.decorators import permission_for
from app.utils.helpers import (guardian_can_access_student, guardian_ids_for_student,
                               linked_children, now_string, teacher_assignments)
from app.utils.notifications import notify_users
from app.utils.rendering import page


ALLOWED_MATERIAL_EXTENSIONS = {
    "pdf", "doc", "docx", "odt", "ppt", "pptx", "xls", "xlsx", "csv", "txt", "rtf",
    "jpg", "jpeg", "png", "webp", "mp3", "mp4", "wav", "ogg", "m4a", "aac", "webm", "ogv", "glb"
}
RESOURCE_TYPES = (
    "Digital Textbook", "Curated Reading", "Lecture Notes", "E-text",
    "Standards Guide", "Reference Notes", "Mind Map", "Timeline", "Chart", "Infographic",
    "Educational Video", "Podcast / Audio", "Virtual Simulation", "3D Model",
    "Flashcards", "Drag-and-Drop Activity", "Self-Assessment Quiz",
)
OFFICE_ROLES = {"master_admin", "head_teacher", "secretary"}
SCHEMATIC_TYPES = {"Mind Map", "Timeline", "Chart", "Infographic"}
ACTIVITY_TYPES = {"Flashcards", "Drag-and-Drop Activity", "Self-Assessment Quiz"}
VIDEO_MIME_TYPES = {"mp4": "video/mp4", "webm": "video/webm", "ogv": "video/ogg"}
AUDIO_MIME_TYPES = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav", "ogg": "audio/ogg", "aac": "audio/aac"}


def _parse_activity(resource_type, data):
    lines = [line.strip() for line in data.splitlines() if line.strip()]
    entries = []
    for line in lines:
        if resource_type in {"Flashcards", "Drag-and-Drop Activity"}:
            parts = [part.strip() for part in line.split("|", 1)]
            if len(parts) != 2 or not all(parts):
                kind = "Front | Back" if resource_type == "Flashcards" else "Term | Definition"
                raise ValueError(f"Enter one item per line as {kind}.")
            entries.append({"front": parts[0], "back": parts[1]})
        else:
            parts = [part.strip() for part in line.split("|", 6)]
            if len(parts) < 6 or not all(parts[:5]) or parts[5].upper() not in {"A", "B", "C", "D"}:
                raise ValueError("Enter each quiz row as Question | Option A | Option B | Option C | Option D | Correct letter | Optional explanation.")
            entries.append({"question": parts[0], "options": parts[1:5], "correct": parts[5].upper(),
                            "explanation": parts[6] if len(parts) > 6 else ""})
    if not entries:
        raise ValueError("Enter at least one practice item.")
    return entries


def _render_activity(material):
    if material.resource_type not in ACTIVITY_TYPES or not material.activity_data:
        return ""
    try:
        entries = _parse_activity(material.resource_type, material.activity_data)
    except ValueError:
        return "<p class='muted'>This activity needs to be updated before it can be used.</p>"
    if material.resource_type == "Flashcards":
        cards = "".join(
            f"<button type='button' class='quick-link flashcard mb-2 text-start' aria-pressed='false'><span class='flash-front'><strong>{escape(entry['front'])}</strong></span><span class='flash-back d-none'>{escape(entry['back'])}</span></button>"
            for entry in entries)
        return f"<div class='learning-activity flashcard-activity mt-3'><p class='muted'>Select a card to reveal its answer.</p>{cards}</div>"
    if material.resource_type == "Drag-and-Drop Activity":
        terms = list(enumerate(entries))
        random.SystemRandom().shuffle(terms)
        term_cards = "".join(f"<button type='button' class='btn btn-outline-success drag-term' draggable='true' data-term-id='{index}'>{escape(entry['front'])}</button>" for index, entry in terms)
        targets = "".join(f"<div class='border rounded p-3 mb-2 drop-zone' data-correct='{index}'><p class='mb-2'>{escape(entry['back'])}</p><button type='button' class='btn btn-sm btn-outline-secondary drop-label'>Drop or select a matching term</button></div>" for index, entry in enumerate(entries))
        return f"<div class='learning-activity drag-activity mt-3'><p class='muted'>Drag a term onto its definition, or select a term and then its definition.</p><div class='d-flex gap-2 flex-wrap term-pool mb-3'>{term_cards}</div><div class='matching-targets'>{targets}</div><div class='d-flex gap-2 flex-wrap'><button type='button' class='btn btn-accent check-matches'>Check matches</button><button type='button' class='btn btn-outline-success reset-matches'>Reset</button></div><p class='activity-result mt-2' aria-live='polite'></p></div>"
    questions = []
    for index, entry in enumerate(entries):
        options = "".join(f"<label class='d-block'><input type='radio' name='self_{material.id}_{index}' value='{letter}'> {letter}. {escape(option)}</label>" for letter, option in zip("ABCD", entry["options"]))
        explanation = f" {escape(entry['explanation'])}" if entry["explanation"] else ""
        feedback = f"<p class='self-feedback d-none mt-2'><strong>Correct answer: {entry['correct']}.</strong>{explanation}</p>"
        questions.append(f"<fieldset class='self-question border rounded p-3 mb-3' data-correct='{entry['correct']}'><legend>{escape(entry['question'])}</legend>{options}{feedback}</fieldset>")
    return f"<form class='learning-activity self-quiz mt-3' data-quiz-id='{material.id}'>{''.join(questions)}<button type='button' class='btn btn-accent check-self-quiz'>Check answers</button> <button type='button' class='btn btn-outline-success reset-self-quiz'>Try again</button><p class='activity-result mt-2' aria-live='polite'></p></form>"


def _parse_schematic(resource_type, data):
    lines = [line.strip() for line in data.splitlines() if line.strip()]
    if resource_type == "Mind Map":
        paths = [[part.strip() for part in line.split(">") if part.strip()] for line in lines]
        if not paths or any(len(path) < 2 for path in paths):
            raise ValueError("Enter one mind-map path per line using Central Topic > Branch > Detail.")
        root = paths[0][0]
        if any(path[0] != root for path in paths):
            raise ValueError("All mind-map paths must start with the same central topic.")
        tree = {"label": root, "children": {}}
        for path in paths:
            node = tree
            for label in path[1:]:
                node = node["children"].setdefault(label, {"label": label, "children": {}})
        return tree
    entries = []
    for line in lines:
        parts = [part.strip() for part in line.split("|", 2)]
        if resource_type == "Timeline":
            if len(parts) < 2 or not parts[0] or not parts[1]:
                raise ValueError("Enter timeline rows as Date | Event | Details.")
            entries.append({"date": parts[0], "label": parts[1], "detail": parts[2] if len(parts) > 2 else ""})
        elif resource_type == "Chart":
            if len(parts) < 2 or not parts[0]:
                raise ValueError("Enter chart rows as Label | Number.")
            try:
                value = float(parts[1])
            except ValueError:
                raise ValueError("Each chart value must be a non-negative number.") from None
            if not math.isfinite(value) or value < 0:
                raise ValueError("Chart values must be non-negative numbers.")
            entries.append({"label": parts[0], "value": value})
        else:
            if len(parts) < 2 or not parts[0] or not parts[1]:
                raise ValueError("Enter infographic rows as Heading | Explanation.")
            entries.append({"label": parts[0], "detail": parts[1]})
    if not entries:
        raise ValueError("Enter at least one schematic item.")
    return entries


def _mind_map_html(node):
    children = "".join(_mind_map_html(child) for child in node["children"].values())
    if children:
        return f"<li><details open><summary>{escape(node['label'])}</summary><ul>{children}</ul></details></li>"
    return f"<li><span class='mind-map-leaf'>{escape(node['label'])}</span></li>"


def _render_schematic(resource_type, data):
    if resource_type not in SCHEMATIC_TYPES or not data:
        return ""
    try:
        parsed = _parse_schematic(resource_type, data)
    except ValueError:
        return "<p class='muted'>This schematic needs to be updated before it can be displayed.</p>"
    if resource_type == "Mind Map":
        controls = "<div class='d-flex gap-2 mb-3'><button class='btn btn-sm btn-outline-success' type='button' data-map-action='expand'>Expand all</button><button class='btn btn-sm btn-outline-success' type='button' data-map-action='collapse'>Collapse all</button></div>"
        return f"<div class='schematic mind-map'>{controls}<ul>{_mind_map_html(parsed)}</ul></div>"
    if resource_type == "Timeline":
        items = "".join(f"<article class='timeline-item'><span class='timeline-dot'></span><strong>{escape(item['date'])} Â· {escape(item['label'])}</strong><small>{escape(item['detail'])}</small></article>" for item in parsed)
        return f"<div class='schematic timeline mt-3'>{items}</div>"
    if resource_type == "Chart":
        maximum = max((item["value"] for item in parsed), default=0) or 1
        bars = "".join(f"<div class='chart-row mb-3'><div class='d-flex justify-content-between gap-2'><strong>{escape(item['label'])}</strong><span>{item['value']:g}</span></div><div class='progress' role='img' aria-label='{escape(item['label'])}: {item['value']:g}'><div class='progress-bar bg-success' style='width:{max(2, item['value'] / maximum * 100):.2f}%'></div></div></div>" for item in parsed)
        return f"<div class='schematic chart-card mt-3'>{bars}</div>"
    cards = "".join(f"<article class='stat'><div class='stat-label'>{escape(item['label'])}</div><p class='mt-2 mb-0'>{escape(item['detail'])}</p></article>" for item in parsed)
    return f"<div class='schematic grid mt-3'>{cards}</div>"


def _assigned_subjects(teacher):
    return {(class_name, subject) for class_name, subject in teacher_assignments(teacher)}


def _teacher_can_access_material(teacher, material):
    assigned = {(class_name.casefold(), subject.casefold())
                for class_name, subject in _assigned_subjects(teacher)}
    key = (material.class_name.casefold(), material.subject.casefold())
    return key in assigned and (material.is_published or material.teacher_id == teacher.id)


def _store_material(upload):
    if not upload or not upload.filename:
        return None, None
    original_name = secure_filename(upload.filename)
    extension = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    if extension not in ALLOWED_MATERIAL_EXTENSIONS:
        abort(400, description="This learning material file type is not allowed.")
    stored_name = f"{secrets.token_hex(12)}.{extension}"
    folder = "learning_materials"
    directory = os.path.join(current_app.config["PRIVATE_UPLOAD_FOLDER"], folder)
    os.makedirs(directory, exist_ok=True)
    upload.save(os.path.join(directory, stored_name))
    return f"{folder}/{stored_name}", original_name


def _staff_material(material_id):
    material = db.session.get(LearningMaterial, material_id) or abort(404)
    if current_user.role == "teacher" and not _teacher_can_access_material(current_user, material):
        abort(403)
    return material


def learning_materials():
    subjects = _assigned_subjects(current_user) if current_user.role == "teacher" else set()
    if request.method == "POST":
        if current_user.role not in {"master_admin", "head_teacher", "teacher"}:
            abort(403)
        title = request.form.get("title", "").strip()
        resource_type = request.form.get("resource_type", "").strip()
        subject = request.form.get("subject", "").strip()
        class_name = request.form.get("class_name", "").strip()
        description = request.form.get("description", "").strip()
        content = request.form.get("reference_content", "").strip()
        standards = request.form.get("standards", "").strip()
        source_reference = request.form.get("source_reference", "").strip()
        schematic_data = request.form.get("schematic_data", "").strip()
        activity_data = request.form.get("activity_data", "").strip()
        external_url = request.form.get("external_url", "").strip()
        upload = request.files.get("material_file")
        has_upload = bool(upload and upload.filename)
        if not title or not class_name or not subject or resource_type not in RESOURCE_TYPES:
            flash("Enter a title, resource type, class, and subject.", "danger")
        elif external_url and (urlsplit(external_url).scheme != "https" or not urlsplit(external_url).netloc):
            flash("External media and simulation links must use a valid HTTPS address.", "danger")
        elif resource_type in SCHEMATIC_TYPES and not schematic_data and not content and not has_upload and not source_reference:
            flash("Add structured schematic data, explanatory content, a source, or a resource file.", "danger")
        elif resource_type in ACTIVITY_TYPES and not activity_data:
            flash("Add the practice items for this activity.", "danger")
        elif resource_type not in SCHEMATIC_TYPES and resource_type not in ACTIVITY_TYPES and not content and not has_upload and not source_reference and not external_url:
            flash("Add reference content, cite a reading, upload a resource file, or combine them.", "danger")
        elif len(content) > 100000 or len(description) > 2000 or len(standards) > 5000 or len(source_reference) > 500 or len(schematic_data) > 40000 or len(activity_data) > 40000 or len(external_url) > 1000:
            flash("The content, schematic, description, standards, or source details are too long.", "danger")
        elif not User.query.filter_by(role="student", class_name=class_name).first():
            flash("Choose a class with registered students.", "danger")
        elif current_user.role == "teacher" and (class_name.casefold(), subject.casefold()) not in {
            (assigned_class.casefold(), assigned_subject.casefold()) for assigned_class, assigned_subject in subjects
        }:
            flash("You can share resources only for lessons assigned to you for that class.", "danger")
        else:
            if resource_type in SCHEMATIC_TYPES and schematic_data:
                try:
                    _parse_schematic(resource_type, schematic_data)
                except ValueError as error:
                    flash(str(error), "danger")
                    return redirect(url_for("learning_materials"))
            if resource_type in ACTIVITY_TYPES:
                try:
                    _parse_activity(resource_type, activity_data)
                except ValueError as error:
                    flash(str(error), "danger")
                    return redirect(url_for("learning_materials"))
            attachment_path, attachment_name = _store_material(upload)
            is_published = request.form.get("publish") == "yes"
            material = LearningMaterial(
                teacher_id=current_user.id,
                title=title,
                resource_type=resource_type,
                class_name=class_name,
                subject=subject,
                description=description or None,
                reference_content=content or None,
                standards=standards or None,
                source_reference=source_reference or None,
                schematic_data=schematic_data or None,
                activity_data=activity_data or None,
                external_url=external_url or None,
                attachment_path=attachment_path,
                attachment_name=attachment_name,
                is_published=is_published,
                created_at=now_string(),
            )
            db.session.add(material)
            if is_published:
                students = User.query.filter_by(role="student", class_name=class_name).all()
                recipient_ids = {student.id for student in students}
                for student in students:
                    recipient_ids.update(guardian_ids_for_student(student.id))
                notify_users(
                    recipient_ids,
                    "Learning Materials",
                    f"New resource: {title}",
                    f"A learning resource for {subject} is now available to your class.",
                    "/learning-materials",
                )
            db.session.commit()
            flash("Learning resource published." if is_published else "Learning resource saved as a draft.", "success")
            return redirect(url_for("learning_materials"))

    if current_user.role in OFFICE_ROLES:
        materials = LearningMaterial.query.order_by(LearningMaterial.created_at.desc(), LearningMaterial.id.desc()).all()
        class_names = User.query.filter(User.role == "student", User.class_name.isnot(None)).with_entities(User.class_name).distinct().all()
        classes = sorted({name for (name,) in class_names if name})
    elif current_user.role == "teacher":
        assigned_subjects = {(class_name.casefold(), subject.casefold()) for class_name, subject in subjects}
        candidates = LearningMaterial.query.filter(
            db.or_(LearningMaterial.is_published.is_(True),
                   LearningMaterial.teacher_id == current_user.id),
        ).order_by(LearningMaterial.created_at.desc(), LearningMaterial.id.desc()).all()
        materials = [material for material in candidates if (
            material.class_name.casefold(), material.subject.casefold()
        ) in assigned_subjects]
        classes = sorted({class_name for class_name, _subject in subjects}, key=str.casefold)
    elif current_user.role == "parent":
        class_names = {
            enrollment.class_name
            for child in linked_children(current_user.id)
            if (enrollment := current_enrollment(child.id)) is not None
            and child.is_active_account
        }
        materials = LearningMaterial.query.filter(
            LearningMaterial.class_name.in_(class_names) if class_names else False,
            LearningMaterial.is_published.is_(True),
        ).order_by(LearningMaterial.subject, LearningMaterial.created_at.desc()).all()
        classes = []
    else:
        materials = LearningMaterial.query.filter_by(
            class_name=(current_enrollment(current_user.id).class_name
                        if current_user.role == "student" and current_enrollment(current_user.id)
                        else None),
            is_published=True,
        ).order_by(LearningMaterial.subject, LearningMaterial.created_at.desc()).all()
        classes = []

    class_options = "".join(f"<option value='{escape(name)}'>{escape(name)}</option>" for name in classes)
    subject_options = "".join(
        f"<option value='{escape(subject)}' data-class='{escape(class_name)}'>{escape(subject)}</option>"
        for class_name, subject in sorted(subjects, key=lambda item: (item[0].casefold(), item[1].casefold()))
    )
    class_field = (
        f"<div class='col-md-4'><label>Assigned Class</label><select class='form-select' id='material-class' name='class_name' required><option value=''>Choose assigned class...</option>{class_options}</select></div>"
        if current_user.role == "teacher" else
        f"<div class='col-md-4'><label>Class</label><select class='form-select' name='class_name' required><option value=''>Choose class...</option>{class_options}</select></div>"
    )
    subject_field = (
        f"<select class='form-select' name='subject' required><option value=''>Choose assigned lesson...</option>{subject_options}</select>"
        if current_user.role == "teacher" else
        "<input class='form-control' name='subject' maxlength='160' required>"
    )
    resource_type_options = "".join(f"<option value='{escape(resource_type)}'>{escape(resource_type)}</option>" for resource_type in RESOURCE_TYPES)
    create_form = ""
    if current_user.role in OFFICE_ROLES | {"teacher"}:
        create_form = f"""<section class='panel'><h2>Share Learning Resource</h2>
<p class='muted'>Add reference notes, upload a file, or include both. Published resources become available to students in the selected class.</p>
<form method='post' enctype='multipart/form-data' class='row g-3'><div class='col-md-4'><label>Title</label><input class='form-control' name='title' maxlength='180' required></div>{class_field}<div class='col-md-4'><label>Subject</label>{subject_field}</div>
<div class='col-md-4'><label>Resource type</label><select class='form-select' id='resource-type' name='resource_type' required>{resource_type_options}</select></div>
<div class='col-12'><label>Short description</label><input class='form-control' name='description' maxlength='2000'></div>
<div class='col-12'><label>Reference content / e-text</label><textarea class='form-control' name='reference_content' rows='8' maxlength='100000' placeholder='Write or paste explanatory content, reading passages, lecture notes, or a standards overview...'></textarea></div>
<div class='col-12 d-none' id='schematic-editor'><label id='schematic-label'>Visual structure data</label><p class='muted mb-2' id='schematic-help'></p><textarea class='form-control font-monospace' name='schematic_data' rows='6' maxlength='40000' placeholder=''></textarea></div>
<div class='col-12 d-none' id='activity-editor'><label id='activity-label'>Practice activity items</label><p class='muted mb-2' id='activity-help'></p><textarea class='form-control font-monospace' name='activity_data' rows='7' maxlength='40000' placeholder=''></textarea></div>
<div class='col-md-6'><label>Core standards / learning outcomes</label><textarea class='form-control' name='standards' rows='3' maxlength='5000' placeholder='List the standards, competencies, or outcomes covered'></textarea></div>
<div class='col-md-6'><label>Source / citation / reading URL</label><textarea class='form-control' name='source_reference' rows='3' maxlength='500' placeholder='Book, author, publisher, page range, or https:// link'></textarea></div>
<div class='col-md-6'><label>External media / simulation URL (optional)</label><input class='form-control' type='url' name='external_url' maxlength='1000' placeholder='https://...'></div>
<div class='col-12'><label>Resource file (optional)</label><input class='form-control' type='file' name='material_file' accept='.pdf,.doc,.docx,.odt,.ppt,.pptx,.xls,.xlsx,.csv,.txt,.rtf,.jpg,.jpeg,.png,.webp,.mp3,.mp4,.wav,.ogg,.m4a,.aac,.webm,.ogv,.glb'></div>
<div class='col-12'><label><input type='checkbox' name='publish' value='yes' checked> Publish to students now</label></div>
<div class='col-12'><button class='btn btn-accent'>Save Learning Resource</button></div></form></section>
<script>(function(){{const type=document.getElementById('resource-type');const editor=document.getElementById('schematic-editor');const data=editor.querySelector('textarea');const help=document.getElementById('schematic-help');const activityEditor=document.getElementById('activity-editor');const activityData=activityEditor.querySelector('textarea');const activityHelp=document.getElementById('activity-help');const examples={{'Mind Map':'Enter one path per line. Example: Ecosystems > Producers > Plants\\nEcosystems > Consumers > Herbivores','Timeline':'Enter one event per line in chronological order: Date | Event | Details.','Chart':'Enter one category per line: Label | Number. Example: Term 1 | 72','Infographic':'Enter one fact per line: Heading | Explanation.'}};const activities={{'Flashcards':'Enter one card per line: Front | Back. Example: Mitosis | Cell division producing two identical cells.','Drag-and-Drop Activity':'Enter one match per line: Term | Definition. Example: Evaporation | Liquid changes into vapor.','Self-Assessment Quiz':'Enter one question per line: Question | Option A | Option B | Option C | Option D | Correct letter | Optional explanation.'}};function sync(){{const schematic=Object.prototype.hasOwnProperty.call(examples,type.value);const activity=Object.prototype.hasOwnProperty.call(activities,type.value);editor.classList.toggle('d-none',!schematic);data.disabled=!schematic;data.required=schematic;help.textContent=examples[type.value]||'';activityEditor.classList.toggle('d-none',!activity);activityData.disabled=!activity;activityData.required=activity;activityHelp.textContent=activities[type.value]||'';}}type.addEventListener('change',sync);sync();}})();</script>"""

    if current_user.role == "teacher":
        create_form += """<script>(function(){const classSelect=document.getElementById('material-class'),subject=document.querySelector('select[name="subject"]');function sync(){if(!subject)return;[...subject.options].forEach(option=>{if(option.dataset.class)option.hidden=!classSelect.value||option.dataset.class!==classSelect.value;});subject.value='';}classSelect.addEventListener('change',sync);sync();})();</script>"""

    cards = []
    for material in materials:
        extension = material.attachment_name.rsplit(".", 1)[-1].lower() if material.attachment_name and "." in material.attachment_name else ""
        file_link = (
            f"<p><a class='btn btn-sm btn-outline-success' href='{url_for('learning_material_file', material_id=material.id)}'>"
            f"Download {escape(material.attachment_name)}</a></p>"
            if material.attachment_path else ""
        )
        inline_url = url_for("learning_material_file", material_id=material.id, inline=1) if material.attachment_path else ""
        media_player = ""
        if material.attachment_path and material.resource_type == "Educational Video" and extension in VIDEO_MIME_TYPES:
            media_player = f"<video class='w-100 rounded mt-3' controls preload='metadata' style='max-height:520px'><source src='{inline_url}' type='{VIDEO_MIME_TYPES[extension]}'>Your browser does not support this video.</video>"
        elif material.attachment_path and material.resource_type == "Podcast / Audio" and extension in AUDIO_MIME_TYPES:
            media_player = f"<audio class='w-100 mt-3' controls preload='metadata'><source src='{inline_url}' type='{AUDIO_MIME_TYPES[extension]}'>Your browser does not support this audio.</audio>"
        external_link = ""
        if material.external_url:
            parsed_external_url = urlsplit(material.external_url.strip())
            if parsed_external_url.scheme == "https" and parsed_external_url.netloc:
                link_label = {
                    "Educational Video": "Watch online",
                    "Podcast / Audio": "Listen online",
                    "Virtual Simulation": "Open simulation",
                    "3D Model": "View 3D resource",
                }.get(material.resource_type, "Open online resource")
                external_link = f"<p><a class='btn btn-outline-success' href='{escape(material.external_url)}' target='_blank' rel='noopener noreferrer'>{link_label}</a></p>"
        status = "Published" if material.is_published else "Draft"
        teacher_name = f"<span class='muted'>Shared by {escape(material.teacher.full_name)}</span> Â· " if current_user.role == "student" else ""
        content = f"<div class='border rounded p-3 mt-3' style='white-space:pre-wrap'>{escape(material.reference_content)}</div>" if material.reference_content else ""
        standards = f"<div class='mt-3'><strong>Standards / learning outcomes</strong><div class='border rounded p-3 mt-1' style='white-space:pre-wrap'>{escape(material.standards)}</div></div>" if material.standards else ""
        schematic = _render_schematic(material.resource_type, material.schematic_data)
        activity = _render_activity(material)
        source = ""
        if material.source_reference:
            parsed_source = urlsplit(material.source_reference.strip())
            if parsed_source.scheme in {"http", "https"} and parsed_source.netloc:
                source = f"<p class='mt-2'><strong>Source:</strong> <a href='{escape(material.source_reference)}' target='_blank' rel='noopener noreferrer'>{escape(material.source_reference)}</a></p>"
            else:
                source = f"<p class='mt-2'><strong>Source / citation:</strong> {escape(material.source_reference)}</p>"
        cards.append(
            f"<article class='panel'><div class='d-flex justify-content-between gap-3 flex-wrap'><div><span class='pill'>{escape(material.resource_type)}</span><h3 class='mt-2'>{escape(material.title)}</h3></div><span class='pill'>{status}</span></div>"
            f"<p>{teacher_name}<strong>{escape(material.class_name)}</strong> Â· {escape(material.subject)} Â· {escape(material.created_at)}</p>"
            f"<p>{escape(material.description or '')}</p>{source}{external_link}{file_link}{media_player}{content}{schematic}{activity}{standards}</article>"
        )
    intro = "<section class='panel'><h1>Learning <span class='accent'>Materials</span></h1><p class='muted'>Reference notes and digital resources to help students prepare for lessons and evaluations.</p></section>"
    empty = "<section class='panel muted'>No learning materials are available for this class yet.</section>"
    interaction_script = """<script>(function(){
let selectedTerm=null;
function assignTerm(zone,termId){
  const activity=zone.closest('.drag-activity');
  const term=activity.querySelector('.drag-term[data-term-id="'+termId+'"]');
  if(!term)return;
  activity.querySelectorAll('.drop-zone').forEach(other=>{
    if(other.dataset.placed===termId){other.removeAttribute('data-placed');other.querySelector('.drop-label').textContent='Drop or select a matching term';term.classList.remove('d-none');}
  });
  const replaced=zone.dataset.placed;
  if(replaced){const oldTerm=activity.querySelector('.drag-term[data-term-id="'+replaced+'"]');if(oldTerm)oldTerm.classList.remove('d-none');}
  zone.dataset.placed=termId;zone.querySelector('.drop-label').textContent=term.textContent;term.classList.add('d-none');
}
document.addEventListener('click',function(event){
  const flashcard=event.target.closest('.flashcard');
  if(flashcard){const front=flashcard.querySelector('.flash-front');const back=flashcard.querySelector('.flash-back');const show=back.classList.contains('d-none');front.classList.toggle('d-none',show);back.classList.toggle('d-none',!show);flashcard.setAttribute('aria-pressed',String(show));}
  const term=event.target.closest('.drag-term');
  if(term&&!term.classList.contains('d-none')){selectedTerm=term;return;}
  const zone=event.target.closest('.drop-zone');
  if(zone&&selectedTerm&&zone.closest('.drag-activity')===selectedTerm.closest('.drag-activity')){assignTerm(zone,selectedTerm.dataset.termId);selectedTerm=null;}
  const checkMatches=event.target.closest('.check-matches');
  if(checkMatches){const activity=checkMatches.closest('.drag-activity');const zones=[...activity.querySelectorAll('.drop-zone')];let score=0;zones.forEach(item=>{const correct=item.dataset.placed===item.dataset.correct;item.classList.toggle('border-success',correct);item.classList.toggle('border-danger',!correct);if(correct)score++;});activity.querySelector('.activity-result').textContent=score+' of '+zones.length+' matches correct.';}
  const resetMatches=event.target.closest('.reset-matches');
  if(resetMatches){const activity=resetMatches.closest('.drag-activity');activity.querySelectorAll('.drag-term').forEach(item=>item.classList.remove('d-none'));activity.querySelectorAll('.drop-zone').forEach(item=>{item.removeAttribute('data-placed');item.classList.remove('border-success','border-danger');item.querySelector('.drop-label').textContent='Drop or select a matching term';});activity.querySelector('.activity-result').textContent='';selectedTerm=null;}
  const checkQuiz=event.target.closest('.check-self-quiz');
  if(checkQuiz){const quiz=checkQuiz.closest('.self-quiz');const questions=[...quiz.querySelectorAll('.self-question')];let score=0;questions.forEach(question=>{const answer=question.querySelector('input:checked');const correct=answer&&answer.value===question.dataset.correct;if(correct)score++;question.classList.toggle('border-success',Boolean(correct));question.classList.toggle('border-danger',!correct);const feedback=question.querySelector('.self-feedback');feedback.classList.remove('d-none');});quiz.querySelector('.activity-result').textContent=score+' of '+questions.length+' correct. This practice score is not added to your official grades.';}
  const resetQuiz=event.target.closest('.reset-self-quiz');
  if(resetQuiz){const quiz=resetQuiz.closest('.self-quiz');quiz.querySelectorAll('input').forEach(input=>input.checked=false);quiz.querySelectorAll('.self-question').forEach(question=>question.classList.remove('border-success','border-danger'));quiz.querySelectorAll('.self-feedback').forEach(feedback=>feedback.classList.add('d-none'));quiz.querySelector('.activity-result').textContent='';}
  const mapButton=event.target.closest('[data-map-action]');
  if(mapButton){const map=mapButton.closest('.mind-map');if(map)map.querySelectorAll('details').forEach(node=>node.open=mapButton.dataset.mapAction==='expand');}
});
document.addEventListener('dragstart',function(event){const term=event.target.closest('.drag-term');if(term){event.dataTransfer.setData('text/plain',term.dataset.termId);}});
document.addEventListener('dragover',function(event){if(event.target.closest('.drop-zone'))event.preventDefault();});
document.addEventListener('drop',function(event){const zone=event.target.closest('.drop-zone');if(zone){event.preventDefault();assignTerm(zone,event.dataTransfer.getData('text/plain'));}});
})();</script>"""
    return page("Learning Materials", intro + create_form + ("".join(cards) or empty) + interaction_script, "teacher")


def learning_material_file(material_id):
    material = db.session.get(LearningMaterial, material_id) or abort(404)
    if current_user.role in OFFICE_ROLES | {"teacher"}:
        _staff_material(material_id)
    elif current_user.role == "student":
        enrollment = current_enrollment(current_user.id)
        if not material.is_published or not enrollment or material.class_name != enrollment.class_name:
            abort(403)
    elif current_user.role == "parent":
        children = linked_children(current_user.id)
        if (not material.is_published or not any(
            guardian_can_access_student(current_user.id, child.id)
            and (enrollment := current_enrollment(child.id)) is not None
            and enrollment.class_name == material.class_name
            for child in children
        )):
            abort(403)
    else:
        abort(403)
    if not material.attachment_path:
        abort(404)
    path = os.path.join(current_app.config["PRIVATE_UPLOAD_FOLDER"], material.attachment_path)
    if not os.path.isfile(path):
        abort(404)
    inline = request.args.get("inline") == "1"
    extension = material.attachment_name.rsplit(".", 1)[-1].lower() if material.attachment_name and "." in material.attachment_name else ""
    mimetype = VIDEO_MIME_TYPES.get(extension) or AUDIO_MIME_TYPES.get(extension)
    if extension == "glb":
        mimetype = "model/gltf-binary"
    return send_file(path, as_attachment=not inline, download_name=material.attachment_name,
                     mimetype=mimetype, conditional=True)


def register_routes(app):
    app.add_url_rule('/learning-materials', endpoint='learning_materials',
                     view_func=permission_for('learning_materials')(learning_materials), methods=['GET', 'POST'])
    app.add_url_rule('/learning-materials/<int:material_id>/file', endpoint='learning_material_file',
                     view_func=permission_for('learning_material_file')(learning_material_file), methods=['GET'])

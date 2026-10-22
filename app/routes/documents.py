import os
import secrets

from flask import abort, current_app, flash, redirect, request, send_file, url_for
from flask_login import current_user
from markupsafe import escape
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import StudentDocument, User
from app.utils.decorators import permission_for
from app.utils.rendering import page


ALLOWED_DOCUMENT_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "doc", "docx"}
DOCUMENT_CATEGORIES = {"Birth Certificate", "Previous School Records", "Medical Record", "Consent Form", "Other"}
MAX_DOCUMENT_SIZE = 20 * 1024 * 1024


def _student_option_class(student):
    class_name = (student.class_name or "").strip()
    if not class_name:
        return "No class"
    class_parts = class_name.split()
    if class_parts[0] in {"L3", "L4", "L5", "S4", "S5", "S6"}:
        section = class_parts[-1] if class_parts[-1] in {"A", "B"} else ""
        return f"{class_parts[0]} {section}".strip()
    return class_name


def _student_picker_option(student):
    class_name = (student.class_name or "No class").strip() or "No class"
    display = f"{student.full_name} ({_student_option_class(student)})"
    search_terms = f"{student.full_name} {class_name}"
    return (
        f"<div class='document-picker-option' role='option' id='document-student-option-{student.id}' "
        f"aria-selected='false' data-option-value='{student.id}' data-display='{escape(display)}' "
        f"data-search='{escape(search_terms)}'><span>{escape(student.full_name)}</span>"
        f"<small>{escape(class_name)}</small></div>"
    )


def documents():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "upload":
            student = db.session.get(User, request.form.get("student_id", type=int))
            uploaded = request.files.get("document")
            category = request.form.get("category", "").strip()
            original_name = secure_filename(uploaded.filename) if uploaded and uploaded.filename else ""
            extension = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
            if not student or student.role != "student":
                abort(400)
            if not original_name or extension not in ALLOWED_DOCUMENT_EXTENSIONS:
                flash("Choose a PDF, image, DOC, or DOCX document.", "danger")
            elif category not in DOCUMENT_CATEGORIES:
                flash("Choose a valid document category.", "danger")
            else:
                directory = os.path.join(current_app.config["PRIVATE_UPLOAD_FOLDER"], "student_documents")
                os.makedirs(directory, exist_ok=True)
                stored_name = f"{secrets.token_hex(20)}.{extension}"
                file_path = os.path.join(directory, stored_name)
                uploaded.save(file_path)
                if os.path.getsize(file_path) > MAX_DOCUMENT_SIZE:
                    os.remove(file_path)
                    flash("Documents must be 20 MB or smaller.", "danger")
                else:
                    db.session.add(StudentDocument(
                        student_id=student.id,
                        uploaded_by=current_user.id,
                        original_name=original_name,
                        stored_name=stored_name,
                        category=category,
                    ))
                    db.session.commit()
                    flash("Student document uploaded.", "success")
        elif action == "delete":
            document = db.session.get(StudentDocument, request.form.get("document_id", type=int))
            if not document:
                abort(404)
            file_path = os.path.join(current_app.config["PRIVATE_UPLOAD_FOLDER"], "student_documents", document.stored_name)
            if os.path.isfile(file_path):
                os.remove(file_path)
            db.session.delete(document)
            db.session.commit()
            flash("Student document removed.", "success")
        else:
            abort(400)
        return redirect(url_for("documents"))

    students = User.query.filter_by(role="student").order_by(User.full_name).all()
    student_options = "".join(_student_picker_option(student) for student in students)
    records = StudentDocument.query.join(User, StudentDocument.student_id == User.id).order_by(
        StudentDocument.uploaded_at.desc(), StudentDocument.id.desc()
    ).all()
    rows = "".join(
        f"<tr><td>{escape(document.student.full_name if document.student else 'Unknown student')}</td>"
        f"<td>{escape(document.category)}</td><td><a class='document-file-link' href='/documents/{document.id}/download'>{escape(document.original_name)}</a></td>"
        f"<td>{escape(document.uploaded_at.strftime('%Y-%m-%d'))}</td>"
        f"<td>{escape(document.uploader.full_name if document.uploader else '-')}</td>"
        f"<td><form method='post' onsubmit=\"return confirm('Remove this document?')\"><input type='hidden' name='action' value='delete'><input type='hidden' name='document_id' value='{document.id}'><button class='btn btn-sm btn-outline-danger'>Remove</button></form></td></tr>"
        for document in records
    )
    category_options = "".join(
        f"<div class='document-picker-option' role='option' id='document-category-option-{index}' "
        f"aria-selected='false' data-option-value='{escape(category)}' data-display='{escape(category)}' "
        f"data-search='{escape(category)}'>{escape(category)}</div>"
        for index, category in enumerate(sorted(DOCUMENT_CATEGORIES))
    )
    document_styles = """<style>
.documents-upload-form .form-label{display:block;min-height:2.5em}
.document-picker{position:relative;min-width:0}
.document-picker-input{width:100%;min-width:0;text-overflow:ellipsis;white-space:nowrap}
.document-picker-options{position:absolute;inset:calc(100% + 4px) 0 auto;z-index:1200;max-height:min(16rem,45vh);overflow:auto;padding:4px;border:1px solid var(--border);border-radius:10px;background:var(--card);box-shadow:0 12px 28px rgba(0,0,0,.22)}
.document-picker-options[hidden],.document-picker-option[hidden]{display:none!important}
.document-picker-option{display:flex;flex-direction:column;gap:2px;width:100%;padding:8px 10px;border:0;border-radius:7px;background:transparent;color:var(--text);text-align:left;overflow-wrap:anywhere;cursor:pointer}
.document-picker-option:hover,.document-picker-option[aria-selected="true"]{background:color-mix(in srgb,var(--green-700) 12%,var(--card))}
.document-picker-option small{color:var(--muted);overflow-wrap:anywhere}
.documents-table{min-width:980px;table-layout:fixed}
.documents-table th,.documents-table td{vertical-align:middle;white-space:nowrap}
.documents-table th:nth-child(1){width:19%}.documents-table th:nth-child(2){width:17%}.documents-table th:nth-child(3){width:27%}.documents-table th:nth-child(4){width:13%}.documents-table th:nth-child(5){width:14%}.documents-table th:nth-child(6){width:10%}
.document-file-link{display:block;overflow-wrap:anywhere;white-space:normal}
</style>"""
    body = f"""{document_styles}<section class='panel'><h1>Student <span class='accent'>Documents</span></h1><p class='muted'>Upload and manage administrative documents stored securely for each student.</p><form method='post' enctype='multipart/form-data' class='row g-3 documents-upload-form'><input type='hidden' name='action' value='upload'><div class='col-md-4'><label class='form-label' for='document-student-search'>Student</label><div class='document-picker'><input type='text' class='form-control document-picker-input' id='document-student-search' placeholder='Search by student or class' role='combobox' aria-autocomplete='list' aria-haspopup='listbox' aria-expanded='false' aria-controls='document-student-options' autocomplete='off' required><input type='hidden' name='student_id'><div class='document-picker-options' id='document-student-options' role='listbox' hidden>{student_options}</div></div></div><div class='col-md-4'><label class='form-label' for='document-category-search'>Document category</label><div class='document-picker'><input type='text' class='form-control document-picker-input' id='document-category-search' placeholder='Search categories' role='combobox' aria-autocomplete='list' aria-haspopup='listbox' aria-expanded='false' aria-controls='document-category-options' autocomplete='off' required><input type='hidden' name='category'><div class='document-picker-options' id='document-category-options' role='listbox' hidden>{category_options}</div></div></div><div class='col-md-4'><label class='form-label' for='document-file'>File (PDF, PNG, JPG, DOC, DOCX; max 20 MB)</label><input class='form-control' type='file' id='document-file' name='document' accept='.pdf,.png,.jpg,.jpeg,.doc,.docx' required></div><div class='col-12'><button class='btn btn-accent'>Upload Document</button></div></form></section><section class='panel'><h2>Stored Documents</h2><div class='table-responsive'><table class='table align-middle documents-table'><thead><tr><th>Student</th><th>Category</th><th>Document</th><th>Uploaded</th><th>Uploaded by</th><th>Action</th></tr></thead><tbody>{rows or '<tr><td colspan="6" class="muted">No student documents uploaded.</td></tr>'}</tbody></table></div></section>"""
    body += """<script>(()=>{
const picker=document.querySelector('.document-picker');
if(!picker)return;
const input=picker.querySelector('.document-picker-input');
const selected=picker.querySelector('input[type="hidden"]');
const list=picker.querySelector('.document-picker-options');
const options=[...list.querySelectorAll('.document-picker-option')];
const form=picker.closest('form');
let activeIndex=-1;
function visibleOptions(){return options.filter(option=>!option.hidden)}
function setActive(index){
const visible=visibleOptions();
activeIndex=visible.length?Math.max(0,Math.min(index,visible.length-1)):-1;
options.forEach(option=>option.setAttribute('aria-selected','false'));
const active=activeIndex<0?null:visible[activeIndex];
if(active){active.setAttribute('aria-selected','true');input.setAttribute('aria-activedescendant',active.id);active.scrollIntoView({block:'nearest'})}
else input.removeAttribute('aria-activedescendant');
}
function filterOptions(query=input.value){
const normalized=query.trim().toLocaleLowerCase();
options.forEach(option=>{option.hidden=!!normalized&&!option.dataset.search.toLocaleLowerCase().includes(normalized)});
activeIndex=-1;
options.forEach(option=>option.setAttribute('aria-selected','false'));
input.removeAttribute('aria-activedescendant');
list.hidden=false;
input.setAttribute('aria-expanded','true');
}
function closeOptions(){list.hidden=true;input.setAttribute('aria-expanded','false');activeIndex=-1;input.removeAttribute('aria-activedescendant');options.forEach(option=>option.setAttribute('aria-selected','false'))}
function choose(option){selected.value=option.dataset.optionValue;input.value=option.dataset.display;input.setCustomValidity('');closeOptions()}
input.addEventListener('focus',()=>{if(selected.value)input.select();filterOptions(selected.value?'':input.value)});
input.addEventListener('input',()=>{selected.value='';input.setCustomValidity('Choose a student from the suggestions.');filterOptions()});
input.addEventListener('keydown',event=>{
if(event.key==='ArrowDown'||event.key==='ArrowUp'){
event.preventDefault();if(list.hidden)filterOptions();const visible=visibleOptions();if(!visible.length)return;
const next=activeIndex<0?(event.key==='ArrowDown'?0:visible.length-1):activeIndex+(event.key==='ArrowDown'?1:-1);
setActive((next+visible.length)%visible.length);return;
}
if(event.key==='Enter'&&!list.hidden&&activeIndex>=0){event.preventDefault();choose(visibleOptions()[activeIndex]);return}
if(event.key==='Escape')closeOptions();
});
list.addEventListener('click',event=>{const option=event.target.closest('.document-picker-option');if(option&&list.contains(option))choose(option)});
document.addEventListener('pointerdown',event=>{if(!picker.contains(event.target))closeOptions()});
form.addEventListener('submit',event=>{if(!selected.value){event.preventDefault();input.setCustomValidity('Choose a student from the suggestions.');input.reportValidity()}});
})();</script>"""
    body += """<script>(()=>{
const picker=document.querySelector('#document-category-search').closest('.document-picker');
const input=picker.querySelector('.document-picker-input');
const selected=picker.querySelector('input[type="hidden"]');
const list=picker.querySelector('.document-picker-options');
const options=[...list.querySelectorAll('.document-picker-option')];
const form=picker.closest('form');
let activeIndex=-1;
function visibleOptions(){return options.filter(option=>!option.hidden)}
function filterOptions(){
const query=input.value.trim().toLocaleLowerCase();
options.forEach(option=>{option.hidden=!!query&&!option.dataset.search.toLocaleLowerCase().includes(query);option.setAttribute('aria-selected','false')});
activeIndex=-1;input.removeAttribute('aria-activedescendant');list.hidden=false;input.setAttribute('aria-expanded','true');
}
function closeOptions(){list.hidden=true;activeIndex=-1;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');options.forEach(option=>option.setAttribute('aria-selected','false'))}
function choose(option){selected.value=option.dataset.optionValue;input.value=option.dataset.display;input.setCustomValidity('');closeOptions()}
input.addEventListener('focus',()=>{if(selected.value)input.select();filterOptions()});
input.addEventListener('input',()=>{selected.value='';input.setCustomValidity('Choose a category from the suggestions.');filterOptions()});
input.addEventListener('keydown',event=>{
if(event.key==='ArrowDown'||event.key==='ArrowUp'){
event.preventDefault();if(list.hidden)filterOptions();const visible=visibleOptions();if(!visible.length)return;
activeIndex=activeIndex<0?(event.key==='ArrowDown'?0:visible.length-1):(activeIndex+(event.key==='ArrowDown'?1:-1)+visible.length)%visible.length;
options.forEach(option=>option.setAttribute('aria-selected','false'));
const active=visible[activeIndex];active.setAttribute('aria-selected','true');input.setAttribute('aria-activedescendant',active.id);active.scrollIntoView({block:'nearest'});return;
}
if(event.key==='Enter'&&!list.hidden&&activeIndex>=0){event.preventDefault();choose(visibleOptions()[activeIndex]);return}
if(event.key==='Escape')closeOptions();
});
list.addEventListener('click',event=>{const option=event.target.closest('.document-picker-option');if(option&&list.contains(option))choose(option)});
document.addEventListener('pointerdown',event=>{if(!picker.contains(event.target))closeOptions()});
form.addEventListener('submit',event=>{if(!selected.value){event.preventDefault();input.setCustomValidity('Choose a category from the suggestions.');input.reportValidity()}});
})();</script>"""
    return page("Student Documents", body, "master_admin")


def download_document(document_id):
    document = db.session.get(StudentDocument, document_id) or abort(404)
    file_path = os.path.join(current_app.config["PRIVATE_UPLOAD_FOLDER"], "student_documents", document.stored_name)
    if not os.path.isfile(file_path):
        abort(404)
    return send_file(file_path, as_attachment=True, download_name=document.original_name)


def register_routes(app):
    app.add_url_rule("/documents", endpoint="documents", view_func=permission_for("documents")(documents), methods=["GET", "POST"])
    app.add_url_rule("/documents/<int:document_id>/download", endpoint="download_document", view_func=permission_for("documents")(download_document), methods=["GET"])

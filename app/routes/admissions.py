from flask import abort, flash, redirect, request, url_for
from flask_login import current_user
from markupsafe import escape

from app.extensions import db
from app.models import AdmissionApplicant, User
from app.utils.constants import CLASS_TRADE_LEVEL, PROGRAM_LEVELS, TRADES
from app.utils.decorators import permission_for
from app.utils.helpers import generate_sdms_code
from app.utils.rendering import page
from app.utils.notifications import notify_school_admins
from app.services.activity import log_student_activity


def admissions():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            full_name = request.form.get("full_name", "").strip()
            department = request.form.get("department", "").strip()
            trade = request.form.get("trade", "").strip()
            level = request.form.get("level", "").strip()
            class_name = request.form.get("class_name", "").strip()
            class_section = request.form.get("class_section", "").strip()
            if (not full_name or not department or trade not in PROGRAM_LEVELS
                    or level not in PROGRAM_LEVELS.get(trade, []) or not class_name):
                flash("Enter the applicant name and department, then choose a valid trade, level, and class.", "danger")
            elif len(department) > 100 or len(class_section) > 50:
                flash("Department must be 100 characters or fewer and class section 50 characters or fewer.", "danger")
            elif class_name in CLASS_TRADE_LEVEL and CLASS_TRADE_LEVEL[class_name] != (trade, level):
                flash("The selected class does not match the chosen trade and level.", "danger")
            else:
                applicant = AdmissionApplicant(
                    full_name=full_name,
                    guardian_name=request.form.get("guardian_name", "").strip(),
                    phone=request.form.get("phone", "").strip(),
                    email=request.form.get("email", "").strip(),
                    desired_department=department,
                    desired_trade=trade,
                    desired_level=level,
                    desired_class=class_name,
                    desired_section=class_section or None,
                    documents_submitted=request.form.get("documents_submitted", "").strip(),
                )
                db.session.add(applicant)
                db.session.commit()
                notify_school_admins(
                    "Admissions", "New applicant registered",
                    f"{applicant.full_name} has applied for {applicant.desired_class}; documents and admission status need review.",
                    "/admissions",
                )
                db.session.commit()
                flash("Applicant registered.", "success")
        elif action == "review":
            applicant = db.session.get(AdmissionApplicant, request.form.get("applicant_id", type=int))
            status = request.form.get("status", "")
            verified = request.form.get("documents_verified") == "yes"
            if not applicant:
                abort(404)
            if status not in {"Submitted", "Under Review", "Admitted", "Rejected"}:
                abort(400)
            if status == "Admitted" and not verified:
                flash("Verify the submitted documents before admitting the applicant.", "danger")
            else:
                applicant.documents_verified = verified
                applicant.status = status
                db.session.commit()
                flash("Admission review saved.", "success")
        elif action == "convert":
            applicant = db.session.get(AdmissionApplicant, request.form.get("applicant_id", type=int))
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if not applicant:
                abort(404)
            existing_class_students = User.query.filter_by(role="student", class_name=applicant.desired_class).all()
            target_department = applicant.desired_department or applicant.desired_trade
            class_conflict = any(
                (student.department or student.trade) != target_department
                or student.trade != applicant.desired_trade
                or student.level != applicant.desired_level
                for student in existing_class_students
            )
            if applicant.status != "Admitted" or not applicant.documents_verified:
                flash("Only admitted applicants with verified documents can be converted.", "danger")
            elif not username or len(password) < 8:
                flash("Provide a username and a password of at least 8 characters.", "danger")
            elif User.query.filter_by(username=username).first():
                flash("That username is already in use.", "danger")
            elif class_conflict:
                flash("The applicant's class is assigned to a different trade or level.", "danger")
            else:
                student = User(
                    username=username,
                    full_name=applicant.full_name,
                    role="student",
                    class_name=applicant.desired_class,
                    class_section=applicant.desired_section,
                    department=target_department,
                    level=applicant.desired_level,
                    trade=applicant.desired_trade,
                    sdms_code=generate_sdms_code(),
                )
                student.set_password(password)
                db.session.add(student)
                db.session.flush()
                applicant.converted_student_id = student.id
                log_student_activity(current_user, student, "Admission converted", f"Admitted applicant converted into student account in {student.class_name}.")
                db.session.commit()
                flash("Applicant converted into a student account.", "success")
        else:
            abort(400)
        return redirect(url_for("admissions"))

    applicants = AdmissionApplicant.query.order_by(AdmissionApplicant.applied_at.desc(), AdmissionApplicant.id.desc()).all()
    rows = []
    for applicant in applicants:
        docs = escape(applicant.documents_submitted or "Not listed")
        converted = (
            f"<a href='/student/profile/{applicant.converted_student_id}'>Student profile</a>"
            if applicant.converted_student_id else
            f"<form method='post' class='d-flex flex-wrap gap-2'><input type='hidden' name='action' value='convert'><input type='hidden' name='applicant_id' value='{applicant.id}'><input class='form-control form-control-sm' name='username' placeholder='Student username' required><input class='form-control form-control-sm' type='password' name='password' minlength='8' placeholder='Temporary password' required><button class='btn btn-sm btn-accent'>Convert to Student</button></form>"
            if applicant.status == "Admitted" and applicant.documents_verified else "—"
        )
        rows.append(
            f"<tr><td>{escape(applicant.full_name)}</td><td>{escape(applicant.guardian_name or '-')}<br>{escape(applicant.phone or applicant.email or '-')}</td>"
            f"<td>{escape(applicant.desired_department or applicant.desired_trade)} / {escape(applicant.desired_trade)} / "
            f"{escape(applicant.desired_level)} / {escape(applicant.desired_class)} / {escape(applicant.desired_section or 'No section')}</td>"
            f"<td>{docs}</td><td>{escape(applicant.applied_at.strftime('%Y-%m-%d'))}</td>"
            f"<td><form method='post' class='d-flex flex-column gap-2'><input type='hidden' name='action' value='review'><input type='hidden' name='applicant_id' value='{applicant.id}'>"
            f"<select class='form-select form-select-sm' name='status'>{''.join(f'<option value="{value}"{" selected" if applicant.status == value else ""}>{value}</option>' for value in ('Submitted', 'Under Review', 'Admitted', 'Rejected'))}</select>"
            f"<label class='small'><input type='checkbox' name='documents_verified' value='yes'{ ' checked' if applicant.documents_verified else ''}> Documents verified</label><button class='btn btn-sm btn-outline-success'>Save Review</button></form></td><td>{converted}</td></tr>"
        )
    trade_options = "".join(f"<option value='{escape(trade)}'>{escape(trade)}</option>" for trade in TRADES)
    level_options = "".join(
        f"<option value='{escape(level)}' data-trade='{escape(trade)}'>{escape(level)}</option>"
        for trade, levels in PROGRAM_LEVELS.items() for level in levels
    )
    class_options = "".join(
        f"<option value='{escape(name)}' data-trade='{escape(trade)}' data-level='{escape(level)}'>{escape(name)}</option>"
        for name, (trade, level) in CLASS_TRADE_LEVEL.items()
    )
    known_departments = sorted(
        {student.department or student.trade for student in User.query.filter_by(role="student").all()
         if student.department or student.trade},
        key=str.casefold,
    )
    department_options = "".join(f'<option value="{escape(value)}">' for value in known_departments)
    body = f"""<section class='panel'><h1>Admissions</h1><p class='muted'>Register applicants, record submitted documents, verify eligibility, and manage admission decisions.</p><form method='post' class='row g-3'><input type='hidden' name='action' value='create'><div class='col-md-6'><label class='form-label'>Applicant name</label><input class='form-control' name='full_name' required></div><div class='col-md-6'><label class='form-label'>Department</label><input class='form-control' name='department' maxlength='100' list='admission-departments' required><datalist id='admission-departments'>{department_options}</datalist></div><div class='col-md-6'><label class='form-label'>Parent / guardian</label><input class='form-control' name='guardian_name'></div><div class='col-md-6'><label class='form-label'>Phone</label><input class='form-control' name='phone'></div><div class='col-md-6'><label class='form-label'>Email</label><input class='form-control' type='email' name='email'></div><div class='col-md-3'><label class='form-label'>Trade</label><select id='admission-trade' class='form-select' name='trade' required><option value=''>Choose trade</option>{trade_options}</select></div><div class='col-md-3'><label class='form-label'>Level</label><select id='admission-level' class='form-select' name='level' required><option value=''>Choose level</option>{level_options}</select></div><div class='col-md-3'><label class='form-label'>Class</label><select id='admission-class' class='form-select' name='class_name' required><option value=''>Choose class</option>{class_options}</select></div><div class='col-md-3'><label class='form-label'>Class section</label><input class='form-control' name='class_section' maxlength='50' placeholder='A, B, Morning'></div><div class='col-12'><label class='form-label'>Documents received</label><textarea class='form-control' name='documents_submitted' rows='2' placeholder='List submitted documents'></textarea></div><div class='col-12'><button class='btn btn-accent'>Register Applicant</button></div></form></section><section class='panel'><h2>Applicant Records</h2><div class='table-responsive'><table class='table align-middle'><thead><tr><th>Applicant</th><th>Guardian / Contact</th><th>Requested placement</th><th>Documents</th><th>Applied</th><th>Review</th><th>Student account</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="7" class="muted">No applicants registered.</td></tr>'}</tbody></table></div></section><script>(function(){{const trade=document.getElementById('admission-trade'),level=document.getElementById('admission-level'),className=document.getElementById('admission-class');function filter(){{for(const option of level.options)if(option.dataset.trade)option.hidden=option.dataset.trade!==trade.value;for(const option of className.options)if(option.dataset.trade)option.hidden=option.dataset.trade!==trade.value||option.dataset.level!==level.value;if(level.selectedOptions[0]?.hidden)level.value='';if(className.selectedOptions[0]?.hidden)className.value='';}}trade.addEventListener('change',filter);level.addEventListener('change',filter);filter();}})();</script>"""
    body += """<style>
.admission-picker{position:relative;width:100%;min-width:0}
.admission-picker-source{display:none!important}
.admission-picker-input{width:100%;min-width:0;text-overflow:ellipsis;white-space:nowrap}
.admission-picker-options{position:absolute;inset:calc(100% + 4px) 0 auto;z-index:1300;display:flex;flex-direction:column;max-height:min(16rem,45vh);overflow:auto;padding:4px;border:1px solid var(--border);border-radius:10px;background:var(--card);box-shadow:0 12px 28px rgba(0,0,0,.24)}
.admission-picker-options[hidden]{display:none!important}
.admission-picker-option{width:100%;min-width:0;padding:9px 10px;border:0;border-radius:7px;background:transparent;color:var(--text);text-align:left;overflow-wrap:anywhere;white-space:normal;cursor:pointer}
.admission-picker-option:hover,.admission-picker-option[aria-selected="true"]{background:color-mix(in srgb,var(--green-700) 12%,var(--card))}
</style><script>(()=>{
const selects=['admission-trade','admission-level','admission-class'].map(id=>document.getElementById(id)).filter(Boolean);
const controls=[];
function enhance(select,index){
const wrapper=document.createElement('div');wrapper.className='admission-picker';
const input=document.createElement('input');input.type='text';input.className='form-control admission-picker-input';input.autocomplete='off';input.setAttribute('role','combobox');input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-haspopup','listbox');input.setAttribute('aria-expanded','false');
const list=document.createElement('div');list.className='admission-picker-options';list.setAttribute('role','listbox');list.hidden=true;
const id=select.id||'admission-choice-'+index;select.id=id;input.id=id+'-picker';list.id=id+'-options';input.setAttribute('aria-controls',list.id);
const label=select.previousElementSibling&&select.previousElementSibling.tagName==='LABEL'?select.previousElementSibling:null;if(label)label.htmlFor=input.id;else input.setAttribute('aria-label','Choose an option');
input.required=select.required;select.required=false;select.classList.add('admission-picker-source');select.tabIndex=-1;
select.parentNode.insertBefore(wrapper,select);wrapper.append(input,list,select);
let activeIndex=-1;
function available(){return [...select.options].filter(option=>!option.hidden&&!option.disabled)}
function visibleItems(){return [...list.querySelectorAll('.admission-picker-option')].filter(item=>!item.hidden)}
function close(){list.hidden=true;activeIndex=-1;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');list.querySelectorAll('.admission-picker-option').forEach(item=>item.setAttribute('aria-selected','false'))}
function render(query=''){
const normalized=query.trim().toLocaleLowerCase();
const options=available().filter(option=>!normalized||option.textContent.trim().toLocaleLowerCase().includes(normalized));
list.replaceChildren(...options.map((option,index)=>{const item=document.createElement('button');item.type='button';item.className='admission-picker-option';item.id=id+'-option-'+index;item.setAttribute('role','option');item.setAttribute('aria-selected','false');item.dataset.value=option.value;item.dataset.label=option.textContent.trim();item.textContent=option.textContent.trim();return item}));
list.hidden=false;input.setAttribute('aria-expanded','true');activeIndex=-1;
}
function sync(){input.disabled=select.disabled;const selected=select.selectedOptions[0];if(list.hidden||select.value)input.value=select.value&&selected?selected.textContent.trim():'';input.setCustomValidity(input.required&&!select.value?'Choose an option from the list.':'');if(!list.hidden)render(input.value)}
function choose(item){select.value=item.dataset.value;select.dispatchEvent(new Event('change',{bubbles:true}));input.value=item.dataset.label;input.setCustomValidity(input.required&&!select.value?'Choose an option from the list.':'');close()}
input.addEventListener('focus',()=>{if(select.value){input.value=select.selectedOptions[0]?.textContent.trim()||'';input.select()}render(select.value?'':input.value);controls.forEach(control=>{if(control.wrapper!==wrapper)control.close()})});
input.addEventListener('click',()=>{if(list.hidden)render(select.value?'':input.value)});
input.addEventListener('input',()=>{const query=input.value;if(select.value){select.value='';select.dispatchEvent(new Event('change',{bubbles:true}))}input.value=query;input.setCustomValidity(input.required?'Choose an option from the list.':'');render(query)});
input.addEventListener('keydown',event=>{if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();if(list.hidden)render(input.value);const items=visibleItems();if(!items.length)return;activeIndex=activeIndex<0?(event.key==='ArrowDown'?0:items.length-1):(activeIndex+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;list.querySelectorAll('.admission-picker-option').forEach(item=>item.setAttribute('aria-selected','false'));const active=items[activeIndex];active.setAttribute('aria-selected','true');input.setAttribute('aria-activedescendant',active.id);active.scrollIntoView({block:'nearest'});return}if(event.key==='Enter'&&!list.hidden){event.preventDefault();const items=visibleItems();if(activeIndex>=0&&items[activeIndex])choose(items[activeIndex]);else if(items.length===1)choose(items[0]);return}if(event.key==='Escape')close()});
list.addEventListener('click',event=>{const item=event.target.closest('.admission-picker-option');if(item&&list.contains(item))choose(item)});
select.addEventListener('change',sync);
const observer=new MutationObserver(()=>{if(select.value&&select.selectedOptions[0]?.hidden){select.value='';sync()}else if(!list.hidden)render(input.value);else sync()});observer.observe(select,{childList:true,subtree:true,attributes:true,attributeFilter:['hidden','disabled']});
const control={wrapper,close};controls.push(control);sync();
}
selects.forEach(enhance);
document.addEventListener('pointerdown',event=>controls.forEach(control=>{if(!control.wrapper.contains(event.target))control.close()}));
})();</script>"""
    return page("Admissions", body, "master_admin")


def register_routes(app):
    app.add_url_rule("/admissions", endpoint="admissions", view_func=permission_for("admissions")(admissions), methods=["GET", "POST"])

import os
import datetime
import json
from flask import request, redirect, url_for, flash, jsonify, abort, send_file, current_app
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from openpyxl import Workbook
from app.extensions import db
from app.models import User, Attendance, PendingCard, FeeTransaction, Book, BookLoan, DisciplineCase, SchoolMessage, Grade, TimetableEntry, Setting
from app.utils.constants import *
from app.utils.decorators import role_required
from app.utils.helpers import get_setting, set_setting, attendance_for_student, linked_children
from app.utils.rendering import page
from app.utils.decorators import permission_for
from markupsafe import escape

def library():
    can_manage = current_user.role in {"master_admin", "librarian"}
    if request.method == "POST" and not can_manage:
        abort(403)
    if request.method == "POST":
        action = request.form.get("action", "add_book")
        if action == "create_loan":
            student = db.session.get(User, request.form.get("student_id", type=int)) or abort(404)
            book = db.session.get(Book, request.form.get("book_id", type=int)) or abort(404)
            try:
                borrowed_date = datetime.date.fromisoformat(request.form.get("borrowed_date", ""))
                due_date = datetime.date.fromisoformat(request.form.get("due_date", ""))
            except ValueError:
                borrowed_date = due_date = None
            if student.role != "student":
                abort(400)
            if not borrowed_date or not due_date or due_date < borrowed_date:
                flash("Enter a valid borrow date and a due date on or after it.", "danger")
            elif (book.available or 0) < 1:
                flash("No copies of this book are currently available.", "danger")
            else:
                loan = BookLoan(book_id=book.id, student_id=student.id,
                                borrowed_date=borrowed_date, due_date=due_date)
                book.available -= 1
                db.session.add(loan)
                db.session.commit()
                flash("Book checkout recorded with its due date.", "success")
                return redirect(url_for("library"))
        elif action == "return_loan":
            loan = db.session.get(BookLoan, request.form.get("loan_id", type=int)) or abort(404)
            if loan.returned_date:
                flash("This book has already been marked as returned.", "info")
            else:
                loan.returned_date = datetime.date.today()
                loan.book.available = min(loan.book.quantity or 0, (loan.book.available or 0) + 1)
                db.session.commit()
                flash("Book return recorded.", "success")
                return redirect(url_for("library"))
        else:
            title = request.form.get("title", "").strip()
            category = request.form.get("category", "").strip()
            try:
                quantity = int(request.form.get("quantity", 1))
            except ValueError:
                quantity = 0
            if not title or not category or quantity < 1:
                flash("Book title, category, and a positive stock quantity are required.", "danger")
            else:
                b = Book(title=title, category=category, quantity=quantity, available=quantity)
                db.session.add(b)
                db.session.commit()
                flash("Book added to library inventory.", "success")
                return redirect(url_for("library"))
    books = Book.query.order_by(Book.title).all()
    book_rows = "".join(f"<tr><td>{escape(b.title)}</td><td>{escape(b.category)}</td><td>{b.quantity}</td><td>{b.available}</td><td>{(b.quantity or 0) - (b.available or 0)}</td></tr>" for b in books)
    book_form = """<form method="post" class="row g-3"><input type="hidden" name="action" value="add_book"><div class="col-md-6"><label>Book Title</label><input class="form-control" name="title" required></div><div class="col-md-6"><label>Category / Type</label><input class="form-control" name="category" required></div><div class="col-md-6"><label>Quantity</label><input type="number" min="1" class="form-control" name="quantity" value="1" required></div><div class="col-12"><button class="btn btn-accent">Add Book</button></div></form>""" if can_manage else ""
    loan_form = ""
    if can_manage:
        students = User.query.filter_by(role="student").order_by(User.full_name).all()
        available_books = [book for book in books if (book.available or 0) > 0]
        student_options = "".join(f"<option value='{student.id}'>{escape(student.full_name)} ({escape(student.class_name or 'No class')})</option>" for student in students)
        book_options = "".join(f"<option value='{book.id}'>{escape(book.title)} — {book.available} available</option>" for book in available_books)
        loan_form = f"""<section class="panel"><h2>Check Out a Book</h2><form method="post" class="row g-3"><input type="hidden" name="action" value="create_loan"><div class="col-md-6"><label>Student</label><select class="form-select" name="student_id" required><option value="">Choose student...</option>{student_options}</select></div><div class="col-md-6"><label>Book</label><select class="form-select" name="book_id" required><option value="">Choose available book...</option>{book_options}</select></div><div class="col-md-6"><label>Borrowed Date</label><input class="form-control" type="date" name="borrowed_date" value="{datetime.date.today().isoformat()}" required></div><div class="col-md-6"><label>Due Date</label><input class="form-control" type="date" name="due_date" required></div><div class="col-12"><button class="btn btn-accent">Record Checkout</button></div></form></section>"""
    loan_query = BookLoan.query
    show_loans = current_user.role in {"master_admin", "librarian", "student", "parent"}
    if current_user.role == "student":
        loan_query = loan_query.filter_by(student_id=current_user.id)
    elif current_user.role == "parent":
        loan_query = loan_query.filter(BookLoan.student_id.in_([student.id for student in linked_children(current_user.id)]))
    loans = loan_query.order_by(BookLoan.borrowed_date.desc(), BookLoan.id.desc()).all() if show_loans else []
    include_student = current_user.role != "student"
    loan_rows = []
    for loan in loans:
        overdue = loan.returned_date is None and loan.due_date < datetime.date.today()
        status = "Returned" if loan.returned_date else "Overdue" if overdue else "Borrowed"
        return_action = f"<form method='post'><input type='hidden' name='action' value='return_loan'><input type='hidden' name='loan_id' value='{loan.id}'><button class='btn btn-sm btn-outline-success'>Mark Returned</button></form>" if can_manage and not loan.returned_date else status
        student_cell = f"<td>{escape(loan.student.full_name if loan.student else '-')}</td>" if include_student else ""
        loan_rows.append(f"<tr>{student_cell}<td>{escape(loan.book.title if loan.book else '-')}</td><td>{loan.borrowed_date.isoformat()}</td><td>{loan.due_date.isoformat()}</td><td>{loan.returned_date.isoformat() if loan.returned_date else '-'}</td><td>{return_action}</td></tr>")
    loan_header = "<th>Student</th>" if include_student else ""
    catalogue = f"""<section class="panel"><h2>Library Catalogue</h2><div class="table-responsive"><table class="table"><tr><th>Title</th><th>Category</th><th>Total Stock</th><th>Available</th><th>Borrowed</th></tr>{book_rows or '<tr><td colspan="5" class="muted">No books are listed in the catalogue.</td></tr>'}</table></div></section>"""
    history = f"""<section class="panel"><h2>{'Book Loan History' if current_user.role in {'master_admin','librarian'} else 'My Children’s Book Loans' if current_user.role == 'parent' else 'My Book Loans'}</h2><p class="muted">Overdue status is shown when a book has not been returned by its due date.</p><div class="table-responsive"><table class="table"><tr>{loan_header}<th>Book</th><th>Borrowed</th><th>Due Date</th><th>Returned</th><th>Status / Action</th></tr>{''.join(loan_rows) or f'<tr><td colspan="{6 if include_student else 5}" class="muted">No book loans are recorded.</td></tr>'}</table></div></section>""" if show_loans else ""
    body = f"""<section class="panel"><h1>Library <span class="accent">Books & Loans</span></h1><p class="muted">Review available books, checkouts, return dates, deadlines, and overdue books.</p>{book_form}</section>{loan_form}{catalogue}{history}"""
    return page("Library", body, "librarian")

def register_routes(app):
    app.add_url_rule('/library', endpoint='library', view_func=permission_for('library')(library), methods=['GET', 'POST'])

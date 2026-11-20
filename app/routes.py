import io

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, url_for)
from flask_login import current_user, login_required
from sqlalchemy import text

from . import audit
from .extensions import db
from .forms import (AssignForm, CustomerForm, DecisionForm, GraphForm, InvestigationForm, JustificationForm,
                    ProblemForm, QuestionForm, ResultSummaryForm, SolutionForm, TicketForm, UploadForm, UserForm)
from .models import (AuditLog, Customer, FinalReport, GraphConfiguration, Problem, ProposedSolution, Role,
                     Status, TechnicalJustification, TechnicalQuestion, TestFile, TestResult, Ticket, User)
from .permissions import (can_customer_download, can_edit_investigation, can_view_ticket, get_ticket_or_404,
                          roles_required, visible_tickets_query)
from .services import WorkflowError, approval_service, data_service, investigation_service as inv_service
from .services import report_service, ticket_service

bp = Blueprint("main", __name__)
PER_PAGE = 15
STAFF = (Role.ADMIN, Role.SUPPORT, Role.ENGINEER, Role.MANAGEMENT)
OFFICE = (Role.ADMIN, Role.SUPPORT)
LOCKED_FOR_OFFICE = (Status.PENDING_MANAGEMENT, Status.APPROVED, Status.CLOSED)


# ---------------------------------------------------------------- helpers
def flash_errors(form):
    for field, errors in form.errors.items():
        label = getattr(getattr(form, field, None), "label", None)
        for e in errors:
            flash(f"{label.text + ': ' if label else ''}{e}", "danger")


def get_problem(problem_id, staff_only=True):
    problem = db.get_or_404(Problem, problem_id)
    if not can_view_ticket(current_user, problem.ticket):
        abort(404)
    if staff_only and current_user.role not in STAFF:
        abort(403)  # customers never see engineering records
    return problem


def get_editable_problem(problem_id):
    """Engineer-only write access to an assigned, unlocked investigation."""
    problem = get_problem(problem_id)
    if not can_edit_investigation(current_user, problem.ticket):
        if current_user.role == Role.ENGINEER:
            flash("This investigation is locked or not editable right now.", "warning")
            abort(redirect(url_for("main.problem_detail", problem_id=problem.id)))
        abort(403)
    return problem


def back_to_problem(problem, anchor=""):
    return redirect(url_for("main.problem_detail", problem_id=problem.id) + (f"#{anchor}" if anchor else ""))


def get_file(file_id):
    f = db.get_or_404(TestFile, file_id)
    if not can_view_ticket(current_user, f.problem.ticket) or current_user.role not in STAFF:
        abort(404)
    return f


def engineer_choices():
    engineers = User.query.filter_by(role=Role.ENGINEER, active=True).order_by(User.full_name).all()
    return [(0, "Unassigned")] + [(u.id, u.full_name) for u in engineers]


def customer_choices(active_only=True):
    q = Customer.query.order_by(Customer.company_name)
    if active_only:
        q = q.filter_by(active=True)
    return [(c.id, f"{c.company_name} ({c.customer_code})") for c in q.all()]


# ---------------------------------------------------------------- basics
@bp.route("/")
def index():
    return redirect(url_for("main.dashboard" if current_user.is_authenticated else "auth.login"))


@bp.route("/healthz")
def healthz():
    db.session.execute(text("SELECT 1"))
    return jsonify(status="ok")


@bp.route("/dashboard")
@login_required
def dashboard():
    query = visible_tickets_query(current_user)
    data = ticket_service.dashboard_data(query, current_user)
    recent = query.order_by(Ticket.updated_at.desc()).limit(8).all()
    waiting = []
    if current_user.role == Role.MANAGEMENT:
        waiting = query.filter(Ticket.status == Status.PENDING_MANAGEMENT).order_by(Ticket.updated_at).all()
    return render_template("dashboard.html", data=data, recent=recent, waiting=waiting)


# ---------------------------------------------------------------- customers
@bp.route("/customers")
@roles_required(*OFFICE)
def customers():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    country = request.args.get("country", "").strip()
    query = Customer.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Customer.company_name.ilike(like), Customer.contact_person.ilike(like),
                                    Customer.email.ilike(like), Customer.customer_code.ilike(like)))
    if status in ("active", "inactive"):
        query = query.filter(Customer.active == (status == "active"))
    if country:
        query = query.filter(Customer.country.ilike(f"%{country}%"))
    page = query.order_by(Customer.company_name).paginate(page=request.args.get("page", 1, type=int),
                                                          per_page=PER_PAGE, error_out=False)
    return render_template("customers.html", page=page, q=q, status=status, country=country)


@bp.route("/customers/new", methods=["GET", "POST"])
@roles_required(*OFFICE)
def customer_new():
    form = CustomerForm()
    if form.validate_on_submit():
        c = Customer(customer_code=Customer.new_placeholder_code(), active=True)
        form.populate_obj(c)
        db.session.add(c)
        db.session.flush()
        c.finalize_code()
        audit.log("customer.created", "Customer", c.id, c.company_name)
        db.session.commit()
        flash(f"Customer {c.customer_code} registered.", "success")
        return redirect(url_for("main.customers"))
    return render_template("form_page.html", form=form, title="Register customer", back=url_for("main.customers"))


@bp.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
@roles_required(*OFFICE)
def customer_edit(customer_id):
    c = db.get_or_404(Customer, customer_id)
    form = CustomerForm(obj=c)
    if form.validate_on_submit():
        form.populate_obj(c)
        audit.log("customer.updated", "Customer", c.id, c.company_name)
        db.session.commit()
        flash("Customer updated.", "success")
        return redirect(url_for("main.customers"))
    return render_template("form_page.html", form=form, title=f"Edit {c.company_name}", back=url_for("main.customers"))


@bp.route("/customers/<int:customer_id>/toggle", methods=["POST"])
@roles_required(*OFFICE)
def customer_toggle(customer_id):
    c = db.get_or_404(Customer, customer_id)
    c.active = not c.active
    audit.log("customer.activated" if c.active else "customer.deactivated", "Customer", c.id, c.company_name)
    db.session.commit()
    flash(f"{c.company_name} is now {'active' if c.active else 'inactive'}.", "success")
    return redirect(url_for("main.customers"))


# ---------------------------------------------------------------- users (admin)
@bp.route("/users")
@roles_required(Role.ADMIN)
def users():
    q = request.args.get("q", "").strip()
    role = request.args.get("role", "")
    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(User.username.ilike(like), User.full_name.ilike(like), User.email.ilike(like)))
    if role in Role.ALL:
        query = query.filter(User.role == role)
    page = query.order_by(User.full_name).paginate(page=request.args.get("page", 1, type=int),
                                                   per_page=PER_PAGE, error_out=False)
    return render_template("users.html", page=page, q=q, role=role)


def _user_form(user=None):
    form = UserForm(obj=user)
    form.customer_id.choices = [(0, "None")] + customer_choices(active_only=False)
    return form


def _save_user(form, user, creating):
    email_taken = User.query.filter(db.func.lower(User.email) == form.email.data.lower(), User.id != (user.id or 0)).first()
    name_taken = User.query.filter(db.func.lower(User.username) == form.username.data.lower(), User.id != (user.id or 0)).first()
    if email_taken:
        form.email.errors.append("This email is already used.")
    if name_taken:
        form.username.errors.append("This username is already used.")
    if creating and not form.password.data:
        form.password.errors.append("Set an initial password.")
    if email_taken or name_taken or (creating and not form.password.data):
        return False
    if not creating and user.id == current_user.id and form.role.data != user.role:
        flash("You cannot change your own role.", "danger")
        return False
    user.username, user.full_name, user.email = form.username.data.strip(), form.full_name.data.strip(), form.email.data.strip()
    user.role = form.role.data
    user.customer_id = form.customer_id.data or None if form.role.data == Role.CUSTOMER else None
    if form.password.data:
        user.set_password(form.password.data)
        user.failed_attempts, user.locked_until = 0, None
    return True


@bp.route("/users/new", methods=["GET", "POST"])
@roles_required(Role.ADMIN)
def user_new():
    form = _user_form()
    if form.validate_on_submit():
        user = User(active=True)
        if _save_user(form, user, creating=True):
            db.session.add(user)
            db.session.flush()
            audit.log("user.created", "User", user.id, f"{user.username} ({user.role})")
            db.session.commit()
            flash(f"User {user.username} created.", "success")
            return redirect(url_for("main.users"))
    return render_template("form_page.html", form=form, title="Create user", back=url_for("main.users"))


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@roles_required(Role.ADMIN)
def user_edit(user_id):
    user = db.get_or_404(User, user_id)
    form = _user_form(user)
    if form.validate_on_submit() and _save_user(form, user, creating=False):
        audit.log("user.updated", "User", user.id, f"{user.username} ({user.role})")
        db.session.commit()
        flash("User updated. Leave the password blank to keep it unchanged.", "success")
        return redirect(url_for("main.users"))
    return render_template("form_page.html", form=form, title=f"Edit {user.username}", back=url_for("main.users"),
                           hint="Leave the password blank to keep the current one.")


@bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@roles_required(Role.ADMIN)
def user_toggle(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("You cannot deactivate your own account.", "danger")
    else:
        user.active = not user.active
        audit.log("user.activated" if user.active else "user.deactivated", "User", user.id, user.username)
        db.session.commit()
        flash(f"{user.username} is now {'active' if user.active else 'deactivated'}.", "success")
    return redirect(url_for("main.users"))


@bp.route("/audit")
@roles_required(Role.ADMIN)
def audit_log():
    q = request.args.get("q", "").strip()
    query = AuditLog.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(AuditLog.action.ilike(like), AuditLog.username.ilike(like), AuditLog.details.ilike(like)))
    page = query.order_by(AuditLog.id.desc()).paginate(page=request.args.get("page", 1, type=int),
                                                       per_page=30, error_out=False)
    return render_template("audit.html", page=page, q=q)


# ---------------------------------------------------------------- tickets
@bp.route("/tickets")
@login_required
def tickets():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    priority = request.args.get("priority", "")
    customer_id = request.args.get("customer", 0, type=int)
    query = ticket_service.filter_tickets(visible_tickets_query(current_user), q, status, priority,
                                          customer_id if current_user.role != Role.CUSTOMER else None)
    page = query.order_by(Ticket.created_at.desc()).paginate(page=request.args.get("page", 1, type=int),
                                                             per_page=PER_PAGE, error_out=False)
    customers_ = customer_choices(False) if current_user.role in (Role.ADMIN, Role.SUPPORT, Role.MANAGEMENT) else []
    return render_template("tickets.html", page=page, q=q, status=status, priority=priority,
                           customer_id=customer_id, customers=customers_)


@bp.route("/tickets/new", methods=["GET", "POST"])
@roles_required(*OFFICE)
def ticket_new():
    form = TicketForm()
    form.customer_id.choices = customer_choices()
    form.assigned_engineer_id.choices = engineer_choices()
    if form.validate_on_submit():
        fields = {k: form[k].data for k in ("customer_id", "title", "description", "product_name", "product_model",
                                            "serial_number", "priority", "target_date")}
        try:
            ticket = ticket_service.create_ticket(
                fields, [{"title": form.problem_title.data.strip(), "description": form.problem_description.data.strip(),
                          "category": form.problem_category.data}], current_user)
            if form.assigned_engineer_id.data:
                ticket_service.assign_engineer(ticket, form.assigned_engineer_id.data)
            audit.log("ticket.created", "Ticket", ticket.id, ticket.ticket_no)
            db.session.commit()
            flash(f"Ticket {ticket.ticket_no} created. Add more problems from the ticket page.", "success")
            return redirect(url_for("main.ticket_detail", ticket_id=ticket.id))
        except WorkflowError as e:
            db.session.rollback()
            flash(str(e), "danger")
    return render_template("form_page.html", form=form, title="Create ticket", back=url_for("main.tickets"))


@bp.route("/tickets/<int:ticket_id>")
@login_required
def ticket_detail(ticket_id):
    ticket = get_ticket_or_404(ticket_id, current_user)
    assign_form = None
    if current_user.role in OFFICE and ticket.status not in LOCKED_FOR_OFFICE:
        assign_form = AssignForm(engineer_id=ticket.assigned_engineer_id or 0)
        assign_form.engineer_id.choices = engineer_choices()
    decision_form = DecisionForm() if current_user.role == Role.MANAGEMENT and ticket.status == Status.PENDING_MANAGEMENT else None
    template = "ticket_customer.html" if current_user.role == Role.CUSTOMER else "ticket_detail.html"
    return render_template(template, ticket=ticket, assign_form=assign_form, decision_form=decision_form,
                           can_download=can_customer_download(ticket),
                           can_edit_ticket=current_user.role in OFFICE and ticket.status not in LOCKED_FOR_OFFICE)


@bp.route("/tickets/<int:ticket_id>/edit", methods=["GET", "POST"])
@roles_required(*OFFICE)
def ticket_edit(ticket_id):
    ticket = get_ticket_or_404(ticket_id, current_user)
    if ticket.status in LOCKED_FOR_OFFICE:
        flash("Tickets cannot be edited while awaiting Management, approved or closed.", "warning")
        return redirect(url_for("main.ticket_detail", ticket_id=ticket.id))
    form = TicketForm(obj=ticket, editing=True)
    form.customer_id.choices = customer_choices(active_only=False)
    if form.validate_on_submit():
        form.populate_obj(ticket)
        audit.log("ticket.updated", "Ticket", ticket.id, ticket.ticket_no)
        db.session.commit()
        flash("Ticket updated.", "success")
        return redirect(url_for("main.ticket_detail", ticket_id=ticket.id))
    return render_template("form_page.html", form=form, title=f"Edit {ticket.ticket_no}",
                           back=url_for("main.ticket_detail", ticket_id=ticket.id))


@bp.route("/tickets/<int:ticket_id>/assign", methods=["POST"])
@roles_required(*OFFICE)
def ticket_assign(ticket_id):
    ticket = get_ticket_or_404(ticket_id, current_user)
    form = AssignForm()
    form.engineer_id.choices = engineer_choices()
    if form.validate_on_submit():
        try:
            engineer = ticket_service.assign_engineer(ticket, form.engineer_id.data)
            audit.log("ticket.assigned", "Ticket", ticket.id, f"{ticket.ticket_no} -> {engineer.username if engineer else 'unassigned'}")
            db.session.commit()
            flash("Assignment saved.", "success")
        except WorkflowError as e:
            db.session.rollback()
            flash(str(e), "danger")
    else:
        flash_errors(form)
    return redirect(url_for("main.ticket_detail", ticket_id=ticket.id))


@bp.route("/tickets/<int:ticket_id>/problems/new", methods=["GET", "POST"])
@roles_required(*OFFICE)
def problem_new(ticket_id):
    ticket = get_ticket_or_404(ticket_id, current_user)
    form = ProblemForm()
    if form.validate_on_submit():
        try:
            p = ticket_service.add_problem(ticket, form.title.data.strip(), form.description.data.strip(), form.category.data)
            db.session.flush()
            audit.log("problem.created", "Problem", p.id, f"{ticket.ticket_no}: {p.title}")
            db.session.commit()
            flash("Problem added to the ticket.", "success")
            return redirect(url_for("main.ticket_detail", ticket_id=ticket.id))
        except WorkflowError as e:
            db.session.rollback()
            flash(str(e), "danger")
            return redirect(url_for("main.ticket_detail", ticket_id=ticket.id))
    return render_template("form_page.html", form=form, title=f"Add problem to {ticket.ticket_no}",
                           back=url_for("main.ticket_detail", ticket_id=ticket.id))


@bp.route("/problems/<int:problem_id>/edit", methods=["GET", "POST"])
@roles_required(*OFFICE)
def problem_edit(problem_id):
    problem = get_problem(problem_id)
    if problem.ticket.status in LOCKED_FOR_OFFICE:
        flash("This problem can no longer be edited.", "warning")
        return redirect(url_for("main.ticket_detail", ticket_id=problem.ticket_id))
    form = ProblemForm(obj=problem)
    if form.validate_on_submit():
        form.populate_obj(problem)
        audit.log("problem.updated", "Problem", problem.id, problem.title)
        db.session.commit()
        flash("Problem updated.", "success")
        return redirect(url_for("main.ticket_detail", ticket_id=problem.ticket_id))
    return render_template("form_page.html", form=form, title="Edit problem",
                           back=url_for("main.ticket_detail", ticket_id=problem.ticket_id))


# ---------------------------------------------------------------- investigation
def _problem_page(problem, **forms):
    ticket = problem.ticket
    ctx = dict(
        problem=problem, ticket=ticket, inv=problem.investigation,
        editable=can_edit_investigation(current_user, ticket),
        inv_form=forms.get("inv_form") or InvestigationForm(obj=problem.investigation),
        upload_form=forms.get("upload_form") or UploadForm(),
        question_form=forms.get("question_form") or QuestionForm(),
        solution_form=SolutionForm(), justification_form=JustificationForm(), result_form=ResultSummaryForm(),
        data_files=[f for f in problem.test_files if f.kind == "data"],
    )
    return render_template("problem_detail.html", **ctx)


@bp.route("/problems/<int:problem_id>")
@login_required
def problem_detail(problem_id):
    return _problem_page(get_problem(problem_id))


@bp.route("/problems/<int:problem_id>/investigation", methods=["POST"])
@login_required
def investigation_save(problem_id):
    problem = get_editable_problem(problem_id)
    form = InvestigationForm()
    if form.validate_on_submit():
        try:
            changed = inv_service.save_investigation(problem, {k: form[k].data for k in form._fields if k != "submit"}, current_user)
            audit.log("investigation.updated", "Problem", problem.id, f"{problem.ticket.ticket_no} stage {problem.stage}")
            db.session.commit()
            flash("Investigation saved." if changed else "Nothing changed.", "success" if changed else "info")
        except WorkflowError as e:
            db.session.rollback()
            flash(str(e), "danger")
        return back_to_problem(problem, "investigation")
    flash_errors(form)
    return _problem_page(problem, inv_form=form)


@bp.route("/problems/<int:problem_id>/files", methods=["POST"])
@login_required
def file_upload(problem_id):
    problem = get_editable_problem(problem_id)
    form = UploadForm()
    if not form.validate_on_submit():
        flash_errors(form)
        return back_to_problem(problem, "evidence")
    try:
        name, ext, data, mimetype = data_service.validate_upload(request.files.get("file"), form.kind.data)
        stats_json = None
        if form.kind.data == "data":
            df = data_service.load_dataframe(data, ext)
            stats_json = data_service.stats_to_json(df)
        tf = TestFile(problem=problem, uploaded_by_id=current_user.id, kind=form.kind.data, filename=name,
                      extension=ext, mimetype=mimetype, size=len(data), data=data,
                      description=(form.description.data or "").strip())
        db.session.add(tf)
        db.session.flush()
        if stats_json:
            db.session.add(TestResult(problem=problem, test_file=tf, title=f"Summary of {name}",
                                      stats_json=stats_json, created_by_id=current_user.id))
        inv_service.touch(problem, current_user, f"Uploaded {form.kind.data} file {name}.")
        audit.log("file.uploaded", "TestFile", tf.id, f"{problem.ticket.ticket_no}: {name} ({len(data)} bytes)")
        db.session.commit()
        flash(f"{name} uploaded.", "success")
    except WorkflowError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return back_to_problem(problem, "evidence")


@bp.route("/files/<int:file_id>/download")
@login_required
def file_download(file_id):
    f = get_file(file_id)
    audit.log("file.downloaded", "TestFile", f.id, f.filename)
    db.session.commit()
    return send_file(io.BytesIO(f.data), mimetype=f.mimetype, as_attachment=f.kind != "image",
                     download_name=f.filename, max_age=0)


@bp.route("/files/<int:file_id>/delete", methods=["POST"])
@login_required
def file_delete(file_id):
    f = get_file(file_id)
    problem = get_editable_problem(f.problem_id)
    name = f.filename
    db.session.delete(f)
    inv_service.touch(problem, current_user, f"Removed file {name}.")
    audit.log("file.deleted", "TestFile", file_id, name)
    db.session.commit()
    flash(f"{name} removed.", "success")
    return back_to_problem(problem, "evidence")


@bp.route("/files/<int:file_id>/preview")
@login_required
def file_preview(file_id):
    f = get_file(file_id)
    if f.kind != "data":
        abort(404)
    try:
        return jsonify(data_service.preview(data_service.load_dataframe(f.data, f.extension)))
    except WorkflowError as e:
        return jsonify(error=str(e)), 400


@bp.route("/files/<int:file_id>/chart-data")
@login_required
def file_chart_data(file_id):
    f = get_file(file_id)
    if f.kind != "data":
        abort(404)
    try:
        df = data_service.load_dataframe(f.data, f.extension)
        return jsonify(data_service.chart_payload(df, request.args.get("x", ""), request.args.get("y", ""),
                                                  request.args.get("type", "line")))
    except WorkflowError as e:
        return jsonify(error=str(e)), 400


@bp.route("/problems/<int:problem_id>/graphs", methods=["POST"])
@login_required
def graph_create(problem_id):
    problem = get_editable_problem(problem_id)
    form = GraphForm()
    if not form.validate_on_submit():
        flash_errors(form)
        return back_to_problem(problem, "graphs")
    tf = TestFile.query.filter_by(id=int(form.test_file_id.data or 0), problem_id=problem.id, kind="data").first()
    if tf is None:
        flash("Choose one of this problem's data files.", "danger")
        return back_to_problem(problem, "graphs")
    try:
        data_service.chart_payload(data_service.load_dataframe(tf.data, tf.extension), form.x_column.data,
                                   form.y_column.data, form.chart_type.data)
    except WorkflowError as e:
        flash(str(e), "danger")
        return back_to_problem(problem, "graphs")
    g = GraphConfiguration(problem=problem, test_file=tf, title=form.title.data.strip(), chart_type=form.chart_type.data,
                           x_column=form.x_column.data, y_column=form.y_column.data,
                           explanation=form.explanation.data.strip(), created_by_id=current_user.id)
    db.session.add(g)
    inv_service.touch(problem, current_user, f"Added graph '{g.title}'.")
    audit.log("graph.created", "Problem", problem.id, g.title)
    db.session.commit()
    flash("Graph saved.", "success")
    return back_to_problem(problem, "graphs")


@bp.route("/graphs/<int:graph_id>/data")
@login_required
def graph_data(graph_id):
    g = db.get_or_404(GraphConfiguration, graph_id)
    get_problem(g.problem_id)
    try:
        df = data_service.load_dataframe(g.test_file.data, g.test_file.extension)
        return jsonify(data_service.chart_payload(df, g.x_column, g.y_column, g.chart_type))
    except WorkflowError as e:
        return jsonify(error=str(e)), 400


@bp.route("/graphs/<int:graph_id>/delete", methods=["POST"])
@login_required
def graph_delete(graph_id):
    g = db.get_or_404(GraphConfiguration, graph_id)
    problem = get_editable_problem(g.problem_id)
    db.session.delete(g)
    inv_service.touch(problem, current_user, "Removed a graph.")
    db.session.commit()
    flash("Graph removed.", "success")
    return back_to_problem(problem, "graphs")


@bp.route("/results/<int:result_id>", methods=["POST"])
@login_required
def result_update(result_id):
    r = db.get_or_404(TestResult, result_id)
    problem = get_editable_problem(r.problem_id)
    form = ResultSummaryForm()
    if form.validate_on_submit():
        r.title, r.summary = form.title.data.strip(), (form.summary.data or "").strip()
        inv_service.touch(problem, current_user, f"Updated test result '{r.title}'.")
        db.session.commit()
        flash("Test result updated.", "success")
    else:
        flash_errors(form)
    return back_to_problem(problem, "graphs")


@bp.route("/problems/<int:problem_id>/questions", methods=["POST"])
@login_required
def question_create(problem_id):
    problem = get_editable_problem(problem_id)
    form = QuestionForm()
    if not form.validate_on_submit():
        flash_errors(form)
        return _problem_page(problem, question_form=form)
    q = TechnicalQuestion(problem=problem, created_by_id=current_user.id, question=form.question.data.strip(),
                          supporting_evidence=(form.supporting_evidence.data or "").strip(),
                          test_result=(form.test_result.data or "").strip(),
                          conclusion=(form.conclusion.data or "").strip(),
                          proposed_action=(form.proposed_action.data or "").strip())
    q.justifications.append(TechnicalJustification(text=form.justification.data.strip(), created_by_id=current_user.id))
    db.session.add(q)
    inv_service.touch(problem, current_user, "Technical question added.")
    db.session.commit()
    flash("Question added.", "success")
    return back_to_problem(problem, "questions")


@bp.route("/questions/<int:question_id>/justifications", methods=["POST"])
@login_required
def justification_create(question_id):
    q = db.get_or_404(TechnicalQuestion, question_id)
    problem = get_editable_problem(q.problem_id)
    form = JustificationForm()
    if form.validate_on_submit():
        q.justifications.append(TechnicalJustification(text=form.text.data.strip(), created_by_id=current_user.id))
        inv_service.touch(problem, current_user, "Justification added.")
        db.session.commit()
        flash("Justification added.", "success")
    else:
        flash_errors(form)
    return back_to_problem(problem, "questions")


@bp.route("/questions/<int:question_id>/delete", methods=["POST"])
@login_required
def question_delete(question_id):
    q = db.get_or_404(TechnicalQuestion, question_id)
    problem = get_editable_problem(q.problem_id)
    db.session.delete(q)
    inv_service.touch(problem, current_user, "Technical question removed.")
    db.session.commit()
    flash("Question removed.", "success")
    return back_to_problem(problem, "questions")


@bp.route("/problems/<int:problem_id>/solutions", methods=["POST"])
@login_required
def solution_create(problem_id):
    problem = get_editable_problem(problem_id)
    form = SolutionForm()
    if form.validate_on_submit():
        inv_service.add_solution(problem, form.description.data.strip(), current_user)
        db.session.commit()
        flash("Solution added.", "success")
    else:
        flash_errors(form)
    return back_to_problem(problem, "solutions")


@bp.route("/solutions/<int:solution_id>/delete", methods=["POST"])
@login_required
def solution_delete(solution_id):
    s = db.get_or_404(ProposedSolution, solution_id)
    problem = get_editable_problem(s.problem_id)
    db.session.delete(s)
    inv_service.touch(problem, current_user, "Proposed solution removed.")
    db.session.commit()
    flash("Solution removed.", "success")
    return back_to_problem(problem, "solutions")


# ---------------------------------------------------------------- approval and reports
@bp.route("/tickets/<int:ticket_id>/submit", methods=["POST"])
@roles_required(Role.ENGINEER)
def ticket_submit(ticket_id):
    ticket = get_ticket_or_404(ticket_id, current_user)
    try:
        approval_service.submit_to_management(ticket, current_user)
        db.session.commit()
        flash("Investigation submitted to Management.", "success")
    except WorkflowError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("main.ticket_detail", ticket_id=ticket.id))


@bp.route("/tickets/<int:ticket_id>/decision", methods=["POST"])
@roles_required(Role.MANAGEMENT)
def ticket_decision(ticket_id):
    ticket = get_ticket_or_404(ticket_id, current_user)
    form = DecisionForm()
    if form.validate_on_submit():
        try:
            approval_service.decide(ticket, current_user, form.decision.data, form.comments.data)
            db.session.commit()
            flash("Decision recorded." if form.decision.data == "APPROVED" else "Returned to the engineer for rework.", "success")
        except WorkflowError as e:
            db.session.rollback()
            flash(str(e), "danger")
    else:
        flash_errors(form)
    return redirect(url_for("main.ticket_detail", ticket_id=ticket.id))


@bp.route("/tickets/<int:ticket_id>/report", methods=["POST"])
@roles_required(Role.ADMIN, Role.SUPPORT, Role.MANAGEMENT)
def report_generate(ticket_id):
    ticket = get_ticket_or_404(ticket_id, current_user)
    try:
        report = report_service.generate_report(ticket, current_user)
        db.session.flush()
        audit.log("report.generated", "FinalReport", report.id, f"{ticket.ticket_no} v{report.version}")
        db.session.commit()
        flash(f"Final report version {report.version} generated.", "success")
    except WorkflowError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("main.ticket_detail", ticket_id=ticket.id))


@bp.route("/reports/<int:report_id>/download")
@login_required
def report_download(report_id):
    report = db.get_or_404(FinalReport, report_id)
    ticket = report.ticket
    if not can_view_ticket(current_user, ticket):
        abort(404)
    if current_user.role == Role.CUSTOMER and not (can_customer_download(ticket) and report.id == ticket.latest_report.id):
        abort(404)
    audit.log("report.downloaded", "FinalReport", report.id, f"{ticket.ticket_no} v{report.version}")
    db.session.commit()
    return send_file(io.BytesIO(report.pdf_data), mimetype="application/pdf", as_attachment=True,
                     download_name=report.filename, max_age=0)


@bp.route("/tickets/<int:ticket_id>/close", methods=["POST"])
@roles_required(Role.ADMIN, Role.SUPPORT, Role.MANAGEMENT)
def ticket_close(ticket_id):
    ticket = get_ticket_or_404(ticket_id, current_user)
    try:
        ticket_service.close_ticket(ticket)
        audit.log("ticket.closed", "Ticket", ticket.id, ticket.ticket_no)
        db.session.commit()
        flash("Ticket closed.", "success")
    except WorkflowError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("main.ticket_detail", ticket_id=ticket.id))

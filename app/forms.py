import re

from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import (DateField, EmailField, HiddenField, PasswordField, SelectField, StringField, SubmitField,
                     TextAreaField, ValidationError)
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional, Regexp

from .models import CATEGORIES, CHART_TYPES, FILE_KINDS, PRIORITIES, Role, STAGES

PHONE_RE = r"^[0-9+()\-\s.]{6,30}$"


def strong_password(form, field):
    pw = field.data or ""
    if len(pw) < 10:
        raise ValidationError("Use at least 10 characters.")
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)):
        raise ValidationError("Include both letters and numbers.")


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=64)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=128)])
    submit = SubmitField("Sign in")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    new_password = PasswordField("New password", validators=[DataRequired(), strong_password])
    confirm = PasswordField("Confirm new password", validators=[DataRequired(), EqualTo("new_password", "Passwords do not match.")])
    submit = SubmitField("Change password")


class UserForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=64),
                                                   Regexp(r"^[A-Za-z0-9_.-]+$", message="Letters, numbers, dot, dash and underscore only.")])
    full_name = StringField("Full name", validators=[DataRequired(), Length(max=120)])
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=254)])
    role = SelectField("Role", choices=[(r, r.title()) for r in Role.ALL])
    customer_id = SelectField("Customer (for customer logins)", coerce=int, default=0)
    password = PasswordField("Password", validators=[Optional(), strong_password])
    submit = SubmitField("Save user")

    def validate_customer_id(self, field):
        if self.role.data == Role.CUSTOMER and not field.data:
            raise ValidationError("Customer logins must be linked to a customer.")


class CustomerForm(FlaskForm):
    company_name = StringField("Company name", validators=[DataRequired(), Length(max=160)])
    contact_person = StringField("Contact person", validators=[DataRequired(), Length(max=120)])
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=254)])
    phone = StringField("Phone number", validators=[DataRequired(), Regexp(PHONE_RE, message="Enter a valid phone number.")])
    address = TextAreaField("Address", validators=[DataRequired(), Length(max=500)])
    country = StringField("Country", validators=[DataRequired(), Length(max=80)])
    submit = SubmitField("Save customer")


class TicketForm(FlaskForm):
    customer_id = SelectField("Customer", coerce=int, validators=[DataRequired()])
    title = StringField("Ticket title", validators=[DataRequired(), Length(max=200)])
    description = TextAreaField("Description", validators=[DataRequired(), Length(max=4000)])
    product_name = StringField("Product name", validators=[DataRequired(), Length(max=120)])
    product_model = StringField("Product model", validators=[DataRequired(), Length(max=120)])
    serial_number = StringField("Serial number", validators=[DataRequired(), Length(max=120)])
    priority = SelectField("Priority", choices=[(p, p.title()) for p in PRIORITIES], default="MEDIUM")
    target_date = DateField("Target resolution date", validators=[Optional()])
    assigned_engineer_id = SelectField("Assign engineer", coerce=int, default=0)
    # First problem, so a ticket always starts with at least one issue.
    problem_title = StringField("First problem: title", validators=[Length(max=200)])
    problem_description = TextAreaField("First problem: description", validators=[Length(max=4000)])
    problem_category = SelectField("First problem: category", choices=[(c, c) for c in CATEGORIES])
    submit = SubmitField("Save ticket")

    def __init__(self, *a, editing=False, **kw):
        super().__init__(*a, **kw)
        self.editing = editing
        if editing:
            for name in ("problem_title", "problem_description", "problem_category", "assigned_engineer_id"):
                delattr(self, name)

    def validate_problem_title(self, field):
        if not self.editing and not (field.data or "").strip():
            raise ValidationError("Add the first problem so engineers know what to investigate.")

    def validate_problem_description(self, field):
        if not self.editing and not (field.data or "").strip():
            raise ValidationError("Describe the first problem.")


class ProblemForm(FlaskForm):
    title = StringField("Problem title", validators=[DataRequired(), Length(max=200)])
    category = SelectField("Category", choices=[(c, c) for c in CATEGORIES])
    description = TextAreaField("Description", validators=[DataRequired(), Length(max=4000)])
    submit = SubmitField("Save problem")


class AssignForm(FlaskForm):
    engineer_id = SelectField("Engineer", coerce=int)
    submit = SubmitField("Assign")


class InvestigationForm(FlaskForm):
    stage = SelectField("Investigation stage", coerce=int,
                        choices=[(k, f"{k}. {v}") for k, v in STAGES.items() if k != 7])
    problem_description = TextAreaField("Problem description (engineering view)", validators=[Length(max=8000)])
    observations = TextAreaField("Technical observations", validators=[Length(max=8000)])
    suspected_root_cause = TextAreaField("Suspected root cause", validators=[Length(max=8000)])
    confirmed_root_cause = TextAreaField("Confirmed root cause", validators=[Length(max=8000)])
    corrective_actions = TextAreaField("Corrective actions", validators=[Length(max=8000)])
    preventive_actions = TextAreaField("Preventive actions", validators=[Length(max=8000)])
    technical_comments = TextAreaField("Technical comments", validators=[Length(max=8000)])
    conclusion = TextAreaField("Conclusion for the customer report", validators=[Length(max=8000)])
    note = StringField("Update note (shown in history)", validators=[Length(max=500)])
    submit = SubmitField("Save investigation")


class UploadForm(FlaskForm):
    kind = SelectField("Category", choices=[("data", "Test data (CSV or XLSX)"), ("image", "Image (PNG or JPG)"),
                                            ("document", "Document (PDF, TXT or DOCX)")])
    file = FileField("File")
    description = StringField("Description", validators=[Length(max=300)])
    submit = SubmitField("Upload")


class GraphForm(FlaskForm):
    test_file_id = HiddenField(validators=[DataRequired()])
    title = StringField("Graph title", validators=[DataRequired(), Length(max=200)])
    chart_type = SelectField("Chart type", choices=[(c, c.title()) for c in CHART_TYPES])
    x_column = StringField("X axis", validators=[DataRequired(), Length(max=200)])
    y_column = StringField("Y axis", validators=[DataRequired(), Length(max=200)])
    explanation = TextAreaField("What this graph shows", validators=[DataRequired(), Length(max=3000)])


class ResultSummaryForm(FlaskForm):
    title = StringField("Result title", validators=[DataRequired(), Length(max=200)])
    summary = TextAreaField("Test result summary", validators=[Length(max=4000)])


class QuestionForm(FlaskForm):
    question = TextAreaField("Technical question", validators=[DataRequired(), Length(max=3000)])
    justification = TextAreaField("Technical justification", validators=[DataRequired(), Length(max=4000)])
    supporting_evidence = TextAreaField("Supporting evidence", validators=[Length(max=4000)])
    test_result = TextAreaField("Test result", validators=[Length(max=4000)])
    conclusion = TextAreaField("Conclusion", validators=[Length(max=4000)])
    proposed_action = TextAreaField("Proposed action", validators=[Length(max=4000)])


class JustificationForm(FlaskForm):
    text = TextAreaField("Additional justification", validators=[DataRequired(), Length(max=4000)])


class SolutionForm(FlaskForm):
    description = TextAreaField("Proposed solution", validators=[DataRequired(), Length(max=4000)])


class DecisionForm(FlaskForm):
    decision = SelectField("Decision", choices=[("APPROVED", "Approve"), ("RETURNED", "Return for rework")])
    comments = TextAreaField("Management comments", validators=[Length(max=4000)])
    submit = SubmitField("Record decision")

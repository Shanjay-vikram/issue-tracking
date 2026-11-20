"""SQLAlchemy models for HardwareCare."""
import json
import uuid
from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy.orm import deferred
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite and PostgreSQL then behave identically)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role:
    ADMIN = "ADMIN"
    SUPPORT = "SUPPORT"
    ENGINEER = "ENGINEER"
    MANAGEMENT = "MANAGEMENT"
    CUSTOMER = "CUSTOMER"
    ALL = (ADMIN, SUPPORT, ENGINEER, MANAGEMENT, CUSTOMER)
    STAFF = (ADMIN, SUPPORT, ENGINEER, MANAGEMENT)


class Status:
    OPEN = "OPEN"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    INVESTIGATION = "INVESTIGATION"
    PENDING_MANAGEMENT = "PENDING_MANAGEMENT"
    REWORK = "REWORK"
    APPROVED = "APPROVED"
    CLOSED = "CLOSED"
    ALL = (OPEN, ASSIGNED, IN_PROGRESS, INVESTIGATION, PENDING_MANAGEMENT, REWORK, APPROVED, CLOSED)
    # Statuses in which the engineer may change investigation data.
    ENGINEER_EDITABLE = (ASSIGNED, IN_PROGRESS, INVESTIGATION, REWORK)


PRIORITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
CATEGORIES = ("Electrical", "Mechanical", "Thermal", "Firmware", "Power supply",
              "Connectivity", "Cosmetic", "Other")
STAGES = {
    1: "Initial Analysis",
    2: "Data Collection",
    3: "Testing",
    4: "Root Cause Analysis",
    5: "Solution Development",
    6: "Validation",
    7: "Submitted to Management",
}
CHART_TYPES = ("line", "bar", "scatter")
FILE_KINDS = ("data", "image", "document")


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), nullable=False, unique=True, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=True, index=True)
    failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login = db.Column(db.DateTime, nullable=True)

    customer = db.relationship("Customer", back_populates="users")

    @property
    def is_active(self):  # Flask-Login hook
        return bool(self.active)

    @property
    def is_locked(self):
        return bool(self.locked_until and self.locked_until > utcnow())

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def has_role(self, *roles) -> bool:
        return self.role in roles

    def __repr__(self):
        return f"<User {self.username} {self.role}>"


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


class Customer(TimestampMixin, db.Model):
    __tablename__ = "customers"
    id = db.Column(db.Integer, primary_key=True)
    customer_code = db.Column(db.String(20), nullable=False, unique=True, index=True)
    company_name = db.Column(db.String(160), nullable=False, index=True)
    contact_person = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), nullable=False)
    phone = db.Column(db.String(30), nullable=False)
    address = db.Column(db.Text, nullable=False)
    country = db.Column(db.String(80), nullable=False, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    users = db.relationship("User", back_populates="customer")
    tickets = db.relationship("Ticket", back_populates="customer")

    @staticmethod
    def new_placeholder_code():
        return "TMP-" + uuid.uuid4().hex[:14]

    def finalize_code(self):
        self.customer_code = f"CUST-{self.id:05d}"


class Ticket(TimestampMixin, db.Model):
    __tablename__ = "tickets"
    id = db.Column(db.Integer, primary_key=True)
    ticket_no = db.Column(db.String(24), nullable=False, unique=True, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    product_name = db.Column(db.String(120), nullable=False)
    product_model = db.Column(db.String(120), nullable=False)
    serial_number = db.Column(db.String(120), nullable=False, index=True)
    priority = db.Column(db.String(10), nullable=False, default="MEDIUM", index=True)
    status = db.Column(db.String(24), nullable=False, default=Status.OPEN, index=True)
    target_date = db.Column(db.Date, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assigned_engineer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    customer = db.relationship("Customer", back_populates="tickets")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    assigned_engineer = db.relationship("User", foreign_keys=[assigned_engineer_id])
    problems = db.relationship("Problem", back_populates="ticket", order_by="Problem.id",
                               cascade="all, delete-orphan")
    approvals = db.relationship("ManagementApproval", back_populates="ticket",
                                order_by="ManagementApproval.id", cascade="all, delete-orphan")
    reports = db.relationship("FinalReport", back_populates="ticket", order_by="FinalReport.version",
                              cascade="all, delete-orphan")

    @staticmethod
    def new_placeholder_no():
        return "TMP-" + uuid.uuid4().hex[:14]

    def finalize_no(self):
        year = (self.created_at or utcnow()).year
        self.ticket_no = f"TKT-{year}-{self.id:05d}"

    @property
    def latest_report(self):
        return self.reports[-1] if self.reports else None

    @property
    def last_approval(self):
        for a in reversed(self.approvals):
            if a.decision == "APPROVED":
                return a
        return None


class Problem(TimestampMixin, db.Model):
    __tablename__ = "problems"
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(40), nullable=False, default="Other", index=True)

    ticket = db.relationship("Ticket", back_populates="problems")
    investigation = db.relationship("Investigation", back_populates="problem", uselist=False,
                                    cascade="all, delete-orphan")
    test_files = db.relationship("TestFile", back_populates="problem", order_by="TestFile.id",
                                 cascade="all, delete-orphan")
    test_results = db.relationship("TestResult", back_populates="problem", order_by="TestResult.id",
                                   cascade="all, delete-orphan")
    graphs = db.relationship("GraphConfiguration", back_populates="problem",
                             order_by="GraphConfiguration.id", cascade="all, delete-orphan")
    questions = db.relationship("TechnicalQuestion", back_populates="problem",
                                order_by="TechnicalQuestion.id", cascade="all, delete-orphan")
    solutions = db.relationship("ProposedSolution", back_populates="problem",
                                order_by="ProposedSolution.id", cascade="all, delete-orphan")

    @property
    def stage(self):
        return self.investigation.stage if self.investigation else 1


class Investigation(TimestampMixin, db.Model):
    __tablename__ = "investigations"
    id = db.Column(db.Integer, primary_key=True)
    problem_id = db.Column(db.Integer, db.ForeignKey("problems.id"), nullable=False, unique=True)
    stage = db.Column(db.Integer, nullable=False, default=1)
    problem_description = db.Column(db.Text, nullable=False, default="")
    observations = db.Column(db.Text, nullable=False, default="")
    suspected_root_cause = db.Column(db.Text, nullable=False, default="")
    confirmed_root_cause = db.Column(db.Text, nullable=False, default="")
    corrective_actions = db.Column(db.Text, nullable=False, default="")
    preventive_actions = db.Column(db.Text, nullable=False, default="")
    technical_comments = db.Column(db.Text, nullable=False, default="")
    conclusion = db.Column(db.Text, nullable=False, default="")
    locked = db.Column(db.Boolean, nullable=False, default=False)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    problem = db.relationship("Problem", back_populates="investigation")
    updated_by = db.relationship("User")
    updates = db.relationship("InvestigationUpdate", back_populates="investigation",
                              order_by="InvestigationUpdate.id", cascade="all, delete-orphan")

    @property
    def stage_name(self):
        return STAGES.get(self.stage, "Unknown")


class InvestigationUpdate(db.Model):
    __tablename__ = "investigation_updates"
    id = db.Column(db.Integer, primary_key=True)
    investigation_id = db.Column(db.Integer, db.ForeignKey("investigations.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    stage = db.Column(db.Integer, nullable=False)
    note = db.Column(db.Text, nullable=False, default="")
    changed_fields = db.Column(db.String(400), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    investigation = db.relationship("Investigation", back_populates="updates")
    user = db.relationship("User")

    @property
    def stage_name(self):
        return STAGES.get(self.stage, "Unknown")


class TestFile(db.Model):
    """Evidence stored in the database so it survives Render's ephemeral disk."""
    __tablename__ = "test_files"
    __test__ = False  # not a pytest class
    id = db.Column(db.Integer, primary_key=True)
    problem_id = db.Column(db.Integer, db.ForeignKey("problems.id"), nullable=False, index=True)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    kind = db.Column(db.String(12), nullable=False)  # data | image | document
    filename = db.Column(db.String(200), nullable=False)
    extension = db.Column(db.String(8), nullable=False)
    mimetype = db.Column(db.String(100), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(300), nullable=False, default="")
    data = deferred(db.Column(db.LargeBinary, nullable=False))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    problem = db.relationship("Problem", back_populates="test_files")
    uploaded_by = db.relationship("User")
    results = db.relationship("TestResult", back_populates="test_file", cascade="all, delete-orphan")
    graphs = db.relationship("GraphConfiguration", back_populates="test_file", cascade="all, delete-orphan")


class TestResult(db.Model):
    __tablename__ = "test_results"
    __test__ = False
    id = db.Column(db.Integer, primary_key=True)
    problem_id = db.Column(db.Integer, db.ForeignKey("problems.id"), nullable=False, index=True)
    test_file_id = db.Column(db.Integer, db.ForeignKey("test_files.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    summary = db.Column(db.Text, nullable=False, default="")
    stats_json = db.Column(db.Text, nullable=False, default="{}")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    problem = db.relationship("Problem", back_populates="test_results")
    test_file = db.relationship("TestFile", back_populates="results")

    @property
    def stats(self):
        try:
            return json.loads(self.stats_json or "{}")
        except ValueError:
            return {}


class GraphConfiguration(db.Model):
    __tablename__ = "graph_configurations"
    id = db.Column(db.Integer, primary_key=True)
    problem_id = db.Column(db.Integer, db.ForeignKey("problems.id"), nullable=False, index=True)
    test_file_id = db.Column(db.Integer, db.ForeignKey("test_files.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    chart_type = db.Column(db.String(10), nullable=False)
    x_column = db.Column(db.String(200), nullable=False)
    y_column = db.Column(db.String(200), nullable=False)
    explanation = db.Column(db.Text, nullable=False, default="")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    problem = db.relationship("Problem", back_populates="graphs")
    test_file = db.relationship("TestFile", back_populates="graphs")


class TechnicalQuestion(TimestampMixin, db.Model):
    __tablename__ = "technical_questions"
    id = db.Column(db.Integer, primary_key=True)
    problem_id = db.Column(db.Integer, db.ForeignKey("problems.id"), nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    supporting_evidence = db.Column(db.Text, nullable=False, default="")
    test_result = db.Column(db.Text, nullable=False, default="")
    conclusion = db.Column(db.Text, nullable=False, default="")
    proposed_action = db.Column(db.Text, nullable=False, default="")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    problem = db.relationship("Problem", back_populates="questions")
    justifications = db.relationship("TechnicalJustification", back_populates="question",
                                     order_by="TechnicalJustification.id", cascade="all, delete-orphan")


class TechnicalJustification(db.Model):
    __tablename__ = "technical_justifications"
    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer, db.ForeignKey("technical_questions.id"), nullable=False, index=True)
    text = db.Column(db.Text, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    question = db.relationship("TechnicalQuestion", back_populates="justifications")


class ProposedSolution(db.Model):
    __tablename__ = "proposed_solutions"
    id = db.Column(db.Integer, primary_key=True)
    problem_id = db.Column(db.Integer, db.ForeignKey("problems.id"), nullable=False, index=True)
    description = db.Column(db.Text, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    problem = db.relationship("Problem", back_populates="solutions")


class ManagementApproval(db.Model):
    """Append-only trail of every Management decision on a ticket."""
    __tablename__ = "management_approvals"
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    decision = db.Column(db.String(10), nullable=False)  # APPROVED | RETURNED
    comments = db.Column(db.Text, nullable=False, default="")
    decided_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    ticket = db.relationship("Ticket", back_populates="approvals")
    decided_by = db.relationship("User")


class FinalReport(db.Model):
    __tablename__ = "final_reports"
    __table_args__ = (db.UniqueConstraint("ticket_id", "version", name="uq_report_ticket_version"),)
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    filename = db.Column(db.String(200), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    pdf_data = deferred(db.Column(db.LargeBinary, nullable=False))
    generated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    ticket = db.relationship("Ticket", back_populates="reports")
    generated_by = db.relationship("User")


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    username = db.Column(db.String(64), nullable=False, default="anonymous")
    action = db.Column(db.String(60), nullable=False, index=True)
    entity_type = db.Column(db.String(40), nullable=False, default="")
    entity_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.String(500), nullable=False, default="")
    ip_address = db.Column(db.String(64), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    user = db.relationship("User")

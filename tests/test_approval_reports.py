import io

import pytest
from pypdf import PdfReader

from app.extensions import db
from app.models import FinalReport, ManagementApproval, Problem, Status, Ticket
from conftest import csv_bytes, fill_investigation, make_ticket, upload

UNICODE_TEXT = "Tamil தமிழ் & <b>unclosed <i>markup</script> Ünïcode"


def submitted_ticket(world, as_user, **kw):
    t = make_ticket(world, **kw)
    fill_investigation(t)
    assert as_user("engineer").post(f"/tickets/{t.id}/submit").status_code == 302
    return db.session.get(Ticket, t.id)


def approve(as_user, t, comments="Looks good"):
    return as_user("manager").post(f"/tickets/{t.id}/decision", data={"decision": "APPROVED", "comments": comments})


def test_submit_is_blocked_until_complete(app, world, as_user):
    t = make_ticket(world)
    c = as_user("engineer")
    r = c.post(f"/tickets/{t.id}/submit", follow_redirects=True)
    assert b"Complete these items first" in r.data and b"proposed solution" in r.data
    assert db.session.get(Ticket, t.id).status == Status.ASSIGNED


def test_submit_locks_investigation_and_sets_stage_seven(app, world, as_user):
    t = submitted_ticket(world, as_user)
    assert t.status == Status.PENDING_MANAGEMENT
    assert all(p.investigation.stage == 7 and p.investigation.locked for p in t.problems)
    c = as_user("engineer")
    c.post(f"/problems/{t.problems[0].id}/investigation", data={"stage": 3, "observations": "edit while locked"})
    db.session.expire_all()
    assert db.session.get(Problem, t.problems[0].id).investigation.observations.startswith("Observed brown-out")
    assert c.post(f"/tickets/{t.id}/submit", follow_redirects=True).status_code == 200  # cannot resubmit while pending
    assert b"Management decision" in as_user("manager").get(f"/tickets/{t.id}").data


def test_rework_flow_and_audit_trail(app, world, as_user):
    t = submitted_ticket(world, as_user)
    m = as_user("manager")
    r = m.post(f"/tickets/{t.id}/decision", data={"decision": "RETURNED", "comments": ""}, follow_redirects=True)
    assert b"Explain what needs rework" in r.data and db.session.get(Ticket, t.id).status == Status.PENDING_MANAGEMENT
    m.post(f"/tickets/{t.id}/decision", data={"decision": "RETURNED", "comments": "Add thermal test evidence"})
    t = db.session.get(Ticket, t.id)
    assert t.status == Status.REWORK and all(not p.investigation.locked and p.investigation.stage == 6 for p in t.problems)
    page = as_user("engineer").get(f"/tickets/{t.id}").get_data(as_text=True)
    assert "Add thermal test evidence" in page
    e = as_user("engineer")
    inv = t.problems[0].investigation
    full = {f: getattr(inv, f) for f in ("problem_description", "suspected_root_cause", "confirmed_root_cause", "corrective_actions",
                                          "preventive_actions", "technical_comments", "conclusion")}
    e.post(f"/problems/{t.problems[0].id}/investigation", data=dict(full, stage=6, observations="Thermal test added", note="rework"))
    assert db.session.get(Ticket, t.id).status == Status.REWORK  # stays until resubmitted
    assert e.post(f"/tickets/{t.id}/submit").status_code == 302
    assert db.session.get(Ticket, t.id).status == Status.PENDING_MANAGEMENT
    assert approve(as_user, t).status_code == 302
    t = db.session.get(Ticket, t.id)
    assert t.status == Status.APPROVED
    trail = ManagementApproval.query.order_by(ManagementApproval.id).all()
    assert [a.decision for a in trail] == ["RETURNED", "APPROVED"] and trail[1].decided_by_id == world["manager"].id and trail[1].created_at
    assert all(p.investigation.locked for p in t.problems)
    e.post(f"/problems/{t.problems[0].id}/investigation", data=dict(stage=2, observations="after approval"))
    db.session.expire_all()
    assert "after approval" not in db.session.get(Problem, t.problems[0].id).investigation.observations


def test_decision_only_while_pending(app, world, as_user):
    t = make_ticket(world)
    m = as_user("manager")
    m.post(f"/tickets/{t.id}/decision", data={"decision": "APPROVED"})
    assert db.session.get(Ticket, t.id).status == Status.ASSIGNED and ManagementApproval.query.count() == 0


def test_report_requires_approval(app, world, as_user):
    t = submitted_ticket(world, as_user)
    for who in ("admin", "support", "manager"):
        as_user(who).post(f"/tickets/{t.id}/report")
    assert FinalReport.query.count() == 0
    assert as_user("manager").post(f"/tickets/{t.id}/close").status_code == 302 and db.session.get(Ticket, t.id).status != Status.CLOSED


def test_pdf_report_content_and_versions(app, world, as_user):
    t = make_ticket(world)
    for p in t.problems:
        pass
    t.problems[0].title = "Reset <b>loop"
    t.problems[0].description = UNICODE_TEXT
    t.description = UNICODE_TEXT
    db.session.commit()
    fill_investigation(t)
    t.problems[0].investigation.observations = UNICODE_TEXT
    db.session.commit()
    e = as_user("engineer")
    upload(e, t.problems[0].id, "run.csv", csv_bytes())
    from app.models import TestFile
    fid = TestFile.query.one().id
    for ctype, x, y in (("line", "time", "voltage"), ("scatter", "time", "current"), ("bar", "label", "current")):
        e.post(f"/problems/{t.problems[0].id}/graphs", data=dict(test_file_id=fid, title=f"{ctype} graph", chart_type=ctype, x_column=x, y_column=y, explanation="Shows trend"))
    e.post(f"/problems/{t.problems[0].id}/questions", data=dict(question="Why reset?", justification="Rail sag", conclusion="Inrush"))
    assert e.post(f"/tickets/{t.id}/submit").status_code == 302
    approve(as_user, t, "Approved with thanks")
    assert as_user("engineer").post(f"/tickets/{t.id}/report").status_code == 403
    assert as_user("support").post(f"/tickets/{t.id}/report").status_code == 302
    as_user("admin").post(f"/tickets/{t.id}/report")
    reports = FinalReport.query.order_by(FinalReport.version).all()
    assert [r.version for r in reports] == [1, 2] and reports[0].filename == f"{t.ticket_no}_report_v1.pdf"
    resp = as_user("manager").get(f"/reports/{reports[1].id}/download")
    assert resp.status_code == 200 and resp.mimetype == "application/pdf" and resp.data.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(resp.data))
    text = "\n".join(page.extract_text() for page in reader.pages)
    for needle in (t.ticket_no, "Acme Robotics", "SN-1001", "Servo Drive", "Problem 2", "Why reset?", "Rail sag", "Capacitor C12 undersized",
                   "Replace C12", "Approved with thanks", "Final conclusion", "Management review", "Shows trend", "Test data summaries", "voltage"):
        assert needle in text, needle
    assert "Page 1 of" in text and f"Page {len(reader.pages)} of {len(reader.pages)}" in text
    assert "<b>unclosed" in text  # user markup is printed literally, never interpreted
    assert len(reader.pages) >= 3


def test_close_ticket_after_report(app, world, as_user):
    t = submitted_ticket(world, as_user)
    approve(as_user, t)
    assert as_user("support").post(f"/tickets/{t.id}/close").status_code == 302
    assert db.session.get(Ticket, t.id).status == Status.APPROVED  # needs a report first
    as_user("support").post(f"/tickets/{t.id}/report")
    as_user("support").post(f"/tickets/{t.id}/close")
    assert db.session.get(Ticket, t.id).status == Status.CLOSED


def test_customer_access_to_reports(app, world, as_user):
    t = submitted_ticket(world, as_user)
    cust, other = as_user("cust_acme"), as_user("cust_globex")
    internal = "Observed brown-out"
    page = cust.get(f"/tickets/{t.id}").get_data(as_text=True)
    assert internal not in page and "Under investigation" in page and "PENDING" not in page.upper().replace("PENDING_", "")
    approve(as_user, t)
    assert b"Not available yet" in cust.get(f"/tickets/{t.id}").data  # approved but no report issued yet
    as_user("support").post(f"/tickets/{t.id}/report")
    r1 = db.session.get(Ticket, t.id).latest_report
    resp = cust.get(f"/reports/{r1.id}/download")
    assert resp.status_code == 200 and resp.data.startswith(b"%PDF")
    assert other.get(f"/reports/{r1.id}/download").status_code == 404
    assert b"Download final report" in cust.get(f"/tickets/{t.id}").data
    # a regenerated report supersedes the old version for customers
    as_user("support").post(f"/tickets/{t.id}/report")
    assert cust.get(f"/reports/{r1.id}/download").status_code == 404
    assert cust.get(f"/reports/{db.session.get(Ticket, t.id).latest_report.id}/download").status_code == 200
    assert cust.get(f"/problems/{t.problems[0].id}").status_code == 403


def test_customer_cannot_download_before_approval_even_with_report_row(app, world, as_user):
    t = submitted_ticket(world, as_user)
    from app.services import report_service
    t.status = Status.APPROVED  # force the report into existence
    db.session.add(ManagementApproval(ticket=t, decision="APPROVED", comments="", decided_by_id=world["manager"].id))
    db.session.commit()
    report = report_service.generate_report(t, world["admin"])
    db.session.commit()
    t.status = Status.REWORK  # then pull it back
    db.session.commit()
    assert as_user("cust_acme").get(f"/reports/{report.id}/download").status_code == 404


def test_customer_status_labels_hide_internal_states(app, world, as_user):
    t = make_ticket(world, status=Status.REWORK)
    page = as_user("cust_acme").get("/tickets").get_data(as_text=True)
    assert "Under investigation" in page and "Rework" not in page

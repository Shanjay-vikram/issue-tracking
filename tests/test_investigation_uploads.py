import io

from app.extensions import db
from app.models import GraphConfiguration, InvestigationUpdate, Problem, ProposedSolution, Status, TechnicalQuestion, TestFile, TestResult, Ticket
from conftest import PNG_BYTES, csv_bytes, fill_investigation, make_ticket, upload, xlsx_bytes

FULL = dict(stage=2, problem_description="Board resets", observations="Rail dips to 2.9V", suspected_root_cause="Inrush",
            confirmed_root_cause="", corrective_actions="", preventive_actions="", technical_comments="", conclusion="", note="first pass")


def test_engineer_updates_investigation_with_history_and_status(app, world, as_user):
    t = make_ticket(world)
    p = t.problems[0]
    c = as_user("engineer")
    assert c.post(f"/problems/{p.id}/investigation", data=FULL).status_code == 302
    db.session.expire_all()
    p = db.session.get(Problem, p.id)
    assert p.investigation.stage == 2 and p.investigation.observations == "Rail dips to 2.9V"
    assert db.session.get(Ticket, t.id).status == Status.IN_PROGRESS
    upd = InvestigationUpdate.query.one()
    assert upd.user_id == world["engineer"].id and upd.note == "first pass" and "observations" in upd.changed_fields and upd.created_at
    c.post(f"/problems/{p.id}/investigation", data={**FULL, "stage": 3})
    assert db.session.get(Ticket, t.id).status == Status.INVESTIGATION
    # the second problem is untouched
    assert db.session.get(Problem, t.problems[1].id).investigation.stage == 1
    assert b"first pass" in c.get(f"/problems/{p.id}").data


def test_stage_seven_cannot_be_set_manually(app, world, as_user):
    t = make_ticket(world)
    r = as_user("engineer").post(f"/problems/{t.problems[0].id}/investigation", data={**FULL, "stage": 7})
    assert r.status_code == 200
    assert db.session.get(Problem, t.problems[0].id).investigation.stage == 1


def test_questions_justifications_and_solutions(app, world, as_user):
    t = make_ticket(world)
    p = t.problems[0]
    c = as_user("engineer")
    r = c.post(f"/problems/{p.id}/questions", data=dict(question="Is C12 undersized?", justification="Ripple is 400mV",
                                                         supporting_evidence="Scope capture", test_result="Fail at 2A",
                                                         conclusion="Yes", proposed_action="Upsize"))
    assert r.status_code == 302
    q = TechnicalQuestion.query.one()
    assert q.justifications[0].text == "Ripple is 400mV" and q.proposed_action == "Upsize"
    c.post(f"/questions/{q.id}/justifications", data={"text": "Datasheet limit exceeded"})
    c.post(f"/problems/{p.id}/questions", data=dict(question="", justification="x"))  # invalid
    c.post(f"/problems/{p.id}/solutions", data={"description": "Use 470uF"})
    assert len(db.session.get(TechnicalQuestion, q.id).justifications) == 2 and TechnicalQuestion.query.count() == 1
    sol = ProposedSolution.query.one()
    page = c.get(f"/problems/{p.id}").get_data(as_text=True)
    assert "Is C12 undersized?" in page and "Datasheet limit exceeded" in page and "Use 470uF" in page
    c.post(f"/solutions/{sol.id}/delete")
    c.post(f"/questions/{q.id}/delete")
    assert ProposedSolution.query.count() == 0 and TechnicalQuestion.query.count() == 0


def test_csv_and_xlsx_upload_creates_summary(app, world, as_user):
    t = make_ticket(world)
    p = t.problems[0]
    c = as_user("engineer")
    assert upload(c, p.id, "run1.csv", csv_bytes()).status_code == 302
    assert upload(c, p.id, "thermal.xlsx", xlsx_bytes()).status_code == 302
    files = TestFile.query.order_by(TestFile.id).all()
    assert [f.filename for f in files] == ["run1.csv", "thermal.xlsx"] and files[0].size == len(csv_bytes())
    r = TestResult.query.order_by(TestResult.id).first()
    assert r.stats["rows"] == 30 and {col["name"] for col in r.stats["columns"]} == {"time", "voltage", "current", "label"}
    volt = next(col for col in r.stats["columns"] if col["name"] == "voltage")
    assert volt["type"] == "numeric" and volt["min"] == 3.3
    assert next(col for col in r.stats["columns"] if col["name"] == "label")["type"] == "text"
    c.post(f"/results/{r.id}", data={"title": "Run 1 voltage", "summary": "Stable within 3%"})
    assert db.session.get(TestResult, r.id).summary == "Stable within 3%"
    page = c.get(f"/problems/{p.id}").get_data(as_text=True)
    assert "run1.csv" in page and "Stable within 3%" in page


def test_upload_validation(app, world, as_user):
    t = make_ticket(world)
    p = t.problems[0]
    c = as_user("engineer")
    cases = [
        ("malware.exe", b"MZ\x90\x00", "data"), ("script.php", b"<?php ?>", "document"), ("data.csv", b"", "data"),
        ("fake.png", b"this is not a png", "image"), ("fake.pdf", b"<html>", "document"), ("book.xlsx", b"not a zip", "data"),
        ("macro.xlsx", b"PK\x03\x04junk", "data"), ("wrongkind.png", PNG_BYTES, "data"), ("bin.csv", b"a,b\n\x00\x01\x02\n", "data"),
    ]
    for name, content, kind in cases:
        c.post(f"/problems/{p.id}/files", data={"kind": kind, "file": (io.BytesIO(content), name)}, content_type="multipart/form-data")
    assert TestFile.query.count() == 0
    # no file selected
    c.post(f"/problems/{p.id}/files", data={"kind": "data"}, content_type="multipart/form-data")
    assert TestFile.query.count() == 0


def test_upload_size_limit(app, world, as_user):
    app.config["MAX_UPLOAD_BYTES"] = 1000
    t = make_ticket(world)
    c = as_user("engineer")
    r = upload(c, t.problems[0].id, "big.csv", b"a,b\n" + b"1,2\n" * 1000)
    assert TestFile.query.count() == 0
    assert b"larger than" in c.get(f"/problems/{t.problems[0].id}").data or r.status_code in (302, 413)


def test_path_traversal_filename_is_neutralised(app, world, as_user):
    t = make_ticket(world)
    c = as_user("engineer")
    upload(c, t.problems[0].id, "../../../etc/evil.csv", csv_bytes())
    f = TestFile.query.one()
    assert f.filename.endswith("evil.csv") and "/" not in f.filename and ".." not in f.filename


def test_image_and_document_upload_download_headers(app, world, as_user):
    t = make_ticket(world)
    p = t.problems[0]
    c = as_user("engineer")
    upload(c, p.id, "board.png", PNG_BYTES, kind="image", description="PCB top")
    upload(c, p.id, "notes.txt", b"hello notes", kind="document")
    img, doc = TestFile.query.order_by(TestFile.id).all()
    r = c.get(f"/files/{doc.id}/download")
    assert r.status_code == 200 and r.data == b"hello notes" and "attachment" in r.headers["Content-Disposition"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    r = c.get(f"/files/{img.id}/download")
    assert r.mimetype == "image/png" and "attachment" not in r.headers.get("Content-Disposition", "")
    assert as_user("manager").get(f"/files/{doc.id}/download").status_code == 200
    assert as_user("cust_acme").get(f"/files/{doc.id}/download").status_code == 404
    assert as_user("engineer2").get(f"/files/{doc.id}/download").status_code == 404
    c.post(f"/files/{doc.id}/delete")
    assert db.session.get(TestFile, doc.id) is None


def test_preview_and_chart_data_endpoints(app, world, as_user):
    t = make_ticket(world)
    p = t.problems[0]
    c = as_user("engineer")
    upload(c, p.id, "run.csv", csv_bytes())
    fid = TestFile.query.one().id
    prev = c.get(f"/files/{fid}/preview").get_json()
    assert prev["columns"] == ["time", "voltage", "current", "label"] and prev["total_rows"] == 30 and len(prev["rows"]) == 15
    assert "label" not in prev["numeric"] and "voltage" in prev["numeric"]
    line = c.get(f"/files/{fid}/chart-data?x=time&y=voltage&type=line").get_json()
    assert line["type"] == "line" and len(line["values"]) == 30 and line["labels"][0] == "0"
    bar = c.get(f"/files/{fid}/chart-data?x=label&y=current&type=bar").get_json()
    assert bar["labels"][1] == "run1"
    sc = c.get(f"/files/{fid}/chart-data?x=time&y=voltage&type=scatter").get_json()
    assert sc["points"][2] == {"x": 2.0, "y": 3.32}
    assert c.get(f"/files/{fid}/chart-data?x=label&y=voltage&type=scatter").status_code == 400  # X not numeric
    assert c.get(f"/files/{fid}/chart-data?x=time&y=label&type=line").status_code == 400  # Y not numeric
    assert c.get(f"/files/{fid}/chart-data?x=nope&y=voltage&type=line").status_code == 400
    assert c.get(f"/files/{fid}/chart-data?x=time&y=voltage&type=pie").status_code == 400
    assert as_user("cust_acme").get(f"/files/{fid}/preview").status_code == 404


def test_graph_creation_belongs_to_problem(app, world, as_user):
    t = make_ticket(world)
    p1, p2 = t.problems
    c = as_user("engineer")
    upload(c, p1.id, "run.csv", csv_bytes())
    fid = TestFile.query.one().id
    good = dict(test_file_id=fid, title="Voltage vs time", chart_type="line", x_column="time", y_column="voltage", explanation="Ripple grows")
    assert c.post(f"/problems/{p1.id}/graphs", data=good).status_code == 302
    g = GraphConfiguration.query.one()
    assert g.problem_id == p1.id and g.explanation == "Ripple grows"
    assert c.get(f"/graphs/{g.id}/data").get_json()["type"] == "line"
    # file from another problem cannot be graphed under this problem, bad columns are rejected, explanation is required
    c.post(f"/problems/{p2.id}/graphs", data=good)
    c.post(f"/problems/{p1.id}/graphs", data={**good, "y_column": "label"})
    c.post(f"/problems/{p1.id}/graphs", data={**good, "explanation": ""})
    assert GraphConfiguration.query.count() == 1
    assert b"Voltage vs time" in c.get(f"/problems/{p1.id}").data
    c.post(f"/graphs/{g.id}/delete")
    assert GraphConfiguration.query.count() == 0


def test_removing_a_file_removes_its_graphs(app, world, as_user):
    t = make_ticket(world)
    p = t.problems[0]
    c = as_user("engineer")
    upload(c, p.id, "run.csv", csv_bytes())
    f = TestFile.query.one()
    c.post(f"/problems/{p.id}/graphs", data=dict(test_file_id=f.id, title="g", chart_type="bar", x_column="label", y_column="current", explanation="e"))
    c.post(f"/files/{f.id}/delete")
    assert GraphConfiguration.query.count() == 0 and TestResult.query.count() == 0


def test_evidence_is_read_only_when_locked(app, world, as_user):
    t = make_ticket(world)
    p = t.problems[0]
    c = as_user("engineer")
    fill_investigation(t)
    t.status = Status.PENDING_MANAGEMENT
    db.session.commit()
    upload(c, p.id, "run.csv", csv_bytes())
    c.post(f"/problems/{p.id}/investigation", data={**FULL, "observations": "tampered"})
    c.post(f"/problems/{p.id}/solutions", data={"description": "late"})
    db.session.expire_all()
    assert TestFile.query.count() == 0 and "tampered" not in db.session.get(Problem, p.id).investigation.observations
    assert ProposedSolution.query.count() == 2  # only the two solutions from fill_investigation; the late one was blocked

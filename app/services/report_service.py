"""Consolidated PDF report built with ReportLab. All user text is escaped before it enters Paragraphs."""
import io
import os
from datetime import datetime
from xml.sax.saxutils import escape

from flask import current_app
from reportlab.graphics import renderPDF  # noqa: F401  (ensures graphics backend is importable)
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate, Paragraph,
                                Spacer, Table, TableStyle)

from ..extensions import db
from ..models import FinalReport, STAGES, Status, utcnow
from . import WorkflowError
from .data_service import chart_payload, load_dataframe

NAVY = colors.HexColor("#14284b")
STEEL = colors.HexColor("#2f6fb0")
PALE = colors.HexColor("#eef2f8")
RULE = colors.HexColor("#c9d3e3")
_FONTS = {}


def _register_fonts():
    if _FONTS:
        return _FONTS
    folder = os.path.join(current_app.root_path, "static", "fonts")
    try:
        pdfmetrics.registerFont(TTFont("HC", os.path.join(folder, "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("HC-Bold", os.path.join(folder, "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFont(TTFont("HC-Italic", os.path.join(folder, "DejaVuSans-Oblique.ttf")))
        pdfmetrics.registerFontFamily("HC", normal="HC", bold="HC-Bold", italic="HC-Italic", boldItalic="HC-Bold")
        _FONTS.update(regular="HC", bold="HC-Bold", italic="HC-Italic")
    except Exception:  # fall back to built-in Latin-1 fonts
        _FONTS.update(regular="Helvetica", bold="Helvetica-Bold", italic="Helvetica-Oblique")
    return _FONTS


def _styles():
    f = _register_fonts()
    base = getSampleStyleSheet()
    s = {
        "body": ParagraphStyle("body", parent=base["Normal"], fontName=f["regular"], fontSize=9, leading=13,
                               alignment=TA_LEFT, textColor=colors.HexColor("#1b2433")),
        "small": ParagraphStyle("small", parent=base["Normal"], fontName=f["regular"], fontSize=8, leading=11,
                                textColor=colors.HexColor("#4a566b")),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontName=f["regular"], fontSize=8.5, leading=11.5),
        "cellb": ParagraphStyle("cellb", parent=base["Normal"], fontName=f["bold"], fontSize=8.5, leading=11.5,
                                textColor=NAVY),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontName=f["bold"], fontSize=14, leading=18,
                             textColor=NAVY, spaceBefore=14, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName=f["bold"], fontSize=11, leading=15,
                             textColor=STEEL, spaceBefore=10, spaceAfter=4),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], fontName=f["bold"], fontSize=9.5, leading=13,
                             textColor=NAVY, spaceBefore=6, spaceAfter=2),
        "title": ParagraphStyle("title", parent=base["Title"], fontName=f["bold"], fontSize=24, leading=29,
                                textColor=NAVY, alignment=TA_LEFT, spaceAfter=4),
        "subtitle": ParagraphStyle("subtitle", parent=base["Normal"], fontName=f["regular"], fontSize=11,
                                   leading=15, textColor=colors.HexColor("#4a566b")),
    }
    for key in ("h1", "h2", "h3"):
        s[key].keepWithNext = 1  # never leave a heading stranded at the bottom of a page
    return s


def _esc(text):
    return escape(str(text if text is not None else "")).replace("\n", "<br/>")


def _p(text, style):
    if isinstance(style, dict):  # callers may pass the whole style sheet
        style = style["body"]
    return Paragraph(_esc(text) if text and str(text).strip() else "<i>Not recorded.</i>", style)


def _kv_table(rows, st, widths=(45 * mm, 125 * mm)):
    data = [[Paragraph(_esc(k), st["cellb"]), Paragraph(_esc(v) or "-", st["cell"])] for k, v in rows]
    t = Table(data, colWidths=widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), PALE), ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return t


def _grid_table(header, rows, st, widths):
    f = _register_fonts()
    data = [[Paragraph(_esc(h), ParagraphStyle("th", parent=st["cellb"], textColor=colors.white)) for h in header]]
    for r in rows:
        data.append([Paragraph(_esc(c), st["cell"]) for c in r])
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


class _NumberedCanvas(canvas.Canvas):
    """Two-pass canvas so each footer can print 'Page x of y'."""
    footer_left = ""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            f = _register_fonts()
            self.setFont(f["regular"], 7.5)
            self.setFillColor(colors.HexColor("#4a566b"))
            self.setStrokeColor(RULE)
            self.line(18 * mm, 15 * mm, A4[0] - 18 * mm, 15 * mm)
            self.drawString(18 * mm, 10.5 * mm, self.footer_left)
            self.drawRightString(A4[0] - 18 * mm, 10.5 * mm, f"Page {self._pageNumber} of {total}")
            super().showPage()
        super().save()


def _chart_drawing(cfg, df, width=165 * mm, height=62 * mm):
    f = _register_fonts()
    payload = chart_payload(df, cfg.x_column, cfg.y_column, cfg.chart_type, max_points=200)
    d = Drawing(width, height)
    d.add(String(0, height - 8, cfg.title[:90], fontName=f["bold"], fontSize=9, fillColor=NAVY))
    left, bottom, w, h = 38, 30, width - 55, height - 50
    if payload["type"] == "bar":
        chart = VerticalBarChart()
        chart.data = [payload["values"]]
        chart.categoryAxis.categoryNames = [l[:12] for l in payload["labels"]]
        chart.categoryAxis.labels.angle = 40
        chart.categoryAxis.labels.boxAnchor = "ne"
        chart.categoryAxis.labels.dy = -2
        chart.bars[0].fillColor = STEEL
        chart.valueAxis.labels.fontName = f["regular"]
        chart.categoryAxis.labels.fontName = f["regular"]
        chart.categoryAxis.labels.fontSize = 6
    else:
        chart = LinePlot()
        if payload["type"] == "scatter":
            pts = [(p["x"], p["y"]) for p in payload["points"]]
        else:
            pts = [(i + 1, v) for i, v in enumerate(payload["values"])]
        chart.data = [pts]
        line = chart.lines[0]
        line.strokeColor = STEEL
        if payload["type"] == "scatter":
            line.strokeWidth = 0
            line.symbol = makeMarker("Circle")
            line.symbol.size = 3
            line.symbol.fillColor = STEEL
            line.symbol.strokeColor = STEEL
        else:
            line.strokeWidth = 1.2
        chart.xValueAxis.labels.fontName = f["regular"]
        chart.yValueAxis.labels.fontName = f["regular"]
        chart.xValueAxis.labels.fontSize = 7
        chart.yValueAxis.labels.fontSize = 7
    chart.x, chart.y, chart.width, chart.height = left, bottom, w, h
    if payload["type"] == "bar":
        chart.valueAxis.labels.fontSize = 7
    d.add(chart)
    axis_note = f"X: {cfg.x_column}   Y: {cfg.y_column}"
    if payload["type"] == "line":
        axis_note += "   (X plotted as sample order)"
    d.add(String(left, 4, axis_note[:110], fontName=f["regular"], fontSize=7, fillColor=colors.HexColor("#4a566b")))
    return d, payload["truncated"]


def build_pdf(ticket) -> bytes:
    st = _styles()
    f = _register_fonts()
    company = current_app.config["COMPANY_NAME"]
    now = utcnow()
    footer = f"{ticket.ticket_no}  |  {ticket.customer.company_name}  |  Generated {now:%Y-%m-%d %H:%M} UTC"
    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=24 * mm,
                          bottomMargin=22 * mm, title=f"Engineering report {ticket.ticket_no}", author=company)

    def header(c, d):
        c.saveState()
        c.setFillColor(NAVY)
        c.rect(0, A4[1] - 14 * mm, A4[0], 14 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont(f["bold"], 10)
        c.drawString(18 * mm, A4[1] - 9 * mm, company)
        c.setFont(f["regular"], 8)
        c.drawRightString(A4[0] - 18 * mm, A4[1] - 9 * mm, f"Ticket {ticket.ticket_no}")
        c.restoreState()

    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=header)])
    canvas_cls = type("Canvas", (_NumberedCanvas,), {"footer_left": footer})

    cust, W = ticket.customer, doc.width
    story = [Spacer(1, 6 * mm), Paragraph("Engineering Investigation Report", st["title"]),
             Paragraph(_esc(ticket.title), st["subtitle"]), Spacer(1, 4 * mm)]

    story += [Paragraph("1. Ticket information", st["h1"]), _kv_table([
        ("Ticket reference", ticket.ticket_no), ("Title", ticket.title), ("Description", ticket.description),
        ("Priority", ticket.priority.title()), ("Status", ticket.status.replace("_", " ").title()),
        ("Created", f"{ticket.created_at:%Y-%m-%d}"),
        ("Target resolution", f"{ticket.target_date:%Y-%m-%d}" if ticket.target_date else "Not set"),
        ("Assigned engineer", ticket.assigned_engineer.full_name if ticket.assigned_engineer else "Unassigned"),
        ("Report date", f"{now:%Y-%m-%d}")], st)]
    story += [Paragraph("2. Customer information", st["h1"]), _kv_table([
        ("Customer ID", cust.customer_code), ("Company", cust.company_name), ("Contact person", cust.contact_person),
        ("Email", cust.email), ("Phone", cust.phone), ("Address", cust.address), ("Country", cust.country)], st)]
    story += [Paragraph("3. Product details", st["h1"]), _kv_table([
        ("Product", ticket.product_name), ("Model", ticket.product_model), ("Serial number", ticket.serial_number)], st)]
    story += [Paragraph("4. Hardware problems", st["h1"]), _grid_table(
        ["#", "Problem", "Category", "Investigation stage"],
        [[str(i), p.title, p.category, STAGES[p.stage]] for i, p in enumerate(ticket.problems, 1)],
        st, [10 * mm, 80 * mm, 32 * mm, W - 122 * mm])]

    story.append(Paragraph("5. Problem investigations", st["h1"]))
    for i, p in enumerate(ticket.problems, 1):
        inv = p.investigation
        story += [PageBreak() if i > 1 else Spacer(1, 1), Paragraph(f"Problem {i}: {_esc(p.title)}", st["h2"]),
                  _kv_table([("Category", p.category), ("Reported description", p.description),
                             ("Engineering description", inv.problem_description or "Not recorded")], st)]

        story.append(Paragraph("Investigation stages", st["h3"]))
        rows = [[u.created_at.strftime("%Y-%m-%d %H:%M"), u.stage_name, u.user.full_name,
                 (u.changed_fields + (": " if u.changed_fields and u.note else "") + u.note) or "-"]
                for u in inv.updates]
        story.append(_grid_table(["Date (UTC)", "Stage", "By", "Update"], rows or [["-", STAGES[inv.stage], "-", "-"]],
                                 st, [28 * mm, 34 * mm, 32 * mm, W - 94 * mm]))
        story += [Paragraph("Engineering observations", st["h3"]), _p(inv.observations, st)]

        story.append(Paragraph("Technical questions and justifications", st["h3"]))
        if not p.questions:
            story.append(_p("", st))
        for n, q in enumerate(p.questions, 1):
            just = "\n".join(f"- {j.text}" for j in q.justifications) or "Not recorded."
            story.append(KeepTogether([_kv_table([
                (f"Question {n}", q.question), ("Technical justification", just),
                ("Supporting evidence", q.supporting_evidence or "-"), ("Test result", q.test_result or "-"),
                ("Conclusion", q.conclusion or "-"), ("Proposed action", q.proposed_action or "-")], st), Spacer(1, 3 * mm)]))

        story.append(Paragraph("Test data summaries", st["h3"]))
        if not p.test_results:
            story.append(_p("", st))
        for r in p.test_results:
            stats = r.stats
            story.append(Paragraph(f"{_esc(r.title)} ({_esc(r.test_file.filename)}, {stats.get('rows', 0)} rows)", st["cell"]))
            if r.summary:
                story.append(_p(r.summary, st["cell"]))
            cols = [c for c in stats.get("columns", []) if c["type"] == "numeric"][:10]
            if cols:
                fmt = lambda v: "-" if v is None else f"{v:g}"
                story.append(_grid_table(["Column", "Count", "Min", "Max", "Mean", "Std dev"],
                                         [[c["name"], str(c["count"]), fmt(c["min"]), fmt(c["max"]), fmt(c["mean"]), fmt(c["std"])] for c in cols],
                                         st, [W - 125 * mm, 20 * mm, 25 * mm, 25 * mm, 25 * mm, 30 * mm]))
            story.append(Spacer(1, 3 * mm))

        story.append(Paragraph("Graphs", st["h3"]))
        if not p.graphs:
            story.append(_p("", st))
        for g in p.graphs:
            try:
                df = load_dataframe(g.test_file.data, g.test_file.extension)
                drawing, truncated = _chart_drawing(g, df)
                note = "Large data sets are sampled for display." if truncated else ""
                story.append(KeepTogether([drawing, _p(g.explanation, st["small"]),
                                           Paragraph(_esc(note), st["small"]), Spacer(1, 3 * mm)]))
            except WorkflowError:
                story.append(_p(f"Graph '{g.title}' could not be rendered from its source file.", st["small"]))

        story += [Paragraph("Root cause analysis", st["h3"]), _kv_table([
            ("Suspected root cause", inv.suspected_root_cause or "Not recorded"),
            ("Confirmed root cause", inv.confirmed_root_cause or "Not recorded")], st),
            Paragraph("Corrective actions", st["h3"]), _p(inv.corrective_actions, st),
            Paragraph("Preventive actions", st["h3"]), _p(inv.preventive_actions, st),
            Paragraph("Proposed solutions", st["h3"]),
            _p("\n".join(f"{n}. {s.description}" for n, s in enumerate(p.solutions, 1)), st)]
        if inv.technical_comments:
            story += [Paragraph("Technical comments", st["h3"]), _p(inv.technical_comments, st)]

    story += [PageBreak(), Paragraph("6. Management review and approval", st["h1"])]
    approved = ticket.last_approval
    if approved:
        story.append(_kv_table([("Decision", "Approved"), ("Approved by", approved.decided_by.full_name),
                                ("Approval date", f"{approved.created_at:%Y-%m-%d %H:%M} UTC"),
                                ("Management comments", approved.comments or "None")], st))
    story += [Paragraph("Decision history", st["h3"]),
              _grid_table(["Date (UTC)", "Decision", "By", "Comments"],
                          [[a.created_at.strftime("%Y-%m-%d %H:%M"), a.decision.title(), a.decided_by.full_name, a.comments or "-"]
                           for a in ticket.approvals], st, [30 * mm, 24 * mm, 36 * mm, W - 90 * mm])]

    story.append(Paragraph("7. Final conclusion", st["h1"]))
    for i, p in enumerate(ticket.problems, 1):
        story.append(_kv_table([(f"Problem {i}", p.title), ("Conclusion", p.investigation.conclusion or p.investigation.confirmed_root_cause)], st))
        story.append(Spacer(1, 2 * mm))
    if approved:
        story.append(Paragraph(_esc(f"This investigation was reviewed and approved by {approved.decided_by.full_name} "
                                    f"on {approved.created_at:%Y-%m-%d}."), st["body"]))
    doc.build(story, canvasmaker=canvas_cls)
    return buf.getvalue()


def generate_report(ticket, user) -> FinalReport:
    if ticket.status not in (Status.APPROVED, Status.CLOSED) or ticket.last_approval is None:
        raise WorkflowError("A final report can only be generated after Management approval.")
    pdf = build_pdf(ticket)
    version = (ticket.latest_report.version + 1) if ticket.latest_report else 1
    report = FinalReport(ticket=ticket, version=version, filename=f"{ticket.ticket_no}_report_v{version}.pdf",
                         size=len(pdf), pdf_data=pdf, generated_by_id=user.id)
    db.session.add(report)
    return report

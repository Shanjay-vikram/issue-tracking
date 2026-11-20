"""Upload validation, test-data parsing, summaries and chart series. Uploaded files are never executed."""
import io
import json
import math
import re
import zipfile

import pandas as pd
from flask import current_app
from werkzeug.utils import secure_filename

from . import WorkflowError

ALLOWED_EXTENSIONS = {
    "data": {"csv", "xlsx"},
    "image": {"png", "jpg", "jpeg"},
    "document": {"pdf", "txt", "docx"},
}
MIMETYPES = {
    "csv": "text/csv", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "pdf": "application/pdf",
    "txt": "text/plain", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_UNZIPPED = 150 * 1024 * 1024


def _check_zip(data: bytes, required_member: str):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            if required_member not in names:
                raise WorkflowError("The file content does not match its extension.")
            if sum(i.file_size for i in zf.infolist()) > MAX_UNZIPPED:
                raise WorkflowError("The file expands to an unsafe size.")
            if any(n.startswith("/") or ".." in n.split("/") for n in names):
                raise WorkflowError("The file contains unsafe paths.")
            if any(n.lower().endswith("vbaproject.bin") for n in names):
                raise WorkflowError("Macro-enabled files are not accepted.")
    except zipfile.BadZipFile:
        raise WorkflowError("The file is corrupted or not a valid Office document.")


def validate_upload(storage, kind: str):
    """Return (clean_name, extension, bytes, mimetype) or raise WorkflowError."""
    if kind not in ALLOWED_EXTENSIONS:
        raise WorkflowError("Unknown file category.")
    if storage is None or not storage.filename:
        raise WorkflowError("Choose a file to upload.")
    name = secure_filename(storage.filename)  # strips paths such as ../../etc/passwd
    if not name or "." not in name:
        raise WorkflowError("The file needs a name and an extension.")
    ext = name.rsplit(".", 1)[1].lower()
    if ext not in ALLOWED_EXTENSIONS[kind]:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS[kind]))
        raise WorkflowError(f"This file type is not allowed here. Allowed: {allowed}.")
    limit = current_app.config["MAX_UPLOAD_BYTES"]
    data = storage.stream.read(limit + 1)
    if len(data) > limit:
        raise WorkflowError(f"The file is larger than {current_app.config['MAX_UPLOAD_MB']} MB.")
    if not data:
        raise WorkflowError("The file is empty.")
    if ext == "png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise WorkflowError("The file is not a valid PNG image.")
    if ext in ("jpg", "jpeg") and not data.startswith(b"\xff\xd8\xff"):
        raise WorkflowError("The file is not a valid JPEG image.")
    if ext == "pdf" and not data.startswith(b"%PDF-"):
        raise WorkflowError("The file is not a valid PDF.")
    if ext == "xlsx":
        _check_zip(data, "xl/workbook.xml")
    if ext == "docx":
        _check_zip(data, "word/document.xml")
    if ext in ("csv", "txt"):
        if b"\x00" in data[:4096]:
            raise WorkflowError("The file looks binary, not text.")
    return name[:200], ext, data, MIMETYPES[ext]


def load_dataframe(data: bytes, ext: str) -> pd.DataFrame:
    max_rows = current_app.config["MAX_DATA_ROWS"]
    try:
        if ext == "csv":
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = data.decode("cp1252", errors="replace")
            df = pd.read_csv(io.StringIO(text), nrows=max_rows, sep=None, engine="python")
        elif ext == "xlsx":
            df = pd.read_excel(io.BytesIO(data), engine="openpyxl", nrows=max_rows)
        else:
            raise WorkflowError("This file type has no tabular data.")
    except WorkflowError:
        raise
    except Exception:
        raise WorkflowError("The data file could not be read. Check that it is a valid table with a header row.")
    df.columns = [str(c).strip() for c in df.columns]
    if df.empty or len(df.columns) == 0:
        raise WorkflowError("The data file has no rows.")
    if len(df.columns) > current_app.config["MAX_DATA_COLUMNS"]:
        raise WorkflowError("The data file has too many columns.")
    return df


def _num(value):
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, 6)


def summarize(df: pd.DataFrame) -> dict:
    columns = []
    for col in df.columns:
        series = df[col]
        numeric = pd.to_numeric(series, errors="coerce")
        if numeric.notna().sum() >= max(1, int(0.8 * series.notna().sum())) and series.notna().any():
            columns.append({"name": col, "type": "numeric", "count": int(numeric.notna().sum()),
                            "min": _num(numeric.min()), "max": _num(numeric.max()),
                            "mean": _num(numeric.mean()), "std": _num(numeric.std())})
        else:
            columns.append({"name": col, "type": "text", "count": int(series.notna().sum()),
                            "unique": int(series.nunique(dropna=True))})
    return {"rows": int(len(df)), "columns": columns}


def numeric_columns(df: pd.DataFrame):
    return [c["name"] for c in summarize(df)["columns"] if c["type"] == "numeric"]


def preview(df: pd.DataFrame, rows=15) -> dict:
    head = df.head(rows).astype(object).where(df.head(rows).notna(), "")
    return {"columns": list(df.columns), "numeric": numeric_columns(df), "rows": head.astype(str).values.tolist(),
            "total_rows": int(len(df))}


def chart_payload(df: pd.DataFrame, x: str, y: str, chart_type: str, max_points=400) -> dict:
    if chart_type not in ("line", "bar", "scatter"):
        raise WorkflowError("Unknown chart type.")
    if x not in df.columns or y not in df.columns:
        raise WorkflowError("Choose columns that exist in the file.")
    ys = pd.to_numeric(df[y], errors="coerce")
    if ys.notna().sum() == 0:
        raise WorkflowError(f"Column '{y}' has no numeric values to plot.")
    truncated = False
    if chart_type == "scatter":
        xs = pd.to_numeric(df[x], errors="coerce")
        if xs.notna().sum() == 0:
            raise WorkflowError(f"Scatter charts need a numeric X column; '{x}' is not numeric.")
        mask = xs.notna() & ys.notna()
        pts = list(zip(xs[mask].tolist(), ys[mask].tolist()))
        step = max(1, math.ceil(len(pts) / max_points))
        truncated = step > 1
        pts = pts[::step]
        return {"type": "scatter", "x": x, "y": y, "points": [{"x": _num(a), "y": _num(b)} for a, b in pts],
                "truncated": truncated}
    mask = ys.notna()
    labels = df[x].astype(str)[mask].tolist()
    values = ys[mask].tolist()
    limit = 40 if chart_type == "bar" else max_points
    if len(values) > limit:
        if chart_type == "bar":
            labels, values = labels[:limit], values[:limit]
        else:
            step = math.ceil(len(values) / limit)
            labels, values = labels[::step], values[::step]
        truncated = True
    return {"type": chart_type, "x": x, "y": y, "labels": [l[:40] for l in labels],
            "values": [_num(v) for v in values], "truncated": truncated}


def stats_to_json(df) -> str:
    return json.dumps(summarize(df))

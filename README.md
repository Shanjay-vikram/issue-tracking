# HardwareCare

Hardware issue tracking and engineering support management for a hardware company. Support logs customer problems under parent tickets, engineers investigate each problem (test data, graphs, technical questions, root cause), Management approves or returns the work, and a consolidated PDF report is issued to the customer.

Stack: Flask, SQLAlchemy, Flask-Login, Flask-WTF (CSRF), Flask-Migrate, Bootstrap 5, Chart.js, ReportLab, OpenPyXL and Pandas. PostgreSQL in production, SQLite locally. Bootstrap, Chart.js and the fonts are bundled in `app/static`, so the app makes no third-party requests.

## Roles

| Role | Can do |
|---|---|
| ADMIN | Manage users and customers, see all tickets, assign engineers, generate reports, close tickets, read the audit log |
| SUPPORT | Register customers, create tickets, add problems, assign engineers, generate reports, close tickets |
| ENGINEER | See assigned tickets, run investigations, upload evidence, build graphs, add questions and solutions, submit to Management |
| MANAGEMENT | Review submitted investigations, approve or return with comments, generate reports, close tickets |
| CUSTOMER | See only their own tickets (simplified status), download the final report once issued |

All permissions are enforced in the backend (`app/permissions.py`). Objects a user may not see return 404 so IDs cannot be probed.

## Workflow

`OPEN -> ASSIGNED -> IN_PROGRESS -> INVESTIGATION -> PENDING_MANAGEMENT -> APPROVED -> CLOSED`, with `REWORK` when Management returns the work.

1. Support creates a ticket with its first problem, then adds more problems and assigns an engineer.
2. The engineer moves each problem through stages 1 to 6. Status advances automatically.
3. Submit is allowed when every problem is at the Validation stage with observations, a confirmed root cause and at least one proposed solution. Investigations are then locked (stage 7).
4. Management approves (locks permanently) or returns with required comments (unlocks, engineer resubmits). Every decision is stored in `management_approvals`.
5. After approval, Admin, Support or Management clicks **Generate final report**. Regenerating creates a new version. Customers can download only the latest report, and only while the ticket is APPROVED or CLOSED.
6. **Close ticket** requires an approved ticket with a report.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                    # then set SECRET_KEY
export FLASK_APP=run.py
flask db upgrade                                        # creates instance/hardwarecare.db
python create_admin.py                                  # prompts for the Admin account and password
flask run                                               # http://127.0.0.1:5000
```

The database starts empty: no customers, users or tickets. Optional demo data for development only: `python seed_dev.py` (prints random passwords once; refuses to run when `APP_ENV=production`).

Run the tests: `python -m pytest`

## Deploy on Render

Option A, Blueprint: push the repo to GitHub, then in Render choose **New > Blueprint** and select the repo. `render.yaml` creates the web service and a PostgreSQL database, wires `DATABASE_URL`, and generates `SECRET_KEY`.

Option B, manual:
1. **New > PostgreSQL**, copy the *Internal Database URL*.
2. **New > Web Service** from the GitHub repo. Runtime: Python 3.
   - Build command: `pip install -r requirements.txt`
   - Start command: `flask db upgrade && gunicorn --bind 0.0.0.0:$PORT run:app`
   - Environment: `APP_ENV=production`, `FLASK_APP=run.py`, `SECRET_KEY` (a long random value, for example `python -c "import secrets; print(secrets.token_hex(32))"`), `DATABASE_URL` (the URL from step 1), optional `COMPANY_NAME`, `MAX_UPLOAD_MB`. Set `PYTHON_VERSION=3.12.3`.
   - Health check path: `/healthz`

`flask db upgrade` runs on every start and applies pending migrations. With several instances, run migrations from one place instead.

### Uploaded files and reports

Render's local disk is ephemeral, so HardwareCare does not use it for customer data. Evidence files and generated PDFs are stored inside PostgreSQL (`test_files.data`, `final_reports.pdf_data`), so they survive deploys and restarts and are covered by your database backups. Uploads are limited by `MAX_UPLOAD_MB` (default 10). If you later store very large files, move those two columns to object storage such as S3.

### Create the first Admin in production

`create_admin.py` has no default password and does nothing if an Admin already exists. Pick one:

- **Render Shell** (paid plans): open the service Shell and run `python create_admin.py`, then type the password at the hidden prompt.
- **From your computer**: temporarily allow your IP on the database (External Database URL), then
  ```bash
  export APP_ENV=production SECRET_KEY=any-value-for-this-script DATABASE_URL='<external url>'
  python create_admin.py
  ```
  Remove the IP allowance afterwards.
- **One-off job**: set `CREATE_ADMIN_PASSWORD` temporarily and run `python create_admin.py --from-env --username ... --full-name ... --email ...`, then delete the variable.

Then sign in and create Support, Engineer, Management and Customer users from **Users**. A customer login must be linked to a registered customer.

## Security notes

- Passwords hashed with Werkzeug (scrypt). Accounts lock for 15 minutes after 5 failed sign-ins. Unknown usernames get the same message and similar timing.
- CSRF tokens on every state-changing request, secure/HttpOnly/SameSite cookies, HSTS in production, strict Content-Security-Policy (scripts only from the app itself), `no-store` caching on dynamic pages.
- Uploads: extension allow-list per category, content sniffing (PNG, JPEG, PDF, OOXML structure, binary check), size limit, zip-bomb and macro rejection, filenames sanitised, files stored in the database and never executed or written to a web path. Downloads are sent with `nosniff`.
- All user text is escaped in HTML and in the PDF. SQL goes through the ORM.
- Audit log (Admin > Audit log) records sign-ins, lockouts, assignments, uploads, approvals and report downloads.

## Known limits

- No email: password resets are done by an Admin from the Users page.
- Data files: `.csv` and `.xlsx` only (no `.xls`, no macros). Tables are capped at 100,000 rows and 200 columns, and charts sample large series.
- Tested against SQLite and a production-mode gunicorn boot. I could not run PostgreSQL or Render in the build environment, so do a first-deploy check: sign in, create a ticket, upload a CSV and generate a report.

## Layout

```
app/            __init__.py (factory), models.py, auth.py, routes.py, forms.py, permissions.py, audit.py
app/services/   ticket_service, investigation_service, approval_service, report_service, data_service
app/templates/  Jinja templates          app/static/  css, js, vendor (Bootstrap, Chart.js), fonts
migrations/     Alembic migrations       tests/       pytest suite (69 tests)
create_admin.py  run.py  seed_dev.py  render.yaml  Procfile  requirements*.txt  .env.example
```

"""Create the first Administrator. Run once after `flask db upgrade`.

Usage:
    python create_admin.py                # prompts for every value, password hidden
    python create_admin.py --from-env     # reads CREATE_ADMIN_PASSWORD (for a one-off Render shell/job)

There is no default password. If an Admin already exists the script exits without changes.
"""
import argparse
import getpass
import os
import re
import sys

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import inspect

from app import create_app
from app.extensions import db
from app.models import Role, User


def password_problem(pw):
    if len(pw) < 12:
        return "Use at least 12 characters for an Admin password."
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)):
        return "Include both letters and numbers."
    return None


def ask(prompt, default=None):
    value = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return value or default


def main():
    parser = argparse.ArgumentParser(description="Create the first HardwareCare Admin user.")
    parser.add_argument("--from-env", action="store_true", help="read the password from CREATE_ADMIN_PASSWORD")
    parser.add_argument("--username")
    parser.add_argument("--full-name")
    parser.add_argument("--email")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        if not inspect(db.engine).has_table("users"):
            sys.exit("The database has no tables yet. Run `flask db upgrade` first.")
        existing = User.query.filter_by(role=Role.ADMIN).first()
        if existing:
            print(f"An Admin already exists ({existing.username}). Nothing was changed.")
            return

        username = args.username or ask("Admin username", "admin")
        full_name = args.full_name or ask("Full name")
        email = args.email or ask("Email")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username or ""):
            sys.exit("Username must be 3-64 characters: letters, numbers, dot, dash, underscore.")
        if not full_name:
            sys.exit("Full name is required.")
        try:
            email = validate_email(email or "", check_deliverability=False).normalized
        except EmailNotValidError as e:
            sys.exit(f"Invalid email: {e}")

        if args.from_env:
            password = os.environ.get("CREATE_ADMIN_PASSWORD", "")
            if not password:
                sys.exit("CREATE_ADMIN_PASSWORD is not set.")
        else:
            password = getpass.getpass("Admin password: ")
            if password != getpass.getpass("Repeat password: "):
                sys.exit("Passwords do not match.")
        problem = password_problem(password)
        if problem:
            sys.exit(problem)

        if User.query.filter(db.func.lower(User.username) == username.lower()).first() or \
           User.query.filter(db.func.lower(User.email) == email.lower()).first():
            sys.exit("A user with that username or email already exists.")

        admin = User(username=username, full_name=full_name, email=email, role=Role.ADMIN, active=True)
        admin.set_password(password)  # hashed with Werkzeug; plaintext is never stored
        db.session.add(admin)
        db.session.commit()
        print(f"Admin '{username}' created. Sign in and create the other users from the Users page.")
        if args.from_env:
            print("Remove CREATE_ADMIN_PASSWORD from the environment now.")


if __name__ == "__main__":
    main()

"""OPTIONAL development seed. Never run against production: it refuses when APP_ENV=production.

Creates demo users and one sample customer so you can click through the app locally.
Passwords are random and printed once, nothing is hard-coded.
"""
import secrets
import sys

from app import create_app
from app.extensions import db
from app.models import Customer, Role, User


def main():
    app = create_app()
    if app.config["APP_ENV"] == "production":
        sys.exit("Refusing to seed data when APP_ENV=production.")
    with app.app_context():
        if User.query.first():
            sys.exit("The database already has users; seed only an empty development database.")
        customer = Customer(customer_code=Customer.new_placeholder_code(), company_name="Demo Robotics Ltd",
                            contact_person="Alex Demo", email="alex@demo-robotics.example", phone="+1 555 0100",
                            address="1 Example Street", country="Demo Land", active=True)
        db.session.add(customer)
        db.session.flush()
        customer.finalize_code()
        people = [("admin", "Dev Admin", Role.ADMIN, None), ("support", "Dev Support", Role.SUPPORT, None),
                  ("engineer", "Dev Engineer", Role.ENGINEER, None), ("manager", "Dev Manager", Role.MANAGEMENT, None),
                  ("customer", "Alex Demo", Role.CUSTOMER, customer.id)]
        print("Development users (shown once):")
        for username, name, role, cid in people:
            pw = secrets.token_urlsafe(12) + "a1"
            u = User(username=username, full_name=name, email=f"{username}@dev.example", role=role, customer_id=cid, active=True)
            u.set_password(pw)
            db.session.add(u)
            print(f"  {role:<11} {username:<10} {pw}")
        db.session.commit()


if __name__ == "__main__":
    main()

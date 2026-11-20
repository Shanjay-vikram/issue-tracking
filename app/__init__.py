import os

from dotenv import load_dotenv
from flask import Flask, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .extensions import csrf, db, login_manager, migrate

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def create_app(config_name=None):
    load_dotenv()
    from .config import CONFIGS
    name = config_name or os.environ.get("APP_ENV", "development").lower()
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(CONFIGS.get(name, CONFIGS["development"]))

    if not app.config.get("SECRET_KEY"):
        if name == "production":
            raise RuntimeError("SECRET_KEY must be set in the environment for production.")
        import secrets
        app.config["SECRET_KEY"] = secrets.token_hex(32)  # development only; sessions reset on restart
    os.makedirs(app.instance_path, exist_ok=True)
    if app.config.get("TRUSTED_PROXIES"):
        n = app.config["TRUSTED_PROXIES"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Sign in to continue."
    login_manager.login_message_category = "info"
    login_manager.session_protection = "strong"

    from . import models  # noqa: F401
    from .auth import bp as auth_bp
    from .routes import bp as main_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    from .models import CATEGORIES, PRIORITIES, Role, STAGES, Status
    from .services.ticket_service import customer_status

    # Jinja globals (not a context processor) so imported macros can use them too.
    app.jinja_env.globals.update(Role=Role, Status=Status, STAGES=STAGES, PRIORITIES=PRIORITIES,
                                 CATEGORIES=CATEGORIES, customer_status=customer_status)

    @app.context_processor
    def inject():
        return dict(company_name=app.config["COMPANY_NAME"])

    @app.template_filter("dt")
    def fmt_dt(value, fmt="%Y-%m-%d %H:%M"):
        return value.strftime(fmt) if value else "-"

    @app.template_filter("label")
    def fmt_label(value):
        return str(value).replace("_", " ").title()

    @app.template_filter("filesize")
    def fmt_size(n):
        return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Content-Security-Policy", CSP)
        if request.endpoint != "static":
            resp.headers["Cache-Control"] = "no-store"
        if name == "production":
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp

    def error_page(code, title, message):
        def handler(e):
            if code == 500:
                db.session.rollback()
            return render_template("error.html", code=code, title=title, message=message), code
        return handler

    app.register_error_handler(403, error_page(403, "You don't have access", "Your role does not allow this action."))
    app.register_error_handler(404, error_page(404, "Page not found", "The page does not exist or you cannot see it."))
    app.register_error_handler(413, error_page(413, "File too large", f"Uploads are limited to {app.config['MAX_UPLOAD_MB']} MB."))
    app.register_error_handler(500, error_page(500, "Something went wrong", "The error was logged. Try again or contact an administrator."))

    from flask_wtf.csrf import CSRFError

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        return render_template("error.html", code=400, title="Session expired",
                               message="Your form session expired. Go back, reload the page and try again."), 400

    return app

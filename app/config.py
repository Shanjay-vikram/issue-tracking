import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        # Render and Heroku may hand out the legacy "postgres://" scheme.
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return url
    return "sqlite:///hardwarecare.db"  # stored in ./instance for local development only


class Config:
    APP_ENV = os.environ.get("APP_ENV", "development").lower()
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    COMPANY_NAME = os.environ.get("COMPANY_NAME", "HardwareCare Engineering")
    MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "10"))
    MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
    MAX_CONTENT_LENGTH = MAX_UPLOAD_BYTES + 512 * 1024  # form overhead
    MAX_DATA_ROWS = 100_000
    MAX_DATA_COLUMNS = 200

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # 8 hours

    LOGIN_MAX_ATTEMPTS = 5
    LOGIN_LOCK_MINUTES = 15
    TRUSTED_PROXIES = int(os.environ.get("TRUSTED_PROXIES", "0"))
    WTF_CSRF_TIME_LIMIT = 60 * 60 * 8


class DevelopmentConfig(Config):
    DEBUG = False  # run.py enables the debugger explicitly


class ProductionConfig(Config):
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    TRUSTED_PROXIES = int(os.environ.get("TRUSTED_PROXIES", "1"))  # Render sits behind one proxy


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "testing-only-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": __import__("sqlalchemy.pool", fromlist=["StaticPool"]).StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    WTF_CSRF_ENABLED = False
    LOGIN_MAX_ATTEMPTS = 3


CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}

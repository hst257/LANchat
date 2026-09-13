import os
from functools import lru_cache
from collections.abc import Generator

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from . import settings


@lru_cache(maxsize=1)
def database_url():
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit
    secret_id = os.getenv("DATABASE_SECRET_ID", "").strip()
    if secret_id:
        secret = settings.get_secret_object(secret_id)
        required = ["username", "password"]
        missing = [name for name in required if not secret.get(name)]
        if missing:
            raise RuntimeError(
                "Database secret is missing: " + ", ".join(missing)
            )
        db_host = os.getenv("DB_HOST", "").strip()
        if not db_host:
            raise RuntimeError("DB_HOST is required when DATABASE_SECRET_ID is used")
        return URL.create(
            "postgresql+psycopg",
            username=secret["username"],
            password=secret["password"],
            host=db_host,
            port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME", "lan_chat"),
            query={
                "sslmode": "verify-full",
                "sslrootcert": os.getenv(
                    "RDS_CA_BUNDLE", "/etc/pki/ca-trust/source/anchors/global-bundle.pem"
                ),
            },
        )
    if settings.IS_PRODUCTION:
        raise RuntimeError("DATABASE_SECRET_ID or DATABASE_URL is required in production")
    return "postgresql+psycopg://lan_chat:lan_chat@localhost:5433/lan_chat"


DATABASE_URL = database_url()

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

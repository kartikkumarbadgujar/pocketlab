from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings


class Base(DeclarativeBase):
    pass


_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(settings.database_url, pool_pre_ping=True, **({"connect_args": {"check_same_thread": False}} if _sqlite else {"pool_size": 5, "max_overflow": 10, "pool_recycle": 1800}))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

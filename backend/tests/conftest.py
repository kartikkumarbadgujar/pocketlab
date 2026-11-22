import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET","test-jwt-secret-that-is-longer-than-32-chars")
os.environ.setdefault("RUNNER_TOKEN","test-runner-secret-that-is-longer-than-32-chars")
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.config import settings
from app.main import app


def pytest_configure(config):
    config.addinivalue_line("markers","mocked: orchestration logic exercised with scripted process adapters - NOT evidence that real emulator execution works")
    config.addinivalue_line("markers","real_emulator: requires a real Android SDK, hardware acceleration and POCKETLAB_REAL_EMULATOR=1")


def pytest_report_header(config):
    return ["Tests marked 'mocked' replace adb/emulator with scripted outputs; only 'real_emulator' tests prove real execution."]


@pytest.fixture()
def client(monkeypatch,tmp_path):
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
    app.state.testing_session=TestingSession
    def override_db():
        db=TestingSession()
        try:yield db
        finally:db.close()
    app.dependency_overrides[get_db]=override_db
    monkeypatch.setattr(settings,"runner_token","test-runner-token")
    # The per-process sliding-window rate limiter is module state; reset it so tests do not throttle each other.
    import app.main as main_module
    main_module._requests.clear()
    monkeypatch.setattr(settings,"upload_dir",str(tmp_path/"private_uploads"))
    from app.seed import seed_database
    with TestingSession() as db:seed_database(db)
    with TestClient(app) as test_client:yield test_client
    app.dependency_overrides.clear();Base.metadata.drop_all(engine);engine.dispose()


@pytest.fixture()
def auth_headers(client):
    client.post("/api/v1/auth/register",json={"email":"a@example.com","password":"correct horse battery staple"})
    tokens=client.post("/api/v1/auth/login",json={"email":"a@example.com","password":"correct horse battery staple"}).json()
    return {"Authorization":f"Bearer {tokens['access_token']}"},tokens

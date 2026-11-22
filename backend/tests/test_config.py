import pytest

from app.config import Settings

SECRETS = {"jwt_secret": "j" * 40, "runner_token": "r" * 40}


@pytest.mark.parametrize("url", ["postgres://u:p@host/db", "postgresql://u:p@host/db"])
def test_hosted_postgres_urls_use_psycopg3(url):
    assert Settings(database_url=url, **SECRETS).database_url == "postgresql+psycopg://u:p@host/db"


@pytest.mark.parametrize("url", ["postgresql+psycopg://u:p@host/db", "sqlite:///./pocketlab.db"])
def test_explicit_urls_unchanged(url):
    assert Settings(database_url=url, **SECRETS).database_url == url

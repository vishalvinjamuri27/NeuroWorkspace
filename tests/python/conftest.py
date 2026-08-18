from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from neuro_api.main import create_app
from neuro_workspace.config import Settings


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--real-data-root",
        action="store",
        default=None,
        help="Directory containing checksum-verified public acceptance data",
    )


@pytest.fixture
def real_data_root(request: pytest.FixtureRequest) -> Path:
    configured = request.config.getoption("--real-data-root")
    if not configured:
        pytest.skip("--real-data-root was not provided")
    return Path(str(configured)).resolve()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        object_store_backend="local",
        object_store_root=tmp_path / "objects",
        session_secret="test-session-secret-that-is-long-enough",
        dev_auth_enabled=True,
        worker_eager=True,
        api_public_url="http://testserver",
        cors_origins="http://testserver,http://localhost:3000",
        build_version="test",
        alpha_admin_emails="owner@example.test",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as selected:
        yield selected


def sign_in(client: TestClient, email: str = "owner@example.test") -> None:
    response = client.post(
        "/api/session/dev",
        json={"email": email, "display_name": email.split("@", maxsplit=1)[0].title()},
    )
    assert response.status_code == 200, response.text


@pytest.fixture
def signed_in_client(client: TestClient) -> TestClient:
    sign_in(client)
    return client

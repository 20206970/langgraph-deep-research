from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient

from src import main
from src.auth import CurrentUser, require_current_user
from src.config import get_config
from src.repository import SQLiteRepository


def _app(tmp_path):
    repository = SQLiteRepository(tmp_path / "auth.db")
    return main.create_app(repository=repository, initialize_services=False), repository


def _register(client: TestClient, username: str = "alice") -> dict:
    response = client.post("/auth/register", json={"username": username, "password": "correct-horse-42"})
    assert response.status_code == 201
    return response.json()


def test_register_login_and_authentication_failures(tmp_path):
    app, repository = _app(tmp_path)
    with TestClient(app) as client:
        registered = _register(client)
        stored = repository.get_user(registered["user"]["user_id"])
        assert stored is not None
        assert stored["password_hash"] != "correct-horse-42"
        assert registered["token_type"] == "bearer"

        me = client.get("/auth/me", headers={"Authorization": f"Bearer {registered['access_token']}"})
        assert me.status_code == 200
        assert me.json() == registered["user"]

        duplicate = client.post("/auth/register", json={"username": "ALICE", "password": "another-safe-42"})
        assert duplicate.status_code == 409

        invalid_login = client.post("/auth/login", json={"username": "alice", "password": "wrong-password"})
        assert invalid_login.status_code == 401

        missing = client.get("/history")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"

        forged = client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
        assert forged.status_code == 401

        expired_token = jwt.encode(
            {
                "sub": registered["user"]["user_id"],
                "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
            },
            get_config().auth.jwt_secret,
            algorithm=get_config().auth.jwt_algorithm,
        )
        expired = client.get("/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
        assert expired.status_code == 401
    repository.close()


def test_fake_authentication_is_an_explicit_fastapi_test_override(tmp_path):
    app, repository = _app(tmp_path)
    app.dependency_overrides[require_current_user] = lambda: CurrentUser(user_id="test_user", username="tester")
    with TestClient(app) as client:
        response = client.get("/auth/me")
    assert response.status_code == 200
    assert response.json() == {"user_id": "test_user", "username": "tester"}
    repository.close()

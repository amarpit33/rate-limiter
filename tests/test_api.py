from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@patch("app.main.token_bucket")
def test_check_allowed(mock_bucket):
    mock_bucket.allow.return_value = {"allowed": True, "tokens_remaining": 9, "capacity": 10}
    resp = client.post("/check", json={"client_id": "user1", "algorithm": "token_bucket"})
    assert resp.status_code == 200
    assert resp.json()["allowed"] is True


@patch("app.main.token_bucket")
def test_check_denied(mock_bucket):
    mock_bucket.allow.return_value = {"allowed": False, "tokens_remaining": 0, "capacity": 10}
    resp = client.post("/check", json={"client_id": "user1", "algorithm": "token_bucket"})
    assert resp.status_code == 200
    assert resp.json()["allowed"] is False


def test_check_invalid_algorithm():
    resp = client.post("/check", json={"client_id": "user1", "algorithm": "invalid"})
    assert resp.status_code == 400


@patch("app.main._redis")
def test_health_ok(mock_redis):
    mock_redis.ping.return_value = True
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

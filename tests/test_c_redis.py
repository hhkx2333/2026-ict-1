import os
import pytest
from fastapi.testclient import TestClient
from c_app.api import create_app
from c_app.db import Base, make_engine, sessions
from c_app.security import create_user


def test_redis_login_limiter_and_readiness(tmp_path, monkeypatch):
    url = os.environ.get("C_TEST_REDIS_URL")
    if not url:
        pytest.skip("Requires an explicit test Redis instance; CI supplies it.")
    monkeypatch.setenv("REDIS_URL", url)
    engine = make_engine("sqlite:///" + (tmp_path / "redis-test.sqlite3").as_posix())
    Base.metadata.create_all(engine)
    with sessions(engine).begin() as db:
        create_user(db, "synthetic-redis", "not-a-real-account-password", "admin")
    app = create_app(engine)
    r = app.state.redis
    from c_app.security import digest

    key = "c:login:" + digest("testclient")
    r.delete(key)
    try:
        c = TestClient(app)
        assert (
            c.post(
                "/api/v1/login",
                json={
                    "username": "synthetic-redis",
                    "password": "not-a-real-account-password",
                },
            ).status_code
            == 200
        )
        assert int(r.get(key)) == 1 and 0 < r.ttl(key) <= 900
        r.set(key, 30, ex=900)
        assert (
            c.post(
                "/api/v1/login",
                json={
                    "username": "synthetic-redis",
                    "password": "not-a-real-account-password",
                },
            ).status_code
            == 429
        )
    finally:
        r.delete(key)
        r.close()
        engine.dispose()

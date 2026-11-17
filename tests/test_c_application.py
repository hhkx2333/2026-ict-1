"""Application acceptance tests use synthetic providers and temporary databases only."""

import copy
import csv
import hashlib
import io
import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from c_app.api import create_app
from c_app.cli import import_legacy, seed_synthetic
from c_app.db import (
    Base,
    ROOT,
    Answer,
    Course,
    Membership,
    Question,
    Review,
    Rubric,
    Task,
    User,
    make_engine,
    sessions,
)
from c_app.security import create_user
from c_app.worker import recover, run_once
from backend.grading import FixtureProvider


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("C_ALLOW_LIVE", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    engine = make_engine(
        os.environ.get("C_TEST_DATABASE_URL")
        or "sqlite:///" + (tmp_path / "test.sqlite3").as_posix()
    )
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessions(engine)
    with factory.begin() as db:
        admin = create_user(db, "admin", "synthetic-test-password", "admin")
        teacher = create_user(db, "teacher", "synthetic-test-password", "teacher")
        create_user(db, "outsider", "synthetic-test-password", "teacher")
        student = create_user(db, "student", "synthetic-test-password", "student")
        c = Course(name="Synthetic course")
        db.add(c)
        db.flush()
        for u in (admin, teacher, student):
            db.add(Membership(course_id=c.id, user_id=u.id))
        db.flush()
        qid = seed_synthetic(db, admin, c.id)
        ids = {
            "admin": admin.id,
            "teacher": teacher.id,
            "student": student.id,
            "course": c.id,
            "question": qid,
        }
    app = create_app(engine)
    client = TestClient(app)

    def as_user(name="teacher"):
        client.cookies.clear()
        r = client.post(
            "/api/v1/login",
            json={"username": name, "password": "synthetic-test-password"},
        )
        assert r.status_code == 200, r.text
        client.headers["x-csrf-token"] = r.json()["csrf"]
        return client

    as_user()
    yield client, factory, ids, as_user, app
    engine.dispose()


def batch(
    client, ids, provider="synthetic", mode="structured", key="test-batch-key", **kwargs
):
    answers = client.get(f"/api/v1/questions/{ids['question']}/answers").json()
    rubric = client.get(f"/api/v1/questions/{ids['question']}/rubrics").json()[-1]
    body = dict(
        answer_ids=[a["id"] for a in answers],
        rubric_id=rubric["id"],
        mode=mode,
        provider=provider,
        idempotency_key=key,
        allow_draft=True,
        **kwargs,
    )
    r = client.post(f"/api/v1/courses/{ids['course']}/batches", json=body)
    return r, body


def drain(factory):
    while run_once(factory):
        pass


def task_ids(client, ids):
    return client.get(f"/api/v1/courses/{ids['course']}/batches").json()[0]["task_ids"]


def confirmation(task, zero=False):
    rows = []
    for c in task["request"]["rubric"]["criteria"]:
        i = next(
            (
                i
                for i in task["result"]["items"]
                if i["criterion_id"] == c["criterion_id"]
            ),
            None,
        )
        rows.append(
            {
                "criterion_id": c["criterion_id"],
                "score": 0 if zero else i["suggested_score"],
                "max_score": c["max_score"],
                "teacher_reason": "Synthetic test review",
                "evidence": [] if zero else i["evidence"],
                "rule_id": next(iter(c["rules"])),
            }
        )
    return {"items": rows, "release": False}


def test_login_csrf_and_course_isolation(setup):
    c, _, ids, login, _ = setup
    c.headers.pop("x-csrf-token")
    assert c.post("/api/v1/courses", json={"name": "forbidden"}).status_code == 403
    login("outsider")
    assert c.get(f"/api/v1/questions/{ids['question']}/answers").status_code == 403
    login("student")
    assert c.get(f"/api/v1/questions/{ids['question']}/answers").status_code == 403
    assert (
        c.post(
            "/api/v1/users",
            json={
                "username": "x1",
                "password": "another-long-password",
                "role": "admin",
            },
        ).status_code
        == 403
    )
    assert c.get("/api/answers").status_code == 410
    assert c.get("/export.csv").status_code == 410


def test_import_atomicity_labels_and_zero_length(setup):
    c, _, ids, _, _ = setup
    url = f"/api/v1/questions/{ids['question']}/answers/import"
    rows = [
        {"q_id": "Q01", "answer_id": "new", "answer": ""},
        {"q_id": "Q01", "answer_id": "SYNTHETIC-A001", "answer": "conflict"},
    ]
    assert (
        c.post(url, json={"text": "\n".join(map(json.dumps, rows))}).status_code == 409
    )
    assert not any(
        a["answer_id"] == "new"
        for a in c.get(f"/api/v1/questions/{ids['question']}/answers").json()
    )
    row = {"q_id": "Q01", "answer_id": "new", "answer": "😀引文", "score": 0}
    assert c.post(url, json={"text": json.dumps(row)}).status_code == 400
    del row["score"]
    assert c.post(url, json={"text": json.dumps(row)}).json()["added"] == 1
    assert c.post(url, json={"text": json.dumps(row)}).json()["existing"] == 1


def test_batch_idempotency_and_failure_states(setup):
    c, factory, ids, _, _ = setup
    r, body = batch(c, ids)
    assert r.status_code == 201, r.text
    assert (
        c.post(f"/api/v1/courses/{ids['course']}/batches", json=body).json() == r.json()
    )
    changed = dict(body, answer_ids=body["answer_ids"][:1])
    assert (
        c.post(f"/api/v1/courses/{ids['course']}/batches", json=changed).status_code
        == 409
    )
    # A different browser submission key cannot bypass active-answer deduplication.
    assert (
        c.post(
            f"/api/v1/courses/{ids['course']}/batches",
            json=dict(body, idempotency_key="another-browser-key"),
        ).status_code
        == 409
    )
    drain(factory)
    tasks = [c.get(f"/api/v1/tasks/{pk}").json() for pk in task_ids(c, ids)]
    assert [t["status"] for t in tasks] == ["COMPLETED", "COMPLETED", "FAILED"]
    assert tasks[0]["result"]["status"] == "REVIEW_REQUIRED"
    assert tasks[1]["result"]["suggested_total"] is None
    assert tasks[2]["result"]["suggested_total"] is None
    assert (
        c.post(
            f"/api/v1/tasks/{tasks[2]['id']}/reviews", json=confirmation(tasks[0])
        ).status_code
        == 400
    )
    assert c.post(f"/api/v1/tasks/{tasks[2]['id']}/retry", json={}).status_code == 201
    assert c.post(f"/api/v1/tasks/{tasks[2]['id']}/retry", json={}).status_code == 409


def test_draft_confirmation_is_trial_and_never_official(setup):
    c, factory, ids, _, _ = setup
    batch(c, ids)
    drain(factory)
    pk = task_ids(c, ids)[0]
    t = c.get(f"/api/v1/tasks/{pk}").json()
    body = confirmation(t, zero=True)
    assert (
        c.post(f"/api/v1/tasks/{pk}/reviews", json=dict(body, release=True)).status_code
        == 400
    )
    r = c.post(f"/api/v1/tasks/{pk}/reviews", json=body)
    assert r.status_code == 201, r.text
    assert c.post(f"/api/v1/tasks/{pk}/reviews", json=body).status_code == 409
    confirmed = c.get(f"/api/v1/tasks/{pk}").json()["review"]
    assert (
        confirmed["trial_only"] and confirmed["confirmed"]["teacher_confirmed"] is False
    )
    assert confirmed["confirmed"]["confirmed_total"] == 0
    assert (
        len(
            list(
                csv.reader(
                    io.StringIO(
                        c.get(
                            f"/api/v1/courses/{ids['course']}/export.csv"
                        ).content.decode("utf-8-sig")
                    )
                )
            )
        )
        == 1
    )
    assert "试验记录" in c.get(
        f"/api/v1/courses/{ids['course']}/export.csv?trial=true"
    ).content.decode("utf-8-sig")
    assert (
        c.post(
            f"/api/v1/reviews/{r.json()['id']}/release", json={"release": True}
        ).status_code
        == 400
    )


def test_interrupted_task_requires_explicit_retry(setup):
    c, factory, ids, _, _ = setup
    batch(c, ids)
    with factory.begin() as db:
        t = db.scalar(select(Task).order_by(Task.id))
        pk = t.id
        t.status, t.claim_token, t.lease_until = (
            "RUNNING",
            "old-worker",
            time.time() - 1,
        )
    assert recover(factory) == 1
    drain(factory)
    task = c.get(f"/api/v1/tasks/{pk}").json()
    assert task["status"] == "NEEDS_ATTENTION" and task["result"] is None
    assert batch(c, ids, key="do-not-repeat-uncertain")[0].status_code == 409
    assert c.post(f"/api/v1/tasks/{pk}/retry", json={}).status_code == 201


def test_worker_claim_is_atomic(setup):
    c, factory, ids, _, _ = setup
    _, body = batch(c, ids)
    with factory.begin() as db:
        db.query(Task).filter(Task.answer_pk != body["answer_ids"][0]).delete()
    calls = []
    f = json.loads((ROOT / "examples/synthetic.json").read_text(encoding="utf-8"))

    def provider(task):
        calls.append(task.id)
        return FixtureProvider(f["model_output"])

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: run_once(factory, provider), range(2)))
    assert len(calls) == 1


def test_formal_review_case_feedback_student_and_stale_export(setup):
    c, factory, ids, login, _ = setup
    # This is synthetic test provenance inside a temporary DB, never a real teacher record.
    with factory.begin() as db:
        q = db.get(Question, ids["question"])
        q.synthetic = False
        r = db.scalar(select(Rubric).where(Rubric.question_pk == q.id))
        rid = r.id
    r = c.post(
        f"/api/v1/rubrics/{rid}/publish",
        json={"version": "teacher-test-v1", "note": "Synthetic test approval"},
    )
    assert r.status_code == 201, r.text
    pub = r.json()["id"]
    # Persist tasks using a mocked live provider; no network requests are made.
    with factory.begin() as db:
        from c_app.services import create_batch

        u = db.get(User, ids["teacher"])
        a = db.scalar(
            select(Answer).where(
                Answer.question_pk == ids["question"],
                Answer.answer_id == "SYNTHETIC-A001",
            )
        )
        aid = a.id
        create_batch(
            db,
            u,
            ids["course"],
            [aid],
            pub,
            "structured",
            "live",
            "formal-flow-test",
            allow_live=True,
            acknowledged=True,
        )
    fixture = json.loads((ROOT / "examples/synthetic.json").read_text(encoding="utf-8"))
    run_once(factory, lambda _: FixtureProvider(fixture["model_output"]))
    pk = task_ids(c, ids)[0]
    task = c.get(f"/api/v1/tasks/{pk}").json()
    body = confirmation(task)
    invalid = copy.deepcopy(body)
    invalid["items"][0]["evidence"][0]["quote"] = "invented quote"
    assert c.post(f"/api/v1/tasks/{pk}/reviews", json=invalid).status_code == 400
    r = c.post(f"/api/v1/tasks/{pk}/reviews", json=body)
    assert r.status_code == 201, r.text
    review_id = r.json()["id"]
    assert c.get(f"/api/v1/questions/{ids['question']}/case-sets").json() == []
    r = c.post(
        f"/api/v1/rubrics/{pub}/case-sets",
        json={"review_ids": [review_id], "version": "set-v1"},
    )
    assert r.status_code == 201, r.text
    assert (
        c.post(
            f"/api/v1/rubrics/{rid}/case-sets",
            json={"review_ids": [review_id], "version": "wrong-version"},
        ).status_code
        == 400
    )
    assert (
        c.post(
            f"/api/v1/answers/{aid}/assign", json={"user_id": ids["student"]}
        ).status_code
        == 200
    )
    practice = {
        "practice_id": "P01",
        "question_id": "P-Q01",
        "source_question_id": "Q01",
        "criterion_ids": ["R2"],
        "text": "Synthetic exercise",
        "reference_answer": "Synthetic answer",
    }
    r = c.post(f"/api/v1/courses/{ids['course']}/practices", json={"data": practice})
    assert r.status_code == 201, r.text
    practice_pk = r.json()["id"]
    assert not c.get(f"/api/v1/reviews/{review_id}/feedback").json()[
        "practice_candidates"
    ]
    assert (
        c.post(f"/api/v1/practices/{practice_pk}/approve", json={}).status_code == 200
    )
    assert c.get(f"/api/v1/reviews/{review_id}/feedback").json()["practice_candidates"]
    login("student")
    assert c.get("/api/v1/student/results").json() == []
    assert c.get(f"/api/v1/reviews/{review_id}/feedback").status_code == 403
    login()
    assert (
        c.post(
            f"/api/v1/reviews/{review_id}/release", json={"release": True}
        ).status_code
        == 200
    )
    login("student")
    rows = c.get("/api/v1/student/results")
    assert rows.status_code == 200, rows.text
    assert len(rows.json()) == 1
    assert (
        "reference_answer"
        not in c.get(f"/api/v1/student/practices/{practice_pk}").json()["data"]
    )
    assert (
        c.post(
            f"/api/v1/student/practices/{practice_pk}/attempts",
            json={"text": "Synthetic submission"},
        ).status_code
        == 200
    )
    assert (
        "reference_answer"
        in c.get(f"/api/v1/student/practices/{practice_pk}").json()["data"]
    )
    assert not c.get("/api/v1/student/results").json()[0]["learning"][
        "practice_candidates"
    ]
    login()
    exported = c.get(f"/api/v1/courses/{ids['course']}/export.csv").content.decode(
        "utf-8-sig"
    )
    assert "SYNTHETIC-A001" in exported
    # Publishing another revision invalidates old exports without deleting the history.
    assert (
        c.post(
            f"/api/v1/rubrics/{rid}/publish",
            json={"version": "teacher-test-v2", "note": "Synthetic revised approval"},
        ).status_code
        == 201
    )
    assert c.get(f"/api/v1/tasks/{pk}").json()["stale"]
    assert "SYNTHETIC-A001" not in c.get(
        f"/api/v1/courses/{ids['course']}/export.csv"
    ).content.decode("utf-8-sig")
    login("student")
    assert c.get("/api/v1/student/results").json() == []


def test_legacy_import_preserves_database(setup, tmp_path):
    _, factory, ids, _, _ = setup
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE answers (id INTEGER, answer_id TEXT, confirmed_score INTEGER)"
        )
        db.execute("INSERT INTO answers VALUES (1, 'old-answer', 0)")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with factory.begin() as db:
        user = db.get(User, ids["teacher"])
        assert import_legacy(db, user, ids["course"], path)["legacy_records_added"] == 1
    with factory.begin() as db:
        assert (
            import_legacy(db, db.get(User, ids["teacher"]), ids["course"], path)[
                "legacy_records_added"
            ]
            == 0
        )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_input_errors_and_logs_do_not_echo_passwords(setup):
    c, _, _, _, _ = setup
    r = c.post(
        "/api/v1/login",
        json={
            "username": "admin",
            "password": "secret-should-not-echo",
            "unexpected": True,
        },
    )
    assert r.status_code == 422 and "secret-should-not-echo" not in r.text
    assert (
        c.post(
            "/api/v1/login",
            json={"username": "admin", "password": "bad"},
            headers={"Origin": "https://other.example"},
        ).status_code
        == 403
    )
    assert (
        c.post(
            "/api/v1/login", content="bad", headers={"Content-Type": "text/plain"}
        ).status_code
        == 415
    )


def test_live_call_requires_server_opt_in_and_ack(setup):
    c, _, ids, _, _ = setup
    assert batch(c, ids, provider="live")[0].status_code == 400


def test_session_revocation(setup):
    c, _, _, _, _ = setup
    old = c.cookies.get("c_session")
    assert c.post("/api/v1/logout", json={}).status_code == 200
    c.cookies.set("c_session", old)
    assert c.get("/api/v1/me").status_code == 401


def test_unused_question_edit_and_versioned_question_freeze(setup):
    c, _, ids, _, _ = setup
    data = {"question_id": "UNUSED", "text": "Original", "max_score": 2}
    r = c.post(f"/api/v1/courses/{ids['course']}/questions", json={"data": data})
    assert r.status_code == 201
    pk = r.json()["id"]
    assert (
        c.patch(
            f"/api/v1/questions/{pk}", json={"data": dict(data, text="Edited")}
        ).status_code
        == 200
    )
    q = c.get(f"/api/v1/courses/{ids['course']}/questions").json()
    assert next(q for q in q if q["id"] == pk)["data"]["text"] == "Edited"
    original = c.get(f"/api/v1/courses/{ids['course']}/questions").json()[0]["data"]
    assert (
        c.patch(
            f"/api/v1/questions/{ids['question']}",
            json={"data": dict(original, text="Do not overwrite")},
        ).status_code
        == 409
    )


def test_concurrent_review_has_one_confirmation(setup):
    c, factory, ids, login, app = setup
    batch(c, ids)
    drain(factory)
    pk = task_ids(c, ids)[0]
    body = confirmation(c.get(f"/api/v1/tasks/{pk}").json(), zero=True)
    # Independent requests share the same valid session, as two browser tabs would.
    token, csrf = c.cookies.get("c_session"), c.headers["x-csrf-token"]

    def confirm(_):
        client = TestClient(app)
        client.cookies.set("c_session", token)
        return client.post(
            f"/api/v1/tasks/{pk}/reviews", json=body, headers={"x-csrf-token": csrf}
        ).status_code

    with ThreadPoolExecutor(2) as pool:
        results = sorted(pool.map(confirm, range(2)))
    assert results == [201, 409]
    with factory() as db:
        assert len(list(db.scalars(select(Review).where(Review.task_id == pk)))) == 1

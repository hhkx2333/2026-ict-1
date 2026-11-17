"""Validate A's actual 15-question import transaction in an isolated SQLite DB."""

import tempfile
from pathlib import Path
from sqlalchemy import func, select
from .db import Base, Course, Draft, Review, Rubric, make_engine, sessions
from .security import create_user
from .source_data import WORK, fetch_snapshot, seed_a


def main():
    snapshot = fetch_snapshot()
    WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="a-validation-", dir=WORK) as tmp:
        engine = make_engine("sqlite:///" + (Path(tmp) / "verify.sqlite3").as_posix())
        Base.metadata.create_all(engine)
        factory = sessions(engine)
        with factory.begin() as db:
            user = create_user(
                db, "synthetic-validation", "not-a-real-account-password", "admin"
            )
            course = Course(name="A import validation")
            db.add(course)
            db.flush()
            result = seed_a(db, user, course.id, snapshot)
            db.flush()
            assert result == {
                "questions": 15,
                "answers_added": 1582,
                "candidates_added": 92,
                "teacher_approvals_created": 0,
            }, result
            assert db.scalar(select(func.count(Review.id))) == 0
            assert all(r.data["status"] == "DRAFT" for r in db.scalars(select(Rubric)))
            assert not any(
                d.data.get("teacher_approved") for d in db.scalars(select(Draft))
            )
        with factory.begin() as db:
            repeated = seed_a(db, db.get(type(user), user.id), course.id, snapshot)
            assert repeated["answers_added"] == repeated["candidates_added"] == 0
        engine.dispose()
    print(
        "A delivery verified: 15 questions / 1582 unscored answers / 92 unapproved candidates; repeat import is idempotent."
    )


if __name__ == "__main__":
    main()

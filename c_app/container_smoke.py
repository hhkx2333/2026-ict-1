"""CI-only synthetic smoke check of a separately running worker."""

import os
import secrets
import time
from sqlalchemy import select
from .cli import seed_synthetic
from .db import Answer, Course, Membership, Rubric, Task, make_engine, sessions
from .security import create_user
from .services import create_batch


def main():
    if os.environ.get("C_ENV") == "production":
        raise SystemExit("Synthetic CI smoke is disabled in production.")
    factory = sessions(make_engine())
    with factory.begin() as db:
        u = create_user(
            db,
            "synthetic-ci-" + secrets.token_hex(4),
            secrets.token_urlsafe(24),
            "admin",
        )
        c = Course(name="CI SYNTHETIC ONLY")
        db.add(c)
        db.flush()
        db.add(Membership(course_id=c.id, user_id=u.id))
        db.flush()
        q = seed_synthetic(db, u, c.id)
        a = db.scalar(
            select(Answer).where(
                Answer.question_pk == q, Answer.answer_id == "SYNTHETIC-A001"
            )
        )
        r = db.scalar(select(Rubric).where(Rubric.question_pk == q))
        b = create_batch(
            db,
            u,
            c.id,
            [a.id],
            r.id,
            "structured",
            "synthetic",
            "synthetic-container-check",
            allow_draft=True,
        )
        batch_id = b.id
    for _ in range(30):
        with factory() as db:
            t = db.scalar(select(Task).where(Task.batch_id == batch_id))
            if t.status == "COMPLETED":
                assert (
                    t.result["status"] == "REVIEW_REQUIRED"
                    and t.result["teacher_confirmed"] is False
                )
                print(
                    "Separate worker processed synthetic task; result remains unconfirmed."
                )
                return
            if t.status == "FAILED":
                raise SystemExit("Synthetic worker check failed: " + str(t.error))
        time.sleep(1)
    raise SystemExit("Worker did not process the synthetic task within 30 seconds.")


if __name__ == "__main__":
    main()

"""Local setup, immutable source imports and read-only legacy migration."""

import argparse
import getpass
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from .db import (
    ROOT,
    Course,
    LegacyRecord,
    Membership,
    Question,
    User,
    make_engine,
    sessions,
)
from .security import audit, course_access, create_user, require
from .services import add_question, add_rubric, import_answers


def migrate():
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


def seed_synthetic(db, user, course_id):
    course_access(db, user, course_id, True)
    f = json.loads((ROOT / "examples/synthetic.json").read_text(encoding="utf-8"))
    q = db.scalar(
        select(Question).where(
            Question.course_id == course_id,
            Question.question_id == f["question"]["question_id"],
        )
    )
    if q:
        require(q.synthetic, "同题号已被真实题目使用。")
        return q.id
    q = add_question(
        db,
        user,
        course_id,
        f["question"],
        {"kind": "synthetic", "notice": "合成接口回放，不是教师审核或模型效果证据"},
        True,
    )
    add_rubric(db, user, q.id, dict(f["rubric"], status="DRAFT"))
    a = f["answer"]
    rows = [
        {"q_id": a["question_id"], "answer_id": a["answer_id"], "answer": a["text"]},
        {"q_id": a["question_id"], "answer_id": "SYNTHETIC-EMPTY", "answer": ""},
        {"q_id": a["question_id"], "answer_id": "SYNTHETIC-FAIL", "answer": a["text"]},
    ]
    import_answers(
        db,
        user,
        q.id,
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        {"kind": "synthetic"},
    )
    return q.id


def import_legacy(db, user, course_id, path):
    course_access(db, user, course_id, True)
    path = Path(path).resolve()
    require(path.is_file(), "旧数据库不存在。")
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM answers")]
    count = 0
    for row in rows:
        key = hashlib.sha256((source_hash + ":" + str(row["id"])).encode()).hexdigest()
        if db.get(LegacyRecord, key):
            continue
        db.add(
            LegacyRecord(
                source_key=key,
                course_id=course_id,
                data={
                    "notice": "旧原型记录、身份未核验；不进入正式成绩或审核案例",
                    "teacher_confirmed": False,
                    "source_sha256": source_hash,
                    "original": row,
                },
            )
        )
        count += 1
    require(
        hashlib.sha256(path.read_bytes()).hexdigest() == source_hash,
        "原数据库在迁移期间变化，请停止旧服务后重试。",
    )
    audit(db, user, "read_only_legacy_import", source_hash)
    return {"legacy_records_added": count, "original_unchanged": True}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest="action", required=True)
    subs.add_parser("migrate")
    bootstrap = subs.add_parser("bootstrap")
    bootstrap.add_argument("--username", required=True)
    for action in ("demo", "import-a", "import-legacy"):
        sub = subs.add_parser(action)
        sub.add_argument("--username", required=True)
        sub.add_argument("--course-id", type=int)
        if action == "import-legacy":
            sub.add_argument(
                "--source", type=Path, default=ROOT / "c-prototype/work/grading.sqlite3"
            )
        if action == "import-a":
            sub.add_argument("--source", type=Path)
    args = p.parse_args()
    if args.action == "migrate":
        migrate()
        print("Database migration complete.")
        return
    factory = sessions(make_engine())
    with factory.begin() as db:
        if args.action == "bootstrap":
            require(
                db.scalar(select(User).where(User.role == "admin")) is None,
                "管理员已存在，请登录后管理用户。",
            )
            password = os.environ.get("C_BOOTSTRAP_PASSWORD") or getpass.getpass(
                "Admin password (12+ characters): "
            )
            u = create_user(db, args.username, password, "admin")
            audit(db, u, "bootstrap_admin", u.id)
            print(
                "Administrator created. No teacher identity or approval has been fabricated."
            )
            return
        user = db.scalar(
            select(User).where(User.username == args.username, User.active.is_(True))
        )
        require(
            user is not None and user.role in ("admin", "teacher"),
            "请选择现有教师或管理员。",
        )
        if args.course_id:
            course_id = args.course_id
            course_access(db, user, course_id, True)
        else:
            c = Course(
                name="合成流程演示" if args.action == "demo" else "数据结构 · 开发资料"
            )
            db.add(c)
            db.flush()
            db.add(Membership(course_id=c.id, user_id=user.id))
            db.flush()
            course_id = c.id
        if args.action == "demo":
            result = {"synthetic_question_id": seed_synthetic(db, user, course_id)}
        elif args.action == "import-a":
            from .source_data import fetch_snapshot, seed_a

            result = seed_a(db, user, course_id, args.source or fetch_snapshot())
        else:
            result = import_legacy(db, user, course_id, args.source)
        print(json.dumps(dict(course_id=course_id, **result), ensure_ascii=False))


if __name__ == "__main__":
    main()

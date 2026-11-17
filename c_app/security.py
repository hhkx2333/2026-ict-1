import hashlib
import secrets
import time
from datetime import datetime, timezone
from pwdlib import PasswordHash
from sqlalchemy import case, or_, select
from .db import Audit, Course, LoginLimit, LoginSession, Membership, User

PASSWORDS = PasswordHash.recommended()
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(20))


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def require(condition, message, status=400):
    if not condition:
        from fastapi import HTTPException

        raise HTTPException(status, message)


def audit(db, user, action, target):
    db.add(Audit(actor_id=user.id, action=action, target=str(target), created_at=now()))


def create_user(db, username, password, role):
    require(
        2 <= len(username.strip()) <= 80 and username == username.strip(),
        "用户名长度应为 2–80。",
    )
    require(12 <= len(password) <= 200, "密码长度应为 12–200。")
    require(role in ("admin", "teacher", "student"), "角色无效。")
    require(
        not db.scalar(select(User).where(User.username == username)),
        "用户名已存在。",
        409,
    )
    user = User(username=username, password_hash=PASSWORDS.hash(password), role=role)
    db.add(user)
    db.flush()
    return user


def course_access(db, user, course_id, write=False):
    require(db.get(Course, course_id) is not None, "课程不存在。", 404)
    require(
        user.role == "admin" or db.get(Membership, (course_id, user.id)) is not None,
        "无权访问该课程。",
        403,
    )
    require(not write or user.role in ("admin", "teacher"), "需要教师权限。", 403)


def login(db, username, password, ip):
    tick = time.time()
    # Separate IP and username buckets prevent both password spraying and per-user bursts.
    for key in (digest("ip:" + ip), digest("user:" + username)):
        if db.bind.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        statement = insert(LoginLimit).values(key=key, count=1, reset_at=tick + 900)
        statement = statement.on_conflict_do_update(
            index_elements=[LoginLimit.key],
            set_={
                "count": case(
                    (LoginLimit.reset_at <= tick, 1), else_=LoginLimit.count + 1
                ),
                "reset_at": case(
                    (LoginLimit.reset_at <= tick, tick + 900), else_=LoginLimit.reset_at
                ),
            },
            where=or_(LoginLimit.reset_at <= tick, LoginLimit.count < 20),
        ).returning(LoginLimit.count)
        require(
            db.execute(statement).scalar() is not None,
            "登录尝试过多，请 15 分钟后重试。",
            429,
        )
    db.commit()  # Failed authentication must still consume a rate-limit attempt.
    user = db.scalar(select(User).where(User.username == username))
    valid = PASSWORDS.verify(password, user.password_hash if user else DUMMY_HASH)
    require(valid and user is not None and user.active, "用户名或密码错误。", 401)
    token = secrets.token_urlsafe(32)
    session = LoginSession(
        token_hash=digest(token),
        user_id=user.id,
        csrf=secrets.token_urlsafe(32),
        expires=tick + 28800,
    )
    db.add(session)
    audit(db, user, "login", user.id)
    db.commit()
    return user, session, token

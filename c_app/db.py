"""Portable persistence. All score, approval and task history is append-only."""

import os
from pathlib import Path
from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = "sqlite:///" + (ROOT / "c-prototype/work/application.sqlite3").as_posix()


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    csrf: Mapped[str] = mapped_column(String(64))
    expires: Mapped[float] = mapped_column(Float)


class LoginLimit(Base):
    __tablename__ = "login_limits"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    reset_at: Mapped[float] = mapped_column(Float)


class Course(Base):
    __tablename__ = "courses"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))


class Membership(Base):
    __tablename__ = "memberships"
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("course_id", "question_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    question_id: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict] = mapped_column(JSON)
    source: Mapped[dict] = mapped_column(JSON, default=dict)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    current_rubric_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Rubric(Base):
    __tablename__ = "rubrics"
    __table_args__ = (UniqueConstraint("question_pk", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    question_pk: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    version: Mapped[str] = mapped_column(String(160))
    data: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    published_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    published_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approval_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (UniqueConstraint("question_pk", "answer_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    question_pk: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    answer_id: Mapped[str] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[dict] = mapped_column(JSON, default=dict)
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )


class Batch(Base):
    __tablename__ = "batches"
    __table_args__ = (UniqueConstraint("course_id", "idempotency_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[str] = mapped_column(String(40))


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"))
    answer_pk: Mapped[int] = mapped_column(ForeignKey("answers.id"))
    rubric_id: Mapped[int] = mapped_column(ForeignKey("rubrics.id"))
    request: Mapped[dict] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(100), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[float | None] = mapped_column(Float, nullable=True)
    retry_of: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id"), nullable=True, unique=True
    )
    created_at: Mapped[str] = mapped_column(String(40))


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), unique=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    confirmed: Mapped[dict] = mapped_column(JSON)
    trial_only: Mapped[bool] = mapped_column(Boolean)
    released: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40))


# Both supported databases enforce this even when two web processes submit at once.
Index(
    "uq_active_task_answer",
    Task.answer_pk,
    unique=True,
    sqlite_where=Task.status.in_(["PENDING", "RUNNING", "NEEDS_ATTENTION"]),
    postgresql_where=Task.status.in_(["PENDING", "RUNNING", "NEEDS_ATTENTION"]),
)


class WorkerState(Base):
    __tablename__ = "worker_state"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    heartbeat: Mapped[float] = mapped_column(Float)


class CaseSet(Base):
    __tablename__ = "case_sets"
    __table_args__ = (UniqueConstraint("rubric_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    rubric_id: Mapped[int] = mapped_column(ForeignKey("rubrics.id"))
    version: Mapped[str] = mapped_column(String(160))
    data: Mapped[list] = mapped_column(JSON)
    approved_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[str] = mapped_column(String(40))


class Draft(Base):
    __tablename__ = "drafts"
    id: Mapped[int] = mapped_column(primary_key=True)
    question_pk: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    data: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    source: Mapped[dict] = mapped_column(JSON, default=dict)


class Practice(Base):
    __tablename__ = "practices"
    __table_args__ = (UniqueConstraint("course_id", "practice_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    practice_id: Mapped[str] = mapped_column(String(100))
    data: Mapped[dict] = mapped_column(JSON)
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )


class PracticeAttempt(Base):
    __tablename__ = "practice_attempts"
    __table_args__ = (UniqueConstraint("student_id", "practice_pk"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    practice_pk: Mapped[int] = mapped_column(ForeignKey("practices.id"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40))


class LegacyRecord(Base):
    __tablename__ = "legacy_records"
    source_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    data: Mapped[dict] = mapped_column(JSON)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[str] = mapped_column(String(40))


def make_engine(url=None):
    url = url or os.environ.get("DATABASE_URL", DEFAULT_DB)
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 30}
        if url.startswith("sqlite")
        else {},
        pool_pre_ping=True,
    )
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")

    return engine


def sessions(engine):
    return sessionmaker(engine, expire_on_commit=False)

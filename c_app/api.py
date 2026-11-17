"""Authenticated C API. Approval provenance is always constructed on the server."""

import os
import secrets
import time
from typing import Annotated, Literal
from urllib.parse import urlsplit
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from backend.grading.core import ValidationError
from . import services as svc
from .db import (
    ROOT,
    Answer,
    Audit,
    Batch,
    CaseSet,
    Course,
    Draft,
    LegacyRecord,
    LoginSession,
    Membership,
    Practice,
    PracticeAttempt,
    Question,
    Review,
    Rubric,
    Task,
    User,
    WorkerState,
    make_engine,
    sessions,
)
from .security import audit, course_access, create_user, digest, login, now, require


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Credentials(Input):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class NewUser(Credentials):
    role: Literal["admin", "teacher", "student"]


class Name(Input):
    name: str = Field(min_length=1, max_length=160)


class Member(Input):
    user_id: int


class Document(Input):
    data: dict


class ImportFile(Input):
    text: str


class Publish(Input):
    version: str = Field(min_length=1, max_length=160)
    note: str = Field(min_length=1, max_length=2000)


class NewBatch(Input):
    answer_ids: list[int] = Field(min_length=1, max_length=1000)
    rubric_id: int
    mode: Literal["structured", "checklist"] = "structured"
    provider: Literal["synthetic", "live"] = "synthetic"
    idempotency_key: str = Field(min_length=8, max_length=100)
    case_set_id: int | None = None
    allow_draft: bool = False
    acknowledge_external_processing: bool = False


class Retry(Input):
    acknowledge_external_processing: bool = False


class Confirm(Input):
    items: list[dict] = Field(min_length=1, max_length=100)
    release: bool = False


class Release(Input):
    release: bool


class ApproveCases(Input):
    review_ids: list[int] = Field(min_length=1, max_length=1000)
    version: str = Field(min_length=1, max_length=160)


class Correction(Input):
    assessment: dict
    scope: Literal["answer_only", "general_rule"] = "answer_only"
    proposed_rule: str | None = Field(default=None, max_length=10000)


def create_app(engine=None):
    app = FastAPI(
        title="校准阅卷 · C",
        version="1.0.0",
        docs_url="/docs" if os.environ.get("C_ENV") != "production" else None,
        redoc_url=None,
    )
    app.state.engine = engine or make_engine()
    app.state.factory = sessions(app.state.engine)
    app.state.redis = None
    if os.environ.get("REDIS_URL"):
        from redis import Redis

        app.state.redis = Redis.from_url(
            os.environ["REDIS_URL"],
            socket_timeout=2,
            socket_connect_timeout=2,
            decode_responses=True,
        )

    @app.exception_handler(RequestValidationError)
    async def bad_request(request, exc):
        # Pydantic's default response echoes input, including passwords and answer text.
        return JSONResponse(
            {
                "detail": "字段格式无效，请检查必填项和数据类型。",
                "fields": [list(e["loc"]) for e in exc.errors()],
            },
            422,
        )

    @app.exception_handler(ValidationError)
    async def invalid_grading(request, exc):
        return JSONResponse({"detail": str(exc)}, 400)

    @app.exception_handler(IntegrityError)
    async def conflict(request, exc):
        return JSONResponse(
            {"detail": "记录已被其他请求更新或存在关联冲突，请刷新重试。"}, 409
        )

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        return JSONResponse({"detail": "服务暂时不可用，请检查服务端状态。"}, 503)

    @app.middleware("http")
    async def safety(request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin and (
                urlsplit(origin).netloc != request.headers.get("host")
                or urlsplit(origin).scheme != request.url.scheme
            ):
                return JSONResponse({"detail": "请求来源不匹配。"}, 403)
            if (
                request.headers.get("content-type", "").split(";")[0].strip().lower()
                != "application/json"
            ):
                return JSONResponse({"detail": "请使用 JSON 请求。"}, 415)
            data = bytearray()
            async for part in request.stream():
                data.extend(part)
                if len(data) > 4_500_000:
                    return JSONResponse({"detail": "请求超过 4.5 MB。"}, 413)
            request._body = bytes(data)
        try:
            response = await call_next(request)
        except Exception as exc:
            import logging

            logging.getLogger("c_app").warning(
                "request failed type=%s", type(exc).__name__
            )
            response = JSONResponse(
                {"detail": "服务暂时不可用，请检查服务端状态。"}, 503
            )
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "same-origin",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    def db_session():
        with app.state.factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    DB = Annotated[object, Depends(db_session)]

    def current(request: Request, db: DB):
        token = request.cookies.get("c_session", "")
        session = db.get(LoginSession, digest(token)) if token else None
        require(
            session is not None and session.expires > time.time(), "请先登录。", 401
        )
        user = db.get(User, session.user_id)
        require(user is not None and user.active, "账号已停用。", 401)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            require(
                secrets.compare_digest(
                    request.headers.get("x-csrf-token", ""), session.csrf
                ),
                "会话校验失败，请刷新页面。",
                403,
            )
        return user

    USER = Annotated[User, Depends(current)]

    def admin(user):
        require(user.role == "admin", "需要管理员权限。", 403)

    @app.get("/")
    def home():
        return FileResponse(ROOT / "c-prototype/index.html")

    @app.get("/assets/{name}")
    def asset(name: str):
        require(name in ("app.js", "style.css"), "文件不存在。", 404)
        return FileResponse(ROOT / "c-prototype" / name)

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready(db: DB):
        try:
            revision = db.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
            require(revision == "0002_task_guards", "数据库迁移未完成。", 503)
            db.execute(select(Course.id).limit(1))
            if app.state.redis:
                app.state.redis.ping()
            if os.environ.get("C_REQUIRE_WORKER") == "true":
                worker = db.get(WorkerState, "default")
                require(
                    worker is not None and time.time() - worker.heartbeat < 300,
                    "后台评分任务服务尚未就绪。",
                    503,
                )
        except Exception:
            raise HTTPException(503, "数据库或已配置的缓存尚未就绪。")
        return {
            "status": "ready",
            "database": "ok",
            "redis": "ok" if app.state.redis else "disabled",
            "live_model": os.environ.get("C_ALLOW_LIVE") == "true",
        }

    @app.post("/api/v1/login")
    def sign_in(body: Credentials, request: Request, response: Response, db: DB):
        if app.state.redis:
            try:
                key = "c:login:" + digest(request.client.host)
                with app.state.redis.pipeline() as p:
                    p.incr(key)
                    p.expire(key, 900, nx=True)
                    count, _ = p.execute()
                require(count <= 30, "登录尝试过多，请稍后重试。", 429)
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(503, "登录限流服务暂时不可用。")
        user, session, token = login(
            db, body.username, body.password, request.client.host
        )
        response.set_cookie(
            "c_session",
            token,
            httponly=True,
            secure=os.environ.get("C_ENV") == "production",
            samesite="strict",
            max_age=28800,
            path="/",
        )
        return {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "csrf": session.csrf,
        }

    @app.get("/api/v1/me")
    def me(request: Request, db: DB, user: USER):
        session = db.get(LoginSession, digest(request.cookies["c_session"]))
        return {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "csrf": session.csrf,
        }

    @app.post("/api/v1/logout")
    def logout(request: Request, response: Response, db: DB, user: USER):
        db.execute(
            delete(LoginSession).where(
                LoginSession.token_hash == digest(request.cookies["c_session"])
            )
        )
        response.delete_cookie("c_session", path="/")
        return {"ok": True}

    @app.get("/api/v1/users")
    def users(db: DB, user: USER):
        admin(user)
        return [
            {"id": u.id, "username": u.username, "role": u.role, "active": u.active}
            for u in db.scalars(select(User).order_by(User.id))
        ]

    @app.post("/api/v1/users", status_code=201)
    def new_user(body: NewUser, db: DB, user: USER):
        admin(user)
        new = create_user(db, body.username, body.password, body.role)
        audit(db, user, "create_user", new.id)
        return {"id": new.id}

    @app.get("/api/v1/courses")
    def courses(db: DB, user: USER):
        query = select(Course)
        if user.role != "admin":
            query = query.join(Membership).where(Membership.user_id == user.id)
        return [
            {"id": c.id, "name": c.name} for c in db.scalars(query.order_by(Course.id))
        ]

    @app.post("/api/v1/courses", status_code=201)
    def new_course(body: Name, db: DB, user: USER):
        require(user.role in ("admin", "teacher"), "需要教师权限。", 403)
        require(body.name.strip(), "课程名称不能为空。")
        c = Course(name=body.name.strip())
        db.add(c)
        db.flush()
        db.add(Membership(course_id=c.id, user_id=user.id))
        audit(db, user, "create_course", c.id)
        return {"id": c.id, "name": c.name}

    @app.get("/api/v1/courses/{course_id}/members")
    def members(course_id: int, db: DB, user: USER):
        course_access(db, user, course_id, True)
        return [
            {"id": u.id, "username": u.username, "role": u.role}
            for u in db.scalars(
                select(User).join(Membership).where(Membership.course_id == course_id)
            )
        ]

    @app.post("/api/v1/courses/{course_id}/members")
    def add_member(course_id: int, body: Member, db: DB, user: USER):
        course_access(db, user, course_id, True)
        target = db.get(User, body.user_id)
        require(target is not None and target.active, "用户不存在。", 404)
        require(
            user.role == "admin" or target.role == "student",
            "只有管理员可分配教师权限。",
            403,
        )
        if db.get(Membership, (course_id, target.id)) is None:
            db.add(Membership(course_id=course_id, user_id=target.id))
        audit(db, user, "add_course_member", f"{course_id}:{target.id}")
        return {"ok": True}

    @app.get("/api/v1/courses/{course_id}/questions")
    def questions(course_id: int, db: DB, user: USER):
        course_access(db, user, course_id, True)
        return [
            {
                "id": q.id,
                "data": q.data,
                "source": q.source,
                "synthetic": q.synthetic,
                "current_rubric_id": q.current_rubric_id,
            }
            for q in db.scalars(
                select(Question)
                .where(Question.course_id == course_id)
                .order_by(Question.id)
            )
        ]

    @app.post("/api/v1/courses/{course_id}/questions", status_code=201)
    def new_question(course_id: int, body: Document, db: DB, user: USER):
        q = svc.add_question(db, user, course_id, body.data)
        return {"id": q.id}

    @app.patch("/api/v1/questions/{pk}")
    def edit_question(pk: int, body: Document, db: DB, user: USER):
        q = svc.get_question(db, user, pk, True)
        require(
            not db.scalar(select(Rubric.id).where(Rubric.question_pk == pk))
            and not db.scalar(select(Answer.id).where(Answer.question_pk == pk)),
            "已有规则或答案的题目须另建题号，避免改写历史评分依据。",
            409,
        )
        require(body.data.get("question_id") == q.question_id, "题号不能修改。")
        data = body.data
        require(
            set(data) <= {"question_id", "text", "max_score", "reference_answer"}
            and {"question_id", "text", "max_score"} <= set(data),
            "题目字段无效。",
        )
        require(
            isinstance(data["text"], str)
            and 1 <= len(data["text"]) <= 100000
            and type(data["max_score"]) in (int, float)
            and 0 < data["max_score"] <= 10000,
            "题目正文或满分无效。",
        )
        q.data = data
        audit(db, user, "edit_unversioned_question", pk)
        return {"id": pk}

    @app.get("/api/v1/questions/{pk}/rubrics")
    def rubrics(pk: int, db: DB, user: USER):
        svc.get_question(db, user, pk, True)
        return [
            {
                "id": r.id,
                "data": r.data,
                "published_by": r.published_by,
                "published_at": r.published_at,
                "approval_note": r.approval_note,
            }
            for r in db.scalars(
                select(Rubric).where(Rubric.question_pk == pk).order_by(Rubric.id)
            )
        ]

    @app.post("/api/v1/questions/{pk}/rubrics", status_code=201)
    def new_rubric(pk: int, body: Document, db: DB, user: USER):
        return {"id": svc.add_rubric(db, user, pk, body.data).id}

    @app.post("/api/v1/rubrics/{pk}/publish", status_code=201)
    def publish(pk: int, body: Publish, db: DB, user: USER):
        return {"id": svc.publish_rubric(db, user, pk, body.version, body.note).id}

    @app.get("/api/v1/questions/{pk}/answers")
    def answers(pk: int, db: DB, user: USER):
        q = svc.get_question(db, user, pk, True)
        result = []
        for a in db.scalars(
            select(Answer).where(Answer.question_pk == pk).order_by(Answer.id)
        ):
            tasks = list(
                db.scalars(select(Task).where(Task.answer_pk == a.id).order_by(Task.id))
            )
            latest = tasks[-1] if tasks else None
            review = (
                db.scalar(select(Review).where(Review.task_id == latest.id))
                if latest
                else None
            )
            result.append(
                {
                    "id": a.id,
                    "answer_id": a.answer_id,
                    "text": a.text,
                    "source": a.source,
                    "student_id": a.student_id,
                    "latest_task_id": latest.id if latest else None,
                    "status": latest.status if latest else "IMPORTED",
                    "suggested_status": latest.result["status"]
                    if latest and latest.result
                    else None,
                    "stale": bool(
                        latest
                        and q.current_rubric_id
                        and q.current_rubric_id != latest.rubric_id
                    ),
                    "review_id": review.id if review else None,
                }
            )
        return result

    @app.post("/api/v1/questions/{pk}/answers/import")
    def import_file(pk: int, body: ImportFile, db: DB, user: USER):
        return svc.import_answers(db, user, pk, body.text)

    @app.post("/api/v1/answers/{pk}/assign")
    def assign(pk: int, body: Member, db: DB, user: USER):
        a = db.get(Answer, pk)
        require(a is not None, "答案不存在。", 404)
        q = svc.get_question(db, user, a.question_pk, True)
        student = db.get(User, body.user_id)
        require(
            student
            and student.role == "student"
            and db.get(Membership, (q.course_id, student.id)),
            "请选择本课程学生。",
        )
        require(a.student_id in (None, student.id), "已分配答案不能静默改给他人。", 409)
        a.student_id = student.id
        audit(db, user, "assign_answer", a.id)
        return {"ok": True}

    @app.post("/api/v1/courses/{course_id}/batches", status_code=201)
    def batch(course_id: int, body: NewBatch, db: DB, user: USER):
        b = svc.create_batch(
            db,
            user,
            course_id,
            body.answer_ids,
            body.rubric_id,
            body.mode,
            body.provider,
            body.idempotency_key,
            body.case_set_id,
            body.allow_draft,
            os.environ.get("C_ALLOW_LIVE") == "true",
            body.acknowledge_external_processing,
        )
        return {"id": b.id}

    @app.get("/api/v1/courses/{course_id}/batches")
    def batches(course_id: int, db: DB, user: USER):
        course_access(db, user, course_id, True)
        result = []
        for b in db.scalars(
            select(Batch).where(Batch.course_id == course_id).order_by(Batch.id.desc())
        ):
            tasks = list(
                db.scalars(select(Task).where(Task.batch_id == b.id).order_by(Task.id))
            )
            counts = {
                s: sum(t.status == s for t in tasks)
                for s in (
                    "PENDING",
                    "RUNNING",
                    "COMPLETED",
                    "FAILED",
                    "NEEDS_ATTENTION",
                    "RETRIED",
                )
            }
            result.append(
                {
                    "id": b.id,
                    "created_at": b.created_at,
                    "counts": counts,
                    "task_ids": [t.id for t in tasks],
                }
            )
        return result

    @app.get("/api/v1/tasks/{pk}")
    def task_detail(pk: int, db: DB, user: USER):
        t, _, q = svc.get_task(db, user, pk)
        r = db.scalar(select(Review).where(Review.task_id == t.id))
        return {
            "id": t.id,
            "status": t.status,
            "error": t.error,
            "request": t.request,
            "provider": t.provider,
            "result": t.result,
            "stale": bool(q.current_rubric_id and q.current_rubric_id != t.rubric_id),
            "review": {
                "id": r.id,
                "confirmed": r.confirmed,
                "trial_only": r.trial_only,
                "released": r.released,
            }
            if r
            else None,
        }

    @app.post("/api/v1/tasks/{pk}/retry", status_code=201)
    def retry(pk: int, body: Retry, db: DB, user: USER):
        return {
            "id": svc.retry_task(
                db,
                user,
                pk,
                body.acknowledge_external_processing,
                os.environ.get("C_ALLOW_LIVE") == "true",
            ).id
        }

    @app.post("/api/v1/tasks/{pk}/reviews", status_code=201)
    def review(pk: int, body: Confirm, db: DB, user: USER):
        return {"id": svc.review_task(db, user, pk, body.items, body.release).id}

    @app.post("/api/v1/tasks/{pk}/corrections", status_code=201)
    def correction(pk: int, body: Correction, db: DB, user: USER):
        return {
            "id": svc.create_correction(
                db, user, pk, body.assessment, body.scope, body.proposed_rule
            ).id
        }

    @app.post("/api/v1/reviews/{pk}/release")
    def release(pk: int, body: Release, db: DB, user: USER):
        r = db.get(Review, pk)
        require(r is not None, "复核不存在。", 404)
        t, _, q = svc.get_task(db, user, r.task_id)
        require(
            not r.trial_only and q.current_rubric_id == t.rubric_id,
            "试验或过期结果不能发布。",
        )
        r.released = body.release
        audit(db, user, "release_result" if body.release else "withdraw_result", pk)
        return {"ok": True}

    @app.get("/api/v1/reviews/{pk}/feedback")
    def learning(pk: int, db: DB, user: USER):
        r = db.get(Review, pk)
        require(r is not None, "复核不存在。", 404)
        return svc.feedback(db, user, r)

    @app.get("/api/v1/questions/{pk}/case-sets")
    def case_sets(pk: int, db: DB, user: USER):
        svc.get_question(db, user, pk, True)
        return [
            {
                "id": s.id,
                "rubric_id": s.rubric_id,
                "version": s.version,
                "count": len(s.data),
            }
            for s in db.scalars(
                select(CaseSet).join(Rubric).where(Rubric.question_pk == pk)
            )
        ]

    @app.post("/api/v1/rubrics/{pk}/case-sets", status_code=201)
    def approve(pk: int, body: ApproveCases, db: DB, user: USER):
        return {"id": svc.approve_cases(db, user, pk, body.review_ids, body.version).id}

    @app.get("/api/v1/questions/{pk}/drafts")
    def drafts(pk: int, db: DB, user: USER):
        svc.get_question(db, user, pk, True)
        return [
            {"id": d.id, "data": d.data, "source": d.source}
            for d in db.scalars(select(Draft).where(Draft.question_pk == pk))
        ]

    @app.get("/api/v1/courses/{course_id}/export.csv")
    def export(course_id: int, db: DB, user: USER, trial: bool = False):
        return Response(
            svc.csv_export(db, user, course_id, trial),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="trial-results.csv"'
                if trial
                else 'attachment; filename="teacher-confirmed.csv"'
            },
        )

    @app.get("/api/v1/courses/{course_id}/practices")
    def practices(course_id: int, db: DB, user: USER):
        course_access(db, user, course_id, True)
        return [
            {"id": p.id, "data": p.data, "approved": p.approved_by is not None}
            for p in db.scalars(select(Practice).where(Practice.course_id == course_id))
        ]

    @app.post("/api/v1/courses/{course_id}/practices", status_code=201)
    def new_practice(course_id: int, body: Document, db: DB, user: USER):
        course_access(db, user, course_id, True)
        data = body.data
        require(
            set(data)
            == {
                "practice_id",
                "question_id",
                "source_question_id",
                "criterion_ids",
                "text",
                "reference_answer",
            },
            "练习须包含题号、来源题号、得分点、正文和参考答案。",
        )
        require(
            all(
                isinstance(data[k], str) and data[k].strip() and len(data[k]) <= 10000
                for k in (
                    "practice_id",
                    "question_id",
                    "source_question_id",
                    "text",
                    "reference_answer",
                )
            ),
            "练习字段无效。",
        )
        require(
            len(data["practice_id"]) <= 100
            and isinstance(data["criterion_ids"], list)
            and data["criterion_ids"]
            and all(isinstance(c, str) for c in data["criterion_ids"]),
            "练习编号或得分点无效。",
        )
        q = db.scalar(
            select(Question).where(
                Question.course_id == course_id,
                Question.question_id == data["source_question_id"],
            )
        )
        require(
            q is not None and q.current_rubric_id is not None, "来源题目尚无正式规则。"
        )
        rubric = db.get(Rubric, q.current_rubric_id)
        require(
            set(data["criterion_ids"])
            <= {c["criterion_id"] for c in rubric.data["criteria"]},
            "练习得分点不属于来源规则。",
        )
        p = Practice(
            course_id=course_id,
            practice_id=data["practice_id"],
            data=dict(data, teacher_approved=False, rubric_version=rubric.version),
        )
        db.add(p)
        db.flush()
        audit(db, user, "create_practice_draft", p.id)
        return {"id": p.id}

    @app.post("/api/v1/practices/{pk}/approve")
    def approve_practice(pk: int, db: DB, user: USER):
        p = db.get(Practice, pk)
        require(p is not None, "练习不存在。", 404)
        course_access(db, user, p.course_id, True)
        require(p.approved_by is None, "练习已经审核。", 409)
        p.approved_by = user.id
        p.data = dict(p.data, teacher_approved=True)
        audit(db, user, "approve_practice", pk)
        return {"ok": True}

    @app.get("/api/v1/student/results")
    def student_results(db: DB, user: USER):
        require(user.role == "student", "需要学生账号。", 403)
        result = []
        rows = db.scalars(
            select(Review)
            .join(Task)
            .join(Answer)
            .join(Question)
            .join(Membership, Membership.course_id == Question.course_id)
            .where(Answer.student_id == user.id, Membership.user_id == user.id)
            .order_by(Review.id)
        )
        latest = {}
        for r in rows:
            t = db.get(Task, r.task_id)
            latest[t.answer_pk] = r
        for r in latest.values():
            t = db.get(Task, r.task_id)
            a = db.get(Answer, t.answer_pk)
            q = db.get(Question, a.question_pk)
            if not r.released or r.trial_only or q.current_rubric_id != t.rubric_id:
                continue
            result.append(
                {
                    "review_id": r.id,
                    "question": {
                        "question_id": q.question_id,
                        "text": q.data["text"],
                        "max_score": q.data["max_score"],
                    },
                    "answer_id": a.answer_id,
                    "answer": a.text,
                    "confirmed": r.confirmed,
                    "learning": svc.feedback(db, user, r),
                }
            )
        return result

    @app.get("/api/v1/student/practices/{pk}")
    def student_practice(pk: int, db: DB, user: USER):
        p = db.get(Practice, pk)
        require(p is not None and p.approved_by is not None, "练习不存在。", 404)
        course_access(db, user, p.course_id)
        require(user.role == "student", "需要学生账号。", 403)
        attempted = db.scalar(
            select(PracticeAttempt).where(
                PracticeAttempt.student_id == user.id, PracticeAttempt.practice_pk == pk
            )
        )
        return {
            "id": p.id,
            "data": {
                k: v
                for k, v in p.data.items()
                if k != "reference_answer" or attempted is not None
            },
            "attempt": attempted.text if attempted else None,
        }

    @app.post("/api/v1/student/practices/{pk}/attempts")
    def attempt(pk: int, body: ImportFile, db: DB, user: USER):
        p = db.get(Practice, pk)
        require(p is not None and p.approved_by is not None, "练习不存在。", 404)
        course_access(db, user, p.course_id)
        require(
            user.role == "student" and 1 <= len(body.text) <= 100000, "请输入练习答案。"
        )
        require(
            db.scalar(
                select(PracticeAttempt).where(
                    PracticeAttempt.student_id == user.id,
                    PracticeAttempt.practice_pk == pk,
                )
            )
            is None,
            "该练习已提交。",
            409,
        )
        db.add(
            PracticeAttempt(
                student_id=user.id, practice_pk=pk, text=body.text, created_at=now()
            )
        )
        audit(db, user, "submit_practice", pk)
        return {"ok": True}

    @app.get("/api/v1/courses/{course_id}/audit")
    def history(course_id: int, db: DB, user: USER):
        course_access(db, user, course_id, True)
        # Global account audit is admin-only; teachers use per-answer task/review history.
        admin(user)
        return [
            {
                "id": a.id,
                "actor_id": a.actor_id,
                "action": a.action,
                "target": a.target,
                "created_at": a.created_at,
            }
            for a in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(200))
        ]

    @app.get("/api/v1/courses/{course_id}/legacy")
    def legacy(course_id: int, db: DB, user: USER):
        course_access(db, user, course_id, True)
        return [
            {"source_key": r.source_key, "data": r.data}
            for r in db.scalars(
                select(LegacyRecord).where(LegacyRecord.course_id == course_id)
            )
        ]

    @app.api_route("/api/{old:path}", methods=["GET", "POST"])
    def retired(old: str):
        raise HTTPException(410, "旧匿名接口已停用，请使用新版工作台。")

    @app.get("/export.csv")
    def retired_export():
        raise HTTPException(410, "请登录后按课程导出。")

    return app

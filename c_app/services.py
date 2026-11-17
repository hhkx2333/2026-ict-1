"""Business boundaries: server-owned identities, immutable revisions, explicit approval."""

import copy
import csv
import io
import json
import uuid
from decimal import Decimal
from sqlalchemy import select
from backend.grading import (
    case_from_confirmed,
    confirmed_assessment,
    correction_draft,
    feedback_and_practice,
)
from backend.grading.core import validate_input
from backend.grading.checklist import validate_checklist
from .db import (
    Answer,
    Batch,
    CaseSet,
    Draft,
    Practice,
    PracticeAttempt,
    Question,
    Review,
    Rubric,
    Task,
)
from .security import audit, course_access, digest, now, require


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    )


def get_question(db, user, pk, write=False):
    q = db.get(Question, pk)
    require(q is not None, "题目不存在。", 404)
    course_access(db, user, q.course_id, write)
    return q


def get_task(db, user, pk, write=True):
    task = db.get(Task, pk)
    require(task is not None, "任务不存在。", 404)
    answer = db.get(Answer, task.answer_pk)
    q = get_question(db, user, answer.question_pk, write)
    if user.role == "student":
        require(answer.student_id == user.id, "无权查看该答案。", 403)
    return task, answer, q


def validate_rubric(question, rubric):
    require(
        isinstance(rubric, dict) and rubric.get("status") in ("DRAFT", "PUBLISHED"),
        "规则状态无效。",
    )
    validate_input(
        question,
        dict(rubric, status="PUBLISHED"),
        {"question_id": question["question_id"], "answer_id": "validation", "text": ""},
    )
    if any("checks" in c for c in rubric["criteria"]):
        validate_checklist(rubric)


def add_question(db, user, course_id, data, source=None, synthetic=False):
    course_access(db, user, course_id, True)
    require(
        set(data) <= {"question_id", "text", "max_score", "reference_answer"}
        and {"question_id", "text", "max_score"} <= set(data),
        "题目字段无效。",
    )
    require(
        isinstance(data["question_id"], str) and 1 <= len(data["question_id"]) <= 80,
        "题号无效。",
    )
    require(
        isinstance(data["text"], str) and 1 <= len(data["text"]) <= 100000,
        "题目正文无效。",
    )
    require(
        type(data["max_score"]) in (int, float)
        and Decimal(str(data["max_score"])).is_finite()
        and 0 < data["max_score"] <= 10000,
        "满分无效。",
    )
    require(
        not db.scalar(
            select(Question).where(
                Question.course_id == course_id,
                Question.question_id == data["question_id"],
            )
        ),
        "题号已存在。",
        409,
    )
    q = Question(
        course_id=course_id,
        question_id=data["question_id"],
        data=copy.deepcopy(data),
        source=source or {},
        synthetic=synthetic,
    )
    db.add(q)
    db.flush()
    audit(db, user, "create_question", q.id)
    return q


def add_rubric(db, user, question_pk, data):
    q = get_question(db, user, question_pk, True)
    require(data.get("status") == "DRAFT", "新规则必须先保存为草案。")
    validate_rubric(q.data, data)
    require(len(data["version"]) <= 160, "版本名过长。")
    require(
        not db.scalar(
            select(Rubric).where(
                Rubric.question_pk == q.id, Rubric.version == data["version"]
            )
        ),
        "版本已存在，请使用新版本名。",
        409,
    )
    r = Rubric(
        question_pk=q.id,
        version=data["version"],
        data=copy.deepcopy(data),
        created_by=user.id,
    )
    db.add(r)
    db.flush()
    audit(db, user, "save_rubric_draft", r.id)
    return r


def publish_rubric(db, user, pk, version, note):
    original = db.get(Rubric, pk)
    require(original is not None, "规则不存在。", 404)
    q = get_question(db, user, original.question_pk, True)
    require(original.data["status"] == "DRAFT", "请选择草案发布。")
    require(
        version != original.version and 1 <= len(version) <= 160 and note.strip(),
        "请填写新的发布版本名与实际审核说明。",
    )
    require(
        not db.scalar(
            select(Rubric).where(Rubric.question_pk == q.id, Rubric.version == version)
        ),
        "版本名已存在。",
        409,
    )
    data = dict(copy.deepcopy(original.data), version=version, status="PUBLISHED")
    validate_rubric(q.data, data)
    r = Rubric(
        question_pk=q.id,
        version=version,
        data=data,
        created_by=user.id,
        published_by=user.id,
        published_at=now(),
        approval_note=note,
    )
    db.add(r)
    db.flush()
    q.current_rubric_id = r.id
    audit(db, user, "publish_rubric", r.id)
    return r


def import_answers(db, user, question_pk, text, source=None):
    q = get_question(db, user, question_pk, True)
    require(
        isinstance(text, str) and len(text.encode("utf-8")) <= 4_000_000,
        "导入文件须小于 4 MB。",
    )
    rows = []
    for n, line in enumerate(text.removeprefix("\ufeff").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            require(False, f"第 {n} 行不是合法 JSON。")
        require(
            isinstance(row, dict) and set(row) == {"q_id", "answer_id", "answer"},
            f"第 {n} 行只能包含 q_id、answer_id、answer，无分数标签。",
        )
        require(
            row["q_id"] == q.question_id
            and isinstance(row["answer_id"], str)
            and 1 <= len(row["answer_id"]) <= 100
            and isinstance(row["answer"], str)
            and len(row["answer"]) <= 100000,
            f"第 {n} 行题号、编号或正文无效。",
        )
        rows.append(row)
    require(0 < len(rows) <= 10000, "答案数量应为 1–10000。")
    added = 0
    for row in rows:
        existing = db.scalar(
            select(Answer).where(
                Answer.question_pk == q.id, Answer.answer_id == row["answer_id"]
            )
        )
        if existing:
            require(
                existing.text == row["answer"],
                f"答案 {row['answer_id']} 正文冲突，整次导入取消。",
                409,
            )
        else:
            db.add(
                Answer(
                    question_pk=q.id,
                    answer_id=row["answer_id"],
                    text=row["answer"],
                    source=source or {"kind": "manual-import"},
                )
            )
            db.flush()
            added += 1
    audit(db, user, "import_answers", q.id)
    return {"added": added, "existing": len(rows) - added}


def create_batch(
    db,
    user,
    course_id,
    answer_ids,
    rubric_id,
    mode,
    provider,
    key,
    case_set_id=None,
    allow_draft=False,
    allow_live=False,
    acknowledged=False,
):
    course_access(db, user, course_id, True)
    require(
        mode in ("structured", "checklist") and provider in ("synthetic", "live"),
        "评分模式无效。",
    )
    require(
        0 < len(answer_ids) <= 1000 and len(set(answer_ids)) == len(answer_ids),
        "一次请选择 1–1000 份不同答案。",
    )
    specification = dict(
        answer_ids=answer_ids,
        rubric_id=rubric_id,
        mode=mode,
        provider=provider,
        case_set_id=case_set_id,
        allow_draft=allow_draft,
    )
    fingerprint = digest(canonical(specification))
    existing = db.scalar(
        select(Batch).where(Batch.course_id == course_id, Batch.idempotency_key == key)
    )
    if existing:
        require(
            existing.request_hash == fingerprint, "相同提交标识不能用于不同批次。", 409
        )
        return existing
    require(
        provider != "live" or (allow_live and acknowledged),
        "真实模型未启用，或尚未确认向模型服务发送所选答案。",
    )
    rubric = db.get(Rubric, rubric_id)
    require(rubric is not None, "规则不存在。", 404)
    q = get_question(db, user, rubric.question_pk, True)
    require(q.course_id == course_id, "规则不属于该课程。")
    require(
        rubric.data["status"] == "PUBLISHED" or allow_draft,
        "草案评分需要显式启用试验模式。",
    )
    if mode == "checklist":
        validate_checklist(rubric.data)
    require(provider != "synthetic" or q.synthetic, "合成回放只能用于系统内置合成题。")
    cases = []
    if case_set_id is not None:
        case_set = db.get(CaseSet, case_set_id)
        require(
            case_set is not None and case_set.rubric_id == rubric.id,
            "案例集合与规则版本不匹配。",
        )
        cases = copy.deepcopy(case_set.data)
    b = Batch(
        course_id=course_id,
        idempotency_key=key,
        request_hash=fingerprint,
        created_by=user.id,
        created_at=now(),
    )
    db.add(b)
    db.flush()
    for pk in answer_ids:
        a = db.get(Answer, pk)
        require(a is not None and a.question_pk == q.id, "答案与规则题目不匹配。")
        active = db.scalar(
            select(Task.id).where(
                Task.answer_pk == a.id,
                Task.status.in_(["PENDING", "RUNNING", "NEEDS_ATTENTION"]),
            )
        )
        require(
            active is None, "该答案存在进行中或结果不明的任务，请先处理原任务。", 409
        )
        request = dict(
            question=copy.deepcopy(q.data),
            rubric=copy.deepcopy(rubric.data),
            answer={
                "question_id": q.question_id,
                "answer_id": a.answer_id,
                "text": a.text,
            },
            mode=mode,
            cases=cases,
            allow_draft=allow_draft,
            max_attempts=2,
        )
        db.add(
            Task(
                batch_id=b.id,
                answer_pk=a.id,
                rubric_id=rubric.id,
                request=request,
                provider=provider,
                status="PENDING",
                created_at=now(),
            )
        )
    audit(db, user, "create_batch", b.id)
    return b


def retry_task(db, user, pk, acknowledged=False, allow_live=False):
    task, _, q = get_task(db, user, pk)
    require(
        not db.scalar(select(Task).where(Task.retry_of == pk)),
        "该任务已创建重试。",
        409,
    )
    require(
        task.status in ("FAILED", "NEEDS_ATTENTION"), "只有失败或中断任务可显式重试。"
    )
    require(
        task.provider != "live" or (allow_live and acknowledged),
        "重试可能再次计费，请显式确认。",
    )
    other = db.scalar(
        select(Task).where(
            Task.answer_pk == task.answer_pk,
            Task.id != task.id,
            Task.status.in_(["PENDING", "RUNNING", "NEEDS_ATTENTION"]),
        )
    )
    require(other is None, "该答案已有进行中的任务。", 409)
    require(
        db.scalar(select(Review).where(Review.task_id == pk)) is None,
        "已有复核记录，不能重试。",
        409,
    )
    task.status = "RETRIED"
    db.flush()
    new = Task(
        batch_id=task.batch_id,
        answer_pk=task.answer_pk,
        rubric_id=task.rubric_id,
        request=copy.deepcopy(task.request),
        provider=task.provider,
        status="PENDING",
        retry_of=pk,
        created_at=now(),
    )
    db.add(new)
    db.flush()
    audit(db, user, "retry_task", new.id)
    return new


def review_task(db, user, task_pk, items, release=False):
    task, answer, q = get_task(db, user, task_pk)
    require(
        task.status == "COMPLETED"
        and task.result
        and task.result["status"] != "FAILED",
        "失败或未完成任务不能确认。",
    )
    require(
        db.scalar(select(Review).where(Review.task_id == task.id)) is None,
        "该任务已复核。",
        409,
    )
    r = db.get(Rubric, task.rubric_id)
    require(
        q.current_rubric_id in (None, r.id), "规则已更新，请按新版本重评后确认。", 409
    )
    trial = (
        q.synthetic
        or r.data["status"] != "PUBLISHED"
        or answer.source.get("kind") == "legacy"
    )
    confirmed = dict(
        question_id=q.question_id,
        answer_id=answer.answer_id,
        rubric_version=r.version,
        teacher_confirmed=True,
        approved_for_reuse=False,
        review_id=str(uuid.uuid4()),
        reviewer_id=str(user.id),
        confirmed_at=now(),
        confirmed_items=items,
    )
    # Validation-only copy does not publish drafts or change the stored rule.
    confirmed_assessment(
        q.data, dict(r.data, status="PUBLISHED"), task.request["answer"], confirmed
    )
    total = sum(Decimal(str(i["score"])) for i in items)
    confirmed["confirmed_total"] = float(total)
    if trial:
        confirmed["teacher_confirmed"] = False
    require(not release or not trial, "试验和合成结果不能发布为正式成绩。")
    review = Review(
        task_id=task.id,
        reviewer_id=user.id,
        confirmed=confirmed,
        trial_only=trial,
        released=release,
        created_at=now(),
    )
    db.add(review)
    db.flush()
    audit(db, user, "trial_review" if trial else "teacher_confirm", review.id)
    return review


def approve_cases(db, user, rubric_id, review_ids, version):
    r = db.get(Rubric, rubric_id)
    require(r is not None, "规则不存在。", 404)
    q = get_question(db, user, r.question_pk, True)
    require(
        r.data["status"] == "PUBLISHED"
        and q.current_rubric_id == r.id
        and not q.synthetic,
        "请选择当前正式规则。",
    )
    require(
        review_ids and len(set(review_ids)) == len(review_ids), "请选择不同的复核记录。"
    )
    require(
        not db.scalar(
            select(CaseSet).where(CaseSet.rubric_id == r.id, CaseSet.version == version)
        ),
        "案例版本已存在。",
        409,
    )
    cases = []
    for review_pk in review_ids:
        review = db.get(Review, review_pk)
        require(review is not None and not review.trial_only, "试验记录不能批准复用。")
        task, _, _ = get_task(db, user, review.task_id)
        require(task.rubric_id == r.id, "复核记录的规则版本不匹配。")
        confirmed = dict(review.confirmed, approved_for_reuse=True)
        case = case_from_confirmed(
            q.data,
            r.data,
            task.request["answer"],
            confirmed,
            case_id="review-" + str(review.id),
            case_set_version=version,
        )
        case["reuse_approval"] = {"reviewer_id": str(user.id), "approved_at": now()}
        cases.append(case)
    case_set = CaseSet(
        rubric_id=r.id,
        version=version,
        data=cases,
        approved_by=user.id,
        created_at=now(),
    )
    db.add(case_set)
    db.flush()
    audit(db, user, "approve_case_reuse", case_set.id)
    return case_set


def create_correction(db, user, task_id, assessment, scope, proposed_rule):
    task, _, q = get_task(db, user, task_id)
    draft = correction_draft(
        task.request["question"],
        task.request["rubric"],
        task.request["answer"],
        assessment,
        scope=scope,
        proposed_rule=proposed_rule,
        allow_draft=True,
    )
    d = Draft(
        question_pk=q.id,
        data=draft,
        created_by=user.id,
        source={"kind": "teacher-correction", "task_id": task.id},
    )
    db.add(d)
    db.flush()
    audit(db, user, "correction_draft", d.id)
    return d


def feedback(db, user, review):
    task, answer, q = get_task(db, user, review.task_id, user.role != "student")
    require(not review.trial_only, "试验记录不生成正式学习反馈。")
    require(
        user.role != "student"
        or (review.released and q.current_rubric_id == task.rubric_id),
        "该成绩尚未发布或需要重审。",
        403,
    )
    practices = list(
        db.scalars(
            select(Practice).where(
                Practice.course_id == q.course_id, Practice.approved_by.is_not(None)
            )
        )
    )
    catalog = [
        dict(p.data, teacher_approved=True)
        for p in practices
        if p.data.get("rubric_version") == task.request["rubric"]["version"]
    ]
    student_id = answer.student_id
    attempted = []
    if student_id:
        attempted = list(
            db.scalars(
                select(Practice.practice_id)
                .join(PracticeAttempt, PracticeAttempt.practice_pk == Practice.id)
                .where(PracticeAttempt.student_id == student_id)
            )
        )
    result = feedback_and_practice(
        q.data,
        task.request["rubric"],
        task.request["answer"],
        review.confirmed,
        catalog,
        attempted,
    )
    practice_ids = {p.practice_id: p.id for p in practices}
    result["practice_candidates"] = [
        dict(p, record_id=practice_ids[p["practice_id"]])
        for p in result["practice_candidates"]
    ]
    return result


def csv_export(db, user, course_id, trial=False):
    course_access(db, user, course_id, True)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "用途",
            "题号",
            "答案编号",
            "规则版本",
            "确认分",
            "满分",
            "复核人编号",
            "确认时间UTC",
        ]
    )
    reviews = db.scalars(
        select(Review)
        .join(Task, Task.id == Review.task_id)
        .join(Answer, Answer.id == Task.answer_pk)
        .join(Question, Question.id == Answer.question_pk)
        .where(Question.course_id == course_id)
        .order_by(Review.id)
    )
    latest = {}
    for review in reviews:
        task = db.get(Task, review.task_id)
        latest[task.answer_pk] = review
    for review in latest.values():
        task = db.get(Task, review.task_id)
        answer = db.get(Answer, task.answer_pk)
        q = db.get(Question, answer.question_pk)
        stale = (
            q.current_rubric_id is not None and q.current_rubric_id != task.rubric_id
        )
        if not trial and (
            review.trial_only
            or stale
            or task.status != "COMPLETED"
            or not review.confirmed["teacher_confirmed"]
        ):
            continue
        row = [
            "试验记录" if trial else "教师确认成绩",
            q.question_id,
            answer.answer_id,
            review.confirmed["rubric_version"],
            review.confirmed["confirmed_total"],
            q.data["max_score"],
            review.reviewer_id,
            review.created_at,
        ]
        # Protect spreadsheet formula interpretation for user-authored identifiers.
        writer.writerow(
            [
                (
                    "'" + x
                    if isinstance(x, str)
                    and x.lstrip().startswith(("=", "+", "-", "@"))
                    else x
                )
                for x in row
            ]
        )
    return output.getvalue().encode("utf-8-sig")

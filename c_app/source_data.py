"""Pinned public A delivery; data stays under ignored work/, with its license."""

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from urllib.request import urlopen
from sqlalchemy import select
from .db import Draft, Question, ROOT
from .security import audit, require
from .services import add_question, add_rubric, import_answers

A_COMMIT = "84357c86ca1cd16d93722e176349d6e2b5316a72"
LABEL_VERSION = "2026-11-16-user-approved-v1"
WORK = ROOT / "c-prototype/work"


def fetch_snapshot():
    target = WORK / "a-source" / A_COMMIT
    if target.is_dir() and (target / "manifest.json").is_file():
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        for name, digest in manifest["files"].items():
            require(
                hashlib.sha256((target / name).read_bytes()).hexdigest() == digest,
                "A 本地快照已变化，请保留原件后另行准备。",
            )
        return target
    raw = urlopen(
        f"https://codeload.github.com/hhkx2333/2026-ict-1/zip/{A_COMMIT}", timeout=60
    ).read(20_000_001)
    require(len(raw) <= 20_000_000, "A 快照超过预期大小。")
    z = zipfile.ZipFile(io.BytesIO(raw))
    files = {}
    target.mkdir(parents=True, exist_ok=True)
    for entry in z.infolist():
        rel = Path(*Path(entry.filename).parts[1:])
        if (
            entry.is_dir()
            or not rel.parts
            or rel.parts[0] not in ("题目答案相关数据", "A全面预处理_2026-11-16")
        ):
            continue
        require(
            ".." not in rel.parts
            and not rel.is_absolute()
            and entry.file_size < 4_000_000,
            "无效的快照路径。",
        )
        content = z.read(entry)
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        files[rel.as_posix()] = hashlib.sha256(content).hexdigest()
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "commit": A_COMMIT,
                "archive_sha256": hashlib.sha256(raw).hexdigest(),
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def seed_a(db, user, course_id, source_root):
    source_root = Path(source_root)
    versions = json.loads(
        (source_root / "A全面预处理_2026-11-16/数据/题目与规则版本对应.json").read_text(
            encoding="utf-8-sig"
        )
    )
    manifest = json.loads((source_root / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        require(
            hashlib.sha256((source_root / name).read_bytes()).hexdigest() == expected,
            "A 快照校验失败。",
        )
    require(
        manifest["commit"] == A_COMMIT and len(versions) == 15,
        "不是固定版本的 15 题交付。",
    )
    question_map, count = {}, 0
    for version in versions:
        qid = version["question_id"]
        document = source_root / f"题目答案相关数据/{qid}/01_题目与参考答案.md"
        machine = source_root / f"A全面预处理_2026-11-16/规则/{qid}_暂行机器规则.json"
        require(
            hashlib.sha256(document.read_bytes()).hexdigest()
            == version["question_document_sha256"],
            "题目文件摘要不匹配。",
        )
        require(
            hashlib.sha256(machine.read_bytes()).hexdigest()
            == version["machine_rubric_sha256"],
            "规则文件摘要不匹配。",
        )
        rubric = json.loads(machine.read_text(encoding="utf-8-sig"))
        require(
            rubric["status"] == "DRAFT"
            and rubric["version"] == version["rubric_version"],
            "规则状态或版本不匹配。",
        )
        md = document.read_text(encoding="utf-8-sig")
        match = re.search(r"## 题目\s*\n(.*?)(?=\n## )", md, re.S)
        require(match is not None, "题目正文段落缺失。")
        reference = re.search(r"## reference1\s*\n(.*?)(?=\n## )", md, re.S)
        data = {
            "question_id": qid,
            "text": match[1].strip(),
            "max_score": rubric["max_score"],
            "reference_answer": reference[1].strip() if reference else "",
        }
        provenance = {
            "kind": "A-user-approved-development",
            "commit": A_COMMIT,
            "label_set_version": LABEL_VERSION,
            "teacher_approved": False,
            "license": "题目答案相关数据/LICENSE",
            "question_sha256": version["question_document_sha256"],
        }
        q = db.scalar(
            select(Question).where(
                Question.course_id == course_id, Question.question_id == qid
            )
        )
        if q:
            require(
                q.source.get("commit") == A_COMMIT and q.data == data,
                "同题号已有不同来源，取消导入。",
                409,
            )
        else:
            q = add_question(db, user, course_id, data, provenance)
            add_rubric(db, user, q.id, rubric)
        question_map[qid] = q.id
        answers = (
            source_root / f"题目答案相关数据/{qid}/04_全部答案_无得分.jsonl"
        ).read_text(encoding="utf-8-sig")
        count += import_answers(db, user, q.id, answers, provenance)["added"]
    candidates = json.loads(
        (
            source_root
            / "A全面预处理_2026-11-16/数据/已填好的校准案例候选_未教师批准.json"
        ).read_text(encoding="utf-8-sig")
    )
    existing = {
        d.data.get("case_id")
        for d in db.scalars(
            select(Draft).where(Draft.question_pk.in_(question_map.values()))
        )
    }
    added = 0
    for c in candidates:
        require(
            c.get("teacher_approved") is False and c.get("approved_for_reuse") is False,
            "候选审核状态异常。",
        )
        if c["case_id"] not in existing:
            db.add(
                Draft(
                    question_pk=question_map[c["question_id"]],
                    data=c,
                    created_by=user.id,
                    source={"kind": "A-calibration-candidate", "commit": A_COMMIT},
                )
            )
            added += 1
    audit(db, user, "import_a_snapshot", A_COMMIT)
    return {
        "questions": len(question_map),
        "answers_added": count,
        "candidates_added": added,
        "teacher_approvals_created": 0,
    }

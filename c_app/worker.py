"""Durable single-worker execution with fenced claims and explicit uncertain recovery."""

import argparse
import logging
import os
import secrets
import time
from sqlalchemy import select, update
from backend.grading import ChatCompletionsProvider, FixtureProvider, grade_submission
from backend.grading.core import ValidationError
from .db import ROOT, Task, WorkerState, make_engine, sessions
import json

log = logging.getLogger(__name__)


def recover(factory, tick=None):
    with factory.begin() as db:
        return db.execute(
            update(Task)
            .where(Task.status == "RUNNING", Task.lease_until < (tick or time.time()))
            .values(
                status="NEEDS_ATTENTION",
                error="INTERRUPTED_CALL_OUTCOME_UNKNOWN",
                claim_token=None,
                lease_until=None,
            )
        ).rowcount


def provider_for(task):
    if task.provider == "synthetic":
        if task.request["question"]["question_id"] != "Q01":
            raise ValueError("synthetic question mismatch")
        if task.request["answer"]["answer_id"] == "SYNTHETIC-FAIL":

            class Failed:
                model = "synthetic-failure"

                def complete(self, system, payload):
                    raise RuntimeError("synthetic failure")

            return Failed()
        fixture = json.loads(
            (ROOT / "examples/synthetic.json").read_text(encoding="utf-8")
        )
        return FixtureProvider(fixture["model_output"])
    if os.environ.get("C_ALLOW_LIVE") != "true":
        raise ValueError("live grading disabled")
    # Empty env_file disables B's implicit loading of an unrelated .env file.
    return ChatCompletionsProvider.from_env(env_file=ROOT / "work/no-implicit-env")


def run_once(factory, provider_factory=None):
    with factory.begin() as db:
        if db.bind.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        statement = insert(WorkerState).values(key="default", heartbeat=time.time())
        db.execute(
            statement.on_conflict_do_update(
                index_elements=[WorkerState.key], set_={"heartbeat": time.time()}
            )
        )
    recover(factory)
    token = secrets.token_hex(24)
    with factory.begin() as db:
        pk = db.scalar(
            select(Task.id).where(Task.status == "PENDING").order_by(Task.id).limit(1)
        )
        if pk is None:
            return False
        claimed = db.execute(
            update(Task)
            .where(Task.id == pk, Task.status == "PENDING")
            .values(status="RUNNING", claim_token=token, lease_until=time.time() + 300)
        ).rowcount
        if not claimed:
            return True
    with factory() as db:
        task = db.get(Task, pk)
        request = task.request
        try:
            result = grade_submission(
                request, provider=(provider_factory or provider_for)(task)
            )
            state = "FAILED" if result["status"] == "FAILED" else "COMPLETED"
            error = "MODEL_CALL_FAILED" if state == "FAILED" else None
        except ValidationError:
            result, state, error = None, "FAILED", "INVALID_GRADING_CONFIGURATION"
        except Exception:
            # Raw exceptions can contain credentials, network URLs or student text.
            result, state, error = (
                None,
                "FAILED",
                "PROVIDER_CONFIGURATION_OR_EXECUTION_FAILED",
            )
    with factory.begin() as db:
        db.execute(
            update(Task)
            .where(Task.id == pk, Task.status == "RUNNING", Task.claim_token == token)
            .values(
                result=result,
                status=state,
                error=error,
                claim_token=None,
                lease_until=None,
            )
        )
    log.info("task_id=%s status=%s", pk, state)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    factory = sessions(make_engine())
    if args.once:
        run_once(factory)
    else:
        while True:
            if not run_once(factory):
                time.sleep(1)


if __name__ == "__main__":
    main()

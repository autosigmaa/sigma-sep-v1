import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import APIKeyHeader
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from sigma.graph import build_graph, digest
from sigma.state import Approval, Assets, JobId, NewJob, Package, Result


def create_app(db_path=None, token=None):
    database = Path(db_path or os.environ.get("SIGMA_DB_PATH", "data/checkpoints.sqlite"))
    api_token = token or os.environ.get("SIGMA_API_TOKEN", "")
    # ponytail: one process, serialized access; use Postgres and per-job locking for multiple workers.
    lock = RLock()
    header = APIKeyHeader(name="X-Sigma-Key", auto_error=False)

    def authorize(value: str | None = Depends(header)):
        if not value or not secrets.compare_digest(value.encode(), api_token.encode()):
            raise HTTPException(401, "Invalid API token")

    @asynccontextmanager
    async def lifespan(app):
        if len(api_token) < 24 or api_token == "replace-with-a-long-random-token":
            raise RuntimeError("Set SIGMA_API_TOKEN to a random token of at least 24 characters")
        database.parent.mkdir(parents=True, exist_ok=True)
        # Prevent a second server process from bypassing the in-process lock.
        import fcntl
        with database.with_suffix(".lock").open("a") as process_lock:
            fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            conn = sqlite3.connect(database, check_same_thread=False)
            try:
                conn.execute("PRAGMA synchronous=FULL")
                saver = SqliteSaver(conn)
                saver.setup()
                app.state.conn = conn
                app.state.graph = build_graph(saver)
                yield
            finally:
                conn.close()

    app = FastAPI(title="SIGMA Kernel", lifespan=lifespan)

    def config(job_id):
        return {"configurable": {"thread_id": job_id}}

    def snapshot(job_id):
        snap = app.state.graph.get_state(config(job_id))
        if not snap.values:
            raise HTTPException(404, "Job not found")
        if snap.values["schema_version"] != 1:
            raise HTTPException(409, "Unsupported checkpoint schema")
        return snap

    def view(job_id):
        snap = snapshot(job_id)
        pending = [i.value for task in snap.tasks for i in task.interrupts]
        state = snap.values
        return {
            "job": state["job"],
            "job_id": job_id, "status": state["status"] if pending or not snap.next else "recoverable",
            "current_step": state["step"], "artifacts": state["artifacts"],
            "approval": state["approval"], "error": state["error"],
            "next_action": pending[0] if pending else None,
        }

    def invoke(job_id, value):
        app.state.graph.invoke(value, config(job_id), durability="sync")
        return view(job_id)

    def accept(job_id, payload, expected):
        state = snapshot(job_id).values
        old = state["receipts"].get(payload["action_id"])
        if old:
            if old != digest(payload):
                raise HTTPException(409, "Action already completed with a different result")
            return view(job_id)
        pending = view(job_id)["next_action"]
        if not pending or pending["action_id"] != payload["action_id"] or pending["type"] not in expected:
            raise HTTPException(409, "Result does not match the pending action")
        return invoke(job_id, Command(resume=payload))

    @app.get("/health")
    def health():
        with lock:
            app.state.conn.execute("SELECT name FROM sqlite_master WHERE name = 'checkpoints'").fetchone()
        return {"status": "ok"}

    @app.post("/jobs", dependencies=[Depends(authorize)])
    def start(job: NewJob):
        with lock:
            snap = app.state.graph.get_state(config(job.job_id))
            data = job.model_dump(mode="json")
            if snap.values:
                if snap.values["job"] != data:
                    raise HTTPException(409, "Job ID already used with different input")
                return {**view(job.job_id), "created": False}
            state = {"schema_version": 1, "job": data, "step": "prepare", "status": "running",
                     "artifacts": {}, "approval": None, "attempts": {}, "error": None, "receipts": {}}
            return {**invoke(job.job_id, state), "created": True}

    @app.get("/jobs/{job_id}", dependencies=[Depends(authorize)])
    def status(job_id: JobId):
        with lock:
            return view(job_id)

    @app.post("/jobs/{job_id}/results", dependencies=[Depends(authorize)])
    def results(job_id: JobId, result: Result):
        with lock:
            state = snapshot(job_id).values
            # Validate even stale success data before passing it into the graph.
            if result.outcome == "success":
                if isinstance(result.data, Package):
                    expected = {"create_package"}
                elif isinstance(result.data, Assets):
                    expected = {"generate_assets"}
                count = state["job"]["input"]["slide_count"]
                if sorted(s.number for s in result.data.slides) != list(range(1, count + 1)):
                    raise HTTPException(422, "Slide numbers must exactly match the requested slide count")
            else:
                expected = {"create_package", "generate_assets"}
            return accept(job_id, result.model_dump(mode="json"), expected)

    @app.post("/jobs/{job_id}/approval", dependencies=[Depends(authorize)])
    def decide(job_id: JobId, decision: Approval):
        with lock:
            return accept(job_id, decision.model_dump(mode="json"), {"approval"})

    @app.post("/jobs/{job_id}/resume", dependencies=[Depends(authorize)])
    def resume(job_id: JobId):
        with lock:
            snap = snapshot(job_id)
            if not snap.next or any(task.interrupts for task in snap.tasks):
                return view(job_id)
            return invoke(job_id, None)

    return app

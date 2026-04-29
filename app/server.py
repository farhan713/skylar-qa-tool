"""
Flask web UI for the Skylar IQ QA tool.

Run:
    python -m app.server
    # or:
    flask --app app.server run -p 5050

Endpoints:
    GET  /                  Form: login URL + creds + .xlsx upload + Start
    POST /jobs              Multipart submit; spawns runner in a thread; returns job_id
    GET  /jobs/<id>         Job page (live progress + final report when done)
    GET  /jobs/<id>/events  Server-Sent Events stream of progress lines
    GET  /jobs/<id>/status  JSON: status + counts
    GET  /jobs/<id>/report  Serves the generated REPORT.html
    GET  /jobs/<id>/file/<rel>  Serves files inside the job dir (screenshots, json)
    GET  /jobs               JSON list of past job runs
"""
from __future__ import annotations

import json
import queue
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import (
    Flask, render_template, request, jsonify, redirect, url_for,
    send_from_directory, Response, stream_with_context, abort,
)

from app.runner import RunConfig, run as run_qa
from app.report import generate as generate_report
from app.excel_reader import read_questions


ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"
RUNS_DIR.mkdir(exist_ok=True)

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25MB upload cap

# Per-job in-memory event queue (also persisted to <job>/events.log)
_event_queues: dict[str, queue.Queue[str]] = {}
_event_locks: dict[str, threading.Lock] = {}


# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job_dir(job_id: str) -> Path:
    return RUNS_DIR / job_id


def _job_meta(job_id: str) -> dict[str, Any] | None:
    p = _job_dir(job_id) / "job.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def _save_job_meta(job_id: str, **patch: Any) -> dict[str, Any]:
    meta = _job_meta(job_id) or {}
    meta.update(patch)
    (_job_dir(job_id) / "job.json").write_text(json.dumps(meta, indent=2, default=str))
    return meta


def _emit(job_id: str, line: str) -> None:
    """Write a progress line to the job's queue + events.log."""
    q = _event_queues.get(job_id)
    if q is not None:
        try:
            q.put_nowait(line)
        except queue.Full:
            pass
    # Persist for late subscribers
    log = _job_dir(job_id) / "events.log"
    try:
        with log.open("a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------
def _run_job(job_id: str, cfg: RunConfig) -> None:
    try:
        _save_job_meta(job_id, status="running", started_at=_now())
        cfg.on_event = lambda line: _emit(job_id, line)
        summary = run_qa(cfg)
        # Always generate the report at the end (even if some queries failed)
        try:
            outputs = generate_report(cfg.output_dir)
            summary["report_outputs"] = outputs
        except Exception as e:
            _emit(job_id, f"[report] generation failed: {e}")
        _save_job_meta(
            job_id,
            status="done",
            finished_at=_now(),
            summary=summary,
        )
        _emit(job_id, f"[done] {summary}")
    except Exception as e:
        tb = traceback.format_exc()
        _emit(job_id, f"[fatal] {type(e).__name__}: {e}")
        _emit(job_id, tb)
        _save_job_meta(
            job_id,
            status="failed",
            finished_at=_now(),
            error=str(e),
            traceback=tb,
        )
    finally:
        # Sentinel so any SSE listener can disconnect
        _emit(job_id, "[__end__]")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    jobs = []
    for d in sorted(RUNS_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        meta = _job_meta(d.name)
        if meta:
            jobs.append({"id": d.name, **meta})
    return render_template("index.html", jobs=jobs[:25])


@app.route("/jobs", methods=["POST"])
def jobs_create():
    login_url = (request.form.get("login_url") or "").strip()
    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "")
    machine_id = (request.form.get("machine_id") or "100").strip()
    sql_agent_path = (request.form.get("sql_agent_path") or
                      "/backoffice/mv-assets/index-modern.html#/listScreen/sqlagent").strip()
    run_sql_to = int(request.form.get("run_sql_timeout_ms") or 120_000)
    gen_viz_to = int(request.form.get("gen_viz_timeout_ms") or 120_000)

    f = request.files.get("questions")
    if not (login_url and username and password and f):
        return ("Missing required fields (login_url, username, password, questions)", 400)

    job_id = uuid.uuid4().hex[:8] + "-" + datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    job = _job_dir(job_id)
    job.mkdir(parents=True, exist_ok=True)

    # Save the uploaded .xlsx alongside the job
    xlsx_path = job / "questions.xlsx"
    f.save(str(xlsx_path))

    try:
        questions = read_questions(xlsx_path)
    except Exception as e:
        return (f"Failed to read questions file: {e}", 400)

    _save_job_meta(
        job_id,
        status="queued",
        created_at=_now(),
        login_url=login_url,
        username=username,
        machine_id=machine_id,
        sql_agent_path=sql_agent_path,
        question_count=len(questions),
        questions_file=str(xlsx_path),
    )

    cfg = RunConfig(
        login_url=login_url,
        username=username,
        password=password,
        questions=questions,
        output_dir=job,
        machine_id=machine_id,
        sql_agent_path=sql_agent_path,
        run_sql_timeout_ms=run_sql_to,
        gen_viz_timeout_ms=gen_viz_to,
        headless=True,
    )

    _event_queues[job_id] = queue.Queue(maxsize=10_000)
    _event_locks[job_id] = threading.Lock()
    th = threading.Thread(target=_run_job, args=(job_id, cfg), daemon=True)
    th.start()

    return redirect(url_for("job_view", job_id=job_id))


@app.route("/jobs/<job_id>")
def job_view(job_id: str):
    meta = _job_meta(job_id)
    if not meta:
        abort(404)
    return render_template("job.html", job_id=job_id, meta=meta)


@app.route("/jobs/<job_id>/status")
def job_status(job_id: str):
    meta = _job_meta(job_id)
    if not meta:
        abort(404)
    # Counts from per-query result files
    res_dir = _job_dir(job_id) / "results"
    counts = {"PASS": 0, "PARTIAL": 0, "FAIL": 0, "TIMEOUT": 0, "PENDING": 0}
    last5 = []
    if res_dir.exists():
        files = sorted(res_dir.glob("q*.json"))
        for f in files:
            try:
                d = json.loads(f.read_text())
                s = d.get("overall_status", "PENDING")
                counts[s] = counts.get(s, 0) + 1
            except Exception:
                pass
        for f in files[-5:]:
            try:
                d = json.loads(f.read_text())
                last5.append({
                    "id": d.get("id"),
                    "nl_query": d.get("nl_query", "")[:80],
                    "status": d.get("overall_status"),
                    "duration_ms": d.get("total_duration_ms"),
                })
            except Exception:
                pass
    return jsonify({
        "job_id": job_id,
        "status": meta.get("status"),
        "completed": sum(counts.values()) - counts["PENDING"],
        "total": meta.get("question_count"),
        "counts": counts,
        "last5": last5,
    })


@app.route("/jobs/<job_id>/events")
def job_events(job_id: str):
    """SSE stream of progress lines."""
    if not _job_dir(job_id).exists():
        abort(404)

    @stream_with_context
    def gen():
        # First, replay anything that was logged before the client connected
        log = _job_dir(job_id) / "events.log"
        if log.exists():
            for line in log.read_text().splitlines():
                yield f"data: {line}\n\n"
        # Then live-stream from the in-memory queue
        q = _event_queues.get(job_id)
        if q is None:
            return
        while True:
            try:
                line = q.get(timeout=20)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            yield f"data: {line}\n\n"
            if line == "[__end__]":
                return

    return Response(gen(), mimetype="text/event-stream")


@app.route("/jobs/<job_id>/report")
def job_report(job_id: str):
    """Serve the generated REPORT.html (regenerate first if missing)."""
    job = _job_dir(job_id)
    if not job.exists():
        abort(404)
    report_path = job / "REPORT.html"
    if not report_path.exists():
        try:
            generate_report(job)
        except SystemExit:
            return ("No results yet", 404)
    return send_from_directory(job, "REPORT.html")


@app.route("/jobs/<job_id>/file/<path:rel>")
def job_file(job_id: str, rel: str):
    job = _job_dir(job_id)
    if not job.exists():
        abort(404)
    full = (job / rel).resolve()
    if not str(full).startswith(str(job.resolve())):
        abort(403)
    if not full.exists():
        abort(404)
    return send_from_directory(job, rel)


@app.route("/jobs.json")
def jobs_list():
    out = []
    for d in sorted(RUNS_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        meta = _job_meta(d.name) or {"id": d.name}
        out.append({"id": d.name, **meta})
    return jsonify(out)


# ---------------------------------------------------------------------------
def main() -> None:
    import argparse, webbrowser
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5050)
    ap.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    args = ap.parse_args()
    url = f"http://{args.host}:{args.port}/"
    print(f"Skylar IQ QA Tool — {url}")
    if not args.no_browser:
        try:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()

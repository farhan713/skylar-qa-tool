"""
Generate REPORT.html / REPORT.md / SUMMARY.json for a single job folder.

A "job folder" is whatever was passed as RunConfig.output_dir to runner.run().
Layout produced by the runner:
    <job_dir>/
        results/qNN.json        ← per-query records
        network_logs/qNN_calls.json
        screenshots/qNN_*.png
        all_results.json        ← aggregate (written when run completes)

This module reads either the aggregate or the per-query JSONs (whichever exists)
and writes:
    <job_dir>/REPORT.html
    <job_dir>/REPORT.md
    <job_dir>/SUMMARY.json
"""
from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_results(job_dir: Path) -> list[dict[str, Any]]:
    agg = job_dir / "all_results.json"
    if agg.exists():
        return json.loads(agg.read_text())
    res = job_dir / "results"
    if res.exists():
        files = sorted(res.glob("q*.json"))
        if files:
            out = []
            for f in files:
                try:
                    out.append(json.loads(f.read_text()))
                except Exception as e:
                    print(f"warn: skipping {f}: {e}")
            return out
    raise SystemExit(f"No results in {job_dir}")


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    status_counts = Counter(r.get("overall_status", "UNKNOWN") for r in results)

    rs_present = sum(1 for r in results if r.get("run_sql_call"))
    rs_ok = sum(1 for r in results if r.get("validations", {}).get("run_sql_status_ok"))
    rs_rows = sum(1 for r in results if r.get("validations", {}).get("run_sql_has_rows"))

    gv_present = sum(1 for r in results if r.get("generate_viz_call"))
    gv_ok = sum(1 for r in results if r.get("validations", {}).get("generate_viz_status_ok"))
    gv_chart = sum(1 for r in results if r.get("validations", {}).get("generate_viz_has_chart"))

    cs = sum(1 for r in results if r.get("validations", {}).get("columns_synced_run_sql_vs_generate_viz") is True)
    cu = sum(1 for r in results if r.get("validations", {}).get("columns_synced_run_sql_vs_generate_viz") is False)

    durations = [r["total_duration_ms"] for r in results if r.get("total_duration_ms")]
    avg = int(sum(durations) / len(durations)) if durations else 0
    mx = max(durations) if durations else 0

    failing = [
        {"id": r["id"], "nl_query": r["nl_query"], "fail_reasons": r.get("validations", {}).get("fail_reasons", [])}
        for r in results if r.get("overall_status") == "FAIL"
    ]
    partial = [
        {"id": r["id"], "nl_query": r["nl_query"], "warn_reasons": r.get("validations", {}).get("warn_reasons", [])}
        for r in results if r.get("overall_status") == "PARTIAL"
    ]
    timeouts = [
        {
            "id": r["id"],
            "nl_query": r["nl_query"],
            "timed_out_on": r.get("validations", {}).get("timed_out_on"),
            "duration_ms": r.get("total_duration_ms"),
        }
        for r in results if r.get("overall_status") == "TIMEOUT"
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_queries": total,
        "status_breakdown": dict(status_counts),
        "run_sql": {"present": rs_present, "http_ok": rs_ok, "with_rows": rs_rows},
        "generate_viz": {"present": gv_present, "http_ok": gv_ok, "with_chart": gv_chart},
        "column_sync": {"synced": cs, "unsynced": cu},
        "performance_ms": {"avg": avg, "max": mx},
        "failing_queries": failing,
        "partial_queries": partial,
        "timeout_queries": timeouts,
    }


# ---------------------------------------------------------------------------
def render_md(results: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    sb = summary["status_breakdown"]
    lines: list[str] = []
    lines.append("# Skylar IQ SQL Agent — Automation QA Report")
    lines.append(f"_Generated: {summary['generated_at']}_")
    lines.append("")
    lines.append("## 1. Executive Summary")
    lines.append(f"- **Total:** {summary['total_queries']}")
    lines.append(
        f"- **PASS:** {sb.get('PASS',0)}  |  **PARTIAL:** {sb.get('PARTIAL',0)}  |  "
        f"**FAIL:** {sb.get('FAIL',0)}  |  **TIMEOUT:** {sb.get('TIMEOUT',0)}"
    )
    lines.append(f"- **Avg duration:** {summary['performance_ms']['avg']} ms")
    lines.append(f"- **Max duration:** {summary['performance_ms']['max']} ms")
    lines.append("")
    lines.append("### API health")
    lines.append("| Endpoint | Captured | HTTP 2xx | Usable payload |")
    lines.append("|---|---|---|---|")
    t = summary["total_queries"]
    rs = summary["run_sql"]
    gv = summary["generate_viz"]
    lines.append(f"| `run-sql` | {rs['present']}/{t} | {rs['http_ok']}/{t} | {rs['with_rows']}/{t} (rows>0) |")
    lines.append(f"| `generate-viz` | {gv['present']}/{t} | {gv['http_ok']}/{t} | {gv['with_chart']}/{t} (chart) |")
    lines.append("")
    cs = summary["column_sync"]
    lines.append(f"### Column-name sync\n- Synced: **{cs['synced']}**, Unsynced: **{cs['unsynced']}**")
    lines.append("")

    if summary["failing_queries"]:
        lines.append("## 2. Failing queries")
        for f in summary["failing_queries"]:
            lines.append(f"- **q{f['id']:02d}** — {f['nl_query']}")
            for r in f["fail_reasons"]:
                lines.append(f"  - {r}")
        lines.append("")
    if summary["partial_queries"]:
        lines.append("## 3. Partial / warnings")
        for w in summary["partial_queries"]:
            lines.append(f"- **q{w['id']:02d}** — {w['nl_query']}")
            for r in w["warn_reasons"]:
                lines.append(f"  - {r}")
        lines.append("")
    if summary["timeout_queries"]:
        lines.append("## 3a. Timed-out queries")
        for t in summary["timeout_queries"]:
            lines.append(f"- **q{t['id']:02d}** — {t['nl_query']}  (on `{t['timed_out_on']}`, {t.get('duration_ms','?')} ms)")
        lines.append("")

    lines.append("## 4. Per-query detail")
    for r in results:
        lines.append(f"### q{r['id']:02d} — {r['nl_query']}")
        lines.append(f"- Status: `{r['overall_status']}` — Duration: {r.get('total_duration_ms')} ms")
        v = r.get("validations", {}) or {}
        rs_call = r.get("run_sql_call") or {}
        gv_call = r.get("generate_viz_call") or {}
        lines.append(f"- run-sql: `{rs_call.get('url','-')}` HTTP {rs_call.get('status','n/a')}, columns={v.get('run_sql_columns')}, rows={v.get('run_sql_row_count')}")
        lines.append(f"- generate-viz: `{gv_call.get('url','-')}` HTTP {gv_call.get('status','n/a')}, chart_type={v.get('generate_viz_chart_type')}, axes=({v.get('generate_viz_x_axis')}, {v.get('generate_viz_y_axis')})")
        lines.append(f"- columns_synced: {v.get('columns_synced_run_sql_vs_generate_viz')}")
        if v.get("fail_reasons"):
            lines.append(f"- fail: {v['fail_reasons']}")
        if v.get("warn_reasons"):
            lines.append(f"- warn: {v['warn_reasons']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
HTML_TMPL = """<!doctype html>
<html><head>
<meta charset="utf-8"><title>Skylar IQ QA Report</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:1200px;margin:24px auto;padding:0 20px;color:#222}}
 h1,h2,h3{{color:#1a3a6e}}
 table{{border-collapse:collapse;width:100%;margin:12px 0}}
 th,td{{border:1px solid #ddd;padding:6px 10px;text-align:left;font-size:13px}}
 th{{background:#f4f6fa}}
 details{{margin:6px 0}}
 pre{{background:#0f172a;color:#e2e8f0;padding:10px;border-radius:6px;overflow:auto;font-size:11px;max-height:320px}}
 .badge{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}}
 .badge.PASS{{background:#1a8a3a;color:#fff}}
 .badge.PARTIAL{{background:#d49a00;color:#fff}}
 .badge.FAIL{{background:#c62828;color:#fff}}
 .badge.TIMEOUT{{background:#6a4ec2;color:#fff}}
 .meta{{color:#555;font-size:12px}}
 img.screenshot{{max-width:100%;border:1px solid #ddd;margin:6px 0}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px}}
 .card{{background:#f8fafc;border:1px solid #e5e7eb;border-radius:6px;padding:10px}}
 .num{{font-size:26px;font-weight:700;color:#1a3a6e}}
</style></head>
<body>
<h1>Skylar IQ SQL Agent — Automation QA Report</h1>
<p class="meta">Generated: {generated_at}</p>

<h2>1. Executive Summary</h2>
<div class="grid">
  <div class="card"><div class="num">{total}</div>Total queries</div>
  <div class="card"><div class="num">{pass_n}</div>PASS</div>
  <div class="card"><div class="num">{partial_n}</div>PARTIAL</div>
  <div class="card"><div class="num">{fail_n}</div>FAIL</div>
  <div class="card"><div class="num">{timeout_n}</div>TIMEOUT</div>
  <div class="card"><div class="num">{avg_ms} ms</div>Avg</div>
  <div class="card"><div class="num">{max_ms} ms</div>Max</div>
</div>

<h3>API health</h3>
<table>
<tr><th>Endpoint</th><th>Captured</th><th>HTTP 2xx</th><th>Usable payload</th></tr>
<tr><td><code>run-sql</code></td><td>{rs_present}/{total}</td><td>{rs_ok}/{total}</td><td>{rs_rows}/{total} (rows&gt;0)</td></tr>
<tr><td><code>generate-viz</code></td><td>{gv_present}/{total}</td><td>{gv_ok}/{total}</td><td>{gv_chart}/{total} (chart)</td></tr>
</table>
<h3>Column-name sync</h3>
<p>Synced: <b>{cols_synced}</b> &nbsp;|&nbsp; Unsynced: <b>{cols_unsynced}</b></p>

<h2>2. Failing queries ({fail_n})</h2>{fail_list}
<h2>3. Partial / warnings ({partial_n})</h2>{partial_list}
<h2>3a. Timed-out queries ({timeout_n})</h2>{timeout_list}

<h2>4. Per-query detail</h2>
{per_query}
</body></html>
"""


def render_html(results: list[dict[str, Any]], summary: dict[str, Any], job_dir: Path) -> str:
    sb = summary["status_breakdown"]

    fail_list = "<ul>" + "".join(
        f"<li><b>q{f['id']:02d}</b> — {html.escape(f['nl_query'])}<ul>"
        + "".join(f"<li>{html.escape(r)}</li>" for r in f["fail_reasons"])
        + "</ul></li>"
        for f in summary["failing_queries"]
    ) + "</ul>" if summary["failing_queries"] else "<p><i>None.</i></p>"

    partial_list = "<ul>" + "".join(
        f"<li><b>q{w['id']:02d}</b> — {html.escape(w['nl_query'])}<ul>"
        + "".join(f"<li>{html.escape(r)}</li>" for r in w["warn_reasons"])
        + "</ul></li>"
        for w in summary["partial_queries"]
    ) + "</ul>" if summary["partial_queries"] else "<p><i>None.</i></p>"

    timeout_list = "<ul>" + "".join(
        f"<li><b>q{t['id']:02d}</b> — {html.escape(t['nl_query'])} <span class=\"meta\">(on <code>{html.escape(str(t['timed_out_on']))}</code>, {t.get('duration_ms','?')} ms)</span></li>"
        for t in summary["timeout_queries"]
    ) + "</ul>" if summary["timeout_queries"] else "<p><i>None.</i></p>"

    parts: list[str] = []
    for r in results:
        v = r.get("validations", {}) or {}
        rs_call = r.get("run_sql_call") or {}
        gv_call = r.get("generate_viz_call") or {}
        status = r.get("overall_status", "PENDING")
        screenshots = ""
        for sp in (r.get("screenshots") or []):
            if sp:
                # Make screenshots relative to the report file
                try:
                    rel = Path(sp).resolve().relative_to(job_dir.resolve())
                except Exception:
                    rel = Path(sp).name
                    rel = Path("screenshots") / rel
                screenshots += f'<img class="screenshot" src="{rel}" alt="screenshot">'
        rs_body = json.dumps(rs_call.get("response_body"), indent=2, default=str)[:6000] if rs_call else ""
        gv_body = json.dumps(gv_call.get("response_body"), indent=2, default=str)[:6000] if gv_call else ""
        parts.append(f"""
<details {'open' if status != 'PASS' else ''}>
<summary><span class="badge {status}">{status}</span> <b>q{r['id']:02d}</b> — {html.escape(r['nl_query'])} <span class="meta">({r.get('total_duration_ms','?')} ms)</span></summary>
<table>
<tr><th>run-sql</th><td>{html.escape(rs_call.get('method','-'))} <code>{html.escape(str(rs_call.get('url','-')))}</code><br>HTTP {rs_call.get('status','n/a')}, dur {rs_call.get('duration_ms','-')} ms<br>columns: <code>{html.escape(json.dumps(v.get('run_sql_columns')))}</code> rows: {v.get('run_sql_row_count','-')}</td></tr>
<tr><th>generate-viz</th><td>{html.escape(gv_call.get('method','-'))} <code>{html.escape(str(gv_call.get('url','-')))}</code><br>HTTP {gv_call.get('status','n/a')}, dur {gv_call.get('duration_ms','-')} ms<br>chart_type: <code>{html.escape(str(v.get('generate_viz_chart_type')))}</code>, axes (<code>{html.escape(str(v.get('generate_viz_x_axis')))}</code>, <code>{html.escape(str(v.get('generate_viz_y_axis')))}</code>)</td></tr>
<tr><th>columns synced</th><td><code>{v.get('columns_synced_run_sql_vs_generate_viz')}</code></td></tr>
<tr><th>fail/warn</th><td>{html.escape(json.dumps(v.get('fail_reasons')))} / {html.escape(json.dumps(v.get('warn_reasons')))}</td></tr>
</table>
<details><summary>run-sql response</summary><pre>{html.escape(rs_body or '(none)')}</pre></details>
<details><summary>generate-viz response</summary><pre>{html.escape(gv_body or '(none)')}</pre></details>
{('<details><summary>screenshots</summary>'+screenshots+'</details>') if screenshots else ''}
</details>
""")

    return HTML_TMPL.format(
        generated_at=summary["generated_at"],
        total=summary["total_queries"],
        pass_n=sb.get("PASS", 0),
        partial_n=sb.get("PARTIAL", 0),
        fail_n=sb.get("FAIL", 0),
        timeout_n=sb.get("TIMEOUT", 0),
        avg_ms=summary["performance_ms"]["avg"],
        max_ms=summary["performance_ms"]["max"],
        rs_present=summary["run_sql"]["present"],
        rs_ok=summary["run_sql"]["http_ok"],
        rs_rows=summary["run_sql"]["with_rows"],
        gv_present=summary["generate_viz"]["present"],
        gv_ok=summary["generate_viz"]["http_ok"],
        gv_chart=summary["generate_viz"]["with_chart"],
        cols_synced=summary["column_sync"]["synced"],
        cols_unsynced=summary["column_sync"]["unsynced"],
        fail_list=fail_list,
        partial_list=partial_list,
        timeout_list=timeout_list,
        per_query="\n".join(parts),
    )


def generate(job_dir: str | Path) -> dict[str, str]:
    job_dir = Path(job_dir)
    results = load_results(job_dir)
    summary = summarize(results)

    summary_path = job_dir / "SUMMARY.json"
    md_path = job_dir / "REPORT.md"
    html_path = job_dir / "REPORT.html"

    summary_path.write_text(json.dumps(summary, indent=2))
    md_path.write_text(render_md(results, summary))
    html_path.write_text(render_html(results, summary, job_dir))

    return {
        "summary": str(summary_path),
        "markdown": str(md_path),
        "html": str(html_path),
    }


def cli() -> None:
    ap = argparse.ArgumentParser(prog="skylar-qa-report")
    ap.add_argument("--job-dir", required=True, help="Path to job output directory containing results/")
    args = ap.parse_args()
    out = generate(args.job_dir)
    for k, v in out.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    cli()

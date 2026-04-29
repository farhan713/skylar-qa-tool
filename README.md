# Skylar IQ SQL Agent — QA Tool

A generic, self-service automation tool for QA-testing **any** Celerant Back Office tenant
running the "Skylar IQ" SQL Agent. You give it three things — a login URL, credentials,
and an Excel sheet of natural-language questions — and it:

1. Logs in (handling the Machine ID + RequireJS handshake),
2. Navigates to the SQL Agent screen,
3. Submits every question through the chat box,
4. Captures every API call (`generate_sql`, `run-sql`, `generate-viz`),
5. Clicks **Generate Visualization** when the UI offers it,
6. Validates response shape, column-name sync, and chart payload integrity,
7. Produces an HTML / Markdown / JSON report + per-query screenshots.

There's a browser-based form (Flask) that anyone can use, plus a CLI for scripted runs.

## Screenshot

Open `http://127.0.0.1:5050/`, fill the form, click Start. Live progress streams in,
and a final report opens in a new tab when the run finishes.

---

## Install

Requires Python 3.10+ and ~150 MB of disk for Playwright browser binaries.

```bash
git clone <repo-url> skylar-qa
cd skylar-qa
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

That's it. No Docker, no compiled binaries.

---

## Run the web UI

```bash
./run-server.sh
# or
python3 -m app.server
```

Browser opens to `http://127.0.0.1:5050/`. Fill in:

| Field | Example | Notes |
|---|---|---|
| **Login URL** | `https://&lt;client-host&gt;:8443/backoffice/?mid=100` | Full URL of the Celerant login page. Different clients = different hostnames/paths, so paste the exact URL from your client's bookmark. |
| **Username** | `user` | |
| **Password** | `*****` | |
| **Machine ID** | `100` | Default. Override if your client uses something else. Leave blank to skip the localStorage step. |
| **SQL Agent path** | `/backoffice/mv-assets/index-modern.html#/listScreen/sqlagent` | Default. Override if a tenant deploys at a different SPA route. |
| **Questions Excel** | upload `.xlsx` | Column A = NL question; column B (optional) = expected SQL |
| **run-sql / generate-viz timeouts** | `120000` ms | Per-query budget. Slower clients → bump to 300000. |

Click **Start QA Run**. You'll be taken to the job page where you can watch:
- Live event log (every URL hit, response code, timing)
- Counts (PASS / PARTIAL / FAIL / TIMEOUT) updating in real time
- "Last 5 queries" table
- A button to open the final HTML report when the run finishes

A sample `data/sample_questions.xlsx` (5 questions) ships with the repo so you can try it quickly.

---

## Run from the command line

For scripted / CI use — no UI, no Flask:

```bash
python3 -m app.runner \
  --login-url 'https://&lt;client-host&gt;:8443/backoffice/?mid=100' \
  --username user \
  --password '*****' \
  --questions data/sample_questions.xlsx \
  --output-dir runs/$(date +%Y%m%d-%H%M%S)
```

Then generate the report from that directory:

```bash
python3 -m app.report --job-dir runs/<that-folder>
# Writes REPORT.html, REPORT.md, SUMMARY.json into the same folder
```

---

## What gets produced (per run)

```
runs/<job-id>/
├── job.json                  # config + final summary
├── questions.xlsx            # the input file (so the run is reproducible)
├── events.log                # every progress line streamed to the web UI
├── results/qNN.json          # one file per query — full request + response + validations
├── network_logs/qNN_calls.json
├── screenshots/qNN_*.png     # input / run-sql / viz states, full-page
├── all_results.json          # aggregate of every query record
├── REPORT.html               # ← THE deliverable
├── REPORT.md
└── SUMMARY.json
```

Open `REPORT.html` in any browser — it has per-query expandable sections with:
- HTTP method + URL + status for `run-sql` and `generate-viz`
- Pretty-printed JSON request/response bodies
- Column-name comparison
- Chart type, axes, data point count
- All screenshots inline

---

## Validations (per query)

For each NL question, the runner checks:

| Check | What it means |
|---|---|
| `run-sql` HTTP 2xx | The actual SQL execution returned successfully |
| `run-sql` has rows | The query returned at least one data row |
| `run-sql` no empty column names | If column names are blank, that's a SQL aliasing bug — flagged |
| `generate-viz` HTTP 2xx | The chart-generation LLM call succeeded |
| `generate-viz` chart payload | Response actually contains chart_type + axes + columns |
| Column-name sync | Columns referenced in the chart match column names returned by run-sql |

Each query is graded:
- **PASS** — everything green
- **PARTIAL** — at least one warning (e.g. zero rows, mismatched columns)
- **FAIL** — at least one hard failure (call missing or HTTP error)
- **TIMEOUT** — the call didn't return within the per-query budget; runner moves on

---

## Notes on the login flow (gotchas)

The Celerant login form requires a **Machine ID** that's normally set by browser-fingerprint
JavaScript. Without it, the server returns `{"status":"failed","message":"Machine ID is empty"}`.
The runner handles this two ways:

1. By including `?mid=<value>` in the login URL — the page's own JS reads it from the query string and stores it in localStorage.
2. By setting `localStorage.MACHINE_ID = '<value>'` directly before submitting.

If your client has a non-default Machine ID convention, just put it in the form's "Machine ID" field.

The login form's submit handler is wired up by **RequireJS asynchronously** — clicking the submit button before that handshake completes does a native POST that omits the machineid query param, and the server rejects it. The runner waits for `jQuery._data(loginform, 'events').submit` to be populated before clicking.

---

## Troubleshooting

**"Login failed at … UserAuthenticationServlet.do: Machine ID is empty"**
→ Wrong Machine ID. Try `100`, or get the right value from the client's Celerant admin.

**Lots of TIMEOUTs**
→ The LLM endpoint at `celerantai.com` can be slow for some questions. Bump `run-sql timeout` to 300000–600000 (5–10 min).

**"jQuery submit handler not bound" timeout**
→ The login page didn't finish loading. Check connectivity to the client's host; try opening the URL manually in a browser first.

**`with_chart=0` in summary even though queries passed**
→ This means our heuristic doesn't recognise the chart payload shape. Inspect `runs/<id>/results/qXX.json` and update `inspect_chart_payload()` in `app/runner.py`.

---

## Architecture

```
┌────────────────────────┐     ┌─────────────────────────┐
│ Web UI (Flask)         │────▶│ Background thread runs  │
│   POST /jobs           │     │ app.runner.run(cfg)     │
│   GET /jobs/<id>       │◀────│ Streams events back     │
│   GET /jobs/<id>/events│ SSE │ via on_event callback   │
└────────────────────────┘     └────────────┬────────────┘
                                            │ writes
                                            ▼
                              ┌──────────────────────────┐
                              │ runs/<job-id>/           │
                              │   results/qNN.json       │
                              │   screenshots/           │
                              │   network_logs/          │
                              │   events.log             │
                              └──────────────────────────┘
                                            │ feeds
                                            ▼
                              ┌──────────────────────────┐
                              │ app.report.generate()    │
                              │   REPORT.html / .md      │
                              │   SUMMARY.json           │
                              └──────────────────────────┘
```

The same `app.runner.run(cfg)` function backs both the CLI and the web UI — pure config-driven, no globals.

---

## Project layout

```
.
├── app/                     # the generic tool (importable package)
│   ├── runner.py            # Playwright-driven QA runner
│   ├── report.py            # HTML / MD / JSON report generator
│   ├── excel_reader.py      # .xlsx → list of questions
│   ├── server.py            # Flask web UI
│   ├── templates/index.html
│   ├── templates/job.html
│   └── static/style.css
├── runs/                    # per-job output folders (created at runtime)
├── data/sample_questions.xlsx  # ships with the repo for first-run testing
├── tests/                   # legacy / dev scripts (the original 55-query Skylar IQ run)
├── reports/                 # output of the legacy run
├── requirements.txt
├── run-server.sh            # convenience launcher
└── README.md                # this file
```

---

## License & ownership

Internal QA automation. Not for external distribution without permission.

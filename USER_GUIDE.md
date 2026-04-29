# Skylar IQ QA Tool — User Guide

A 5-minute guide to installing and using the tool.

---

## What this tool does

Drop in **any client's** Celerant Back Office login URL, credentials, and an Excel sheet
of natural-language questions. The tool will:

1. Log in to that client's Skylar IQ
2. Open the SQL Agent screen
3. Ask every question one by one
4. Click **Generate Visualization** when the UI offers it
5. Capture every API call (`generate_sql`, `run-sql`, `generate-viz`)
6. Produce a polished HTML report with screenshots, response payloads, and pass/fail status per question

It works for any tenant — you just supply the URL.

---

## Install (one-time, ~5 minutes)

### Prerequisites

- **Python 3.10 or newer** (check with `python3 --version`)
- **Internet access** so Playwright can download the Chromium browser
- ~200 MB free disk space

### Steps

```bash
# 1. Get the code
git clone <YOUR_REPO_URL> skylar-qa
cd skylar-qa

# 2. Install Python packages
python3 -m pip install -r requirements.txt

# 3. Install the headless browser (one-time, ~150 MB download)
python3 -m playwright install chromium
```

That's it. No Docker, no compiled binaries.

---

## Use it (every time)

### 1. Start the tool

From the `skylar-qa` folder, run:

```bash
./run-server.sh
```

(On Windows: `python -m app.server`)

Your browser will automatically open `http://127.0.0.1:5050/`.

### 2. Fill in the form

| Field | What to enter |
|---|---|
| **Login URL** | The full URL of your client's Celerant login page, e.g. `https://acme-store.com:8443/backoffice/?mid=100` |
| **Username** | Login username for that client |
| **Password** | Login password |
| **Machine ID** | Usually `100`. Override if your client uses something else. |
| **SQL Agent path** | Leave at default unless the client has a custom path |
| **run-sql / generate-viz timeouts** | `120000` ms (2 min) is usually fine. Bump to `300000` (5 min) for slow clients. |
| **Questions Excel** | Upload your `.xlsx` file (see format below) |

Click **Start QA Run**.

### 3. Watch live progress

You'll be taken to the job page where you can see:

- **Counts** updating in real time: PASS / PARTIAL / FAIL / TIMEOUT
- **Progress bar** showing completed-of-total
- **Last 5 queries** table with status + duration
- **Event log** streaming every URL the tool hits

When the run finishes, click **Open final report ↗** — it opens the full HTML report in a new tab.

### 4. Review the report

The HTML report has:

- An executive summary (counts, average duration, API health)
- Lists of failing / partial / timed-out queries with reasons
- An expandable section per query with:
  - Full request + response JSON (pretty-printed)
  - HTTP status, timing
  - Column-name comparison between `run-sql` and `generate-viz`
  - All screenshots inline (input box, results table, generated chart)

You can also share the report file directly — it's self-contained HTML in `runs/<job-id>/REPORT.html`.

---

## Excel file format

Your `.xlsx` should look like this:

| natural_language_query | expected_sql *(optional)* |
|---|---|
| Show me a list of all my calibers | `SELECT DISTINCT caliber FROM ...` |
| What firearm brands sold most this year? | `SELECT TOP(10) brand, SUM(qty) ...` |
| Which items have the highest return rate? | |

Rules:
- **Column A** is the natural-language question (required)
- **Column B** is the expected SQL (optional — used as a reference, not enforced)
- **Header row** is auto-detected if the first cell is `natural_language_query`, `question`, or `query`
- Empty rows are skipped
- Only the first sheet is read

A working example ships with the repo: **`data/sample_questions.xlsx`** (5 questions). Try it on your first run.

---

## Result statuses explained

| Badge | Meaning |
|---|---|
| 🟢 **PASS** | All checks passed: run-sql HTTP 2xx with rows, generate-viz returned a valid chart, columns synced |
| 🟡 **PARTIAL** | Data was captured but with warnings — e.g. zero rows returned, or chart columns don't match table columns |
| 🔴 **FAIL** | Hard failure — `run-sql` or `generate-viz` call missing or returned an HTTP error |
| 🟣 **TIMEOUT** | The query didn't return within the configured budget (default 2 min). The runner moves on automatically and reloads the page. |

---

## Troubleshooting

**"Login failed: Machine ID is empty"**
Wrong Machine ID. Try `100`, or get the correct value from the client's Celerant admin.

**Lots of TIMEOUTs**
The Skylar IQ LLM endpoint can be slow. Bump both timeouts to `300000` (5 min) or `600000` (10 min) and re-run.

**Can't connect to login URL**
Check the URL works in your browser first. The tool ignores HTTPS cert errors but it can't help if the host is unreachable.

**Where's my report?**
Every run creates a folder `runs/<job-id>/`. The HTML report lives at `runs/<job-id>/REPORT.html`. The job page in the web UI links to it.

---

## CLI alternative (for power users / scripted runs)

If you'd rather skip the web UI:

```bash
python3 -m app.runner \
  --login-url 'https://client-host:8443/backoffice/?mid=100' \
  --username user \
  --password '*****' \
  --questions /path/to/questions.xlsx \
  --output-dir runs/$(date +%Y%m%d-%H%M%S)

# Then build the report:
python3 -m app.report --job-dir runs/<that-folder>
```

---

## Sharing a report

After a run finishes, the entire folder `runs/<job-id>/` is self-contained:

- `REPORT.html` — open in any browser
- `screenshots/` — referenced by the HTML
- `results/qNN.json` — raw per-query data
- `all_results.json` — aggregate

Zip the folder and send it. The HTML works offline.

---

## Need help?

Internal slack channel / email of repo owner.

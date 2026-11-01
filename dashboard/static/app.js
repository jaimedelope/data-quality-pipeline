async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} → ${res.status}`);
  return res.json();
}

function setStatus(ok, text) {
  const el = document.getElementById("status");
  const label = document.getElementById("statusText");
  el.classList.toggle("ok", ok);
  label.textContent = text;
}

function renderChecks(checks) {
  const body = document.getElementById("checksBody");
  if (!checks || !checks.length) {
    body.innerHTML = `<tr><td colspan="4" style="color:var(--muted)">Waiting for first batch…</td></tr>`;
    return;
  }
  body.innerHTML = checks
    .map((c) => {
      const cls = c.passed ? "" : "anomaly";
      const status = c.passed ? "PASS" : "FAIL";
      return `<tr class="${cls}">
        <td>${c.name}</td>
        <td>${status}</td>
        <td class="score">${c.metric}</td>
        <td>${c.detail}</td>
      </tr>`;
    })
    .join("");
}

function renderFailures(failures) {
  const list = document.getElementById("failList");
  if (!failures.length) {
    list.innerHTML = `<li style="background:transparent;border-style:dashed"><div class="meta">No failures yet — good batches are also informative.</div></li>`;
    return;
  }
  list.innerHTML = failures
    .map((f) => {
      const offenders = (f.offenders || [])
        .slice(0, 3)
        .map((o) => o.order_id || o.issue || JSON.stringify(o))
        .join(", ");
      return `<li>
        <div class="anom-icon" aria-hidden="true">!</div>
        <div>
          <div class="title">${f.name}</div>
          <div class="meta">${f.detail}${offenders ? ` · ${offenders}` : ""}</div>
        </div>
      </li>`;
    })
    .join("");
}

function renderRuns(runs) {
  const body = document.getElementById("runsBody");
  if (!runs.length) {
    body.innerHTML = `<tr><td colspan="6" style="color:var(--muted)">No runs yet</td></tr>`;
    return;
  }
  body.innerHTML = runs
    .map((r) => {
      const short = (r.batch_id || "").slice(0, 8);
      const when = (r.checked_at || "").replace("T", " ").slice(0, 19);
      const failed = (r.failed_names || []).join(", ") || "—";
      const cls = r.checks_failed > 0 ? "anomaly" : "";
      return `<tr class="${cls}">
        <td>${short}</td>
        <td>${r.row_count}</td>
        <td>${r.pass_rate_pct}%</td>
        <td>${failed}</td>
        <td>${r.duration_ms} ms</td>
        <td>${when}</td>
      </tr>`;
    })
    .join("");
}

async function tick() {
  try {
    const [metrics, feed] = await Promise.all([
      fetchJson("/api/metrics"),
      fetchJson("/api/feed?limit=30"),
    ]);
    document.getElementById("runsTotal").textContent = metrics.runs_total;
    document.getElementById("rowsTotal").textContent = metrics.rows_total;
    document.getElementById("passRate").textContent = `${metrics.latest_pass_rate_pct}%`;
    document.getElementById("avgPass").textContent = `${metrics.avg_pass_rate_pct}%`;
    document.getElementById("failedTotal").textContent = metrics.checks_failed_total;
    document.getElementById("avgDuration").textContent = `${metrics.avg_duration_ms} ms`;
    renderChecks(metrics.latest_checks || []);
    renderFailures(feed.failures || []);
    renderRuns(feed.runs || []);
    setStatus(true, "live · redis connected");
  } catch (err) {
    setStatus(false, "disconnected");
    console.error(err);
  }
}

tick();
setInterval(tick, 2000);

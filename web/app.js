/** Browser demo: Pandera-style contract scorecards with trends + downloadable reports. */

const CONTRACTS = {
  customers: {
    owner: "growth-analytics",
    description: "Customer dimension used by CRM and billing joins.",
    sla: 95,
    yaml: `dataset: customers
owner: growth-analytics
sla_pass_rate: 0.95
engine: pandera
columns:
  customer_id: { unique: true, required: true }
  email: { required: true, pattern: email }
  country: { allowed: [ES, PT, FR, DE, IT, GB, US] }`,
  },
  orders: {
    owner: "commerce-analytics",
    description: "Fact table of completed checkout events for revenue reporting.",
    sla: 97,
    yaml: `dataset: orders
owner: commerce-analytics
sla_pass_rate: 0.97
engine: pandera
columns:
  order_id: { unique: true }
  customer_id: { references: customers.customer_id }
  amount_eur: { min: 0.01, max: 5000 }`,
  },
  events: {
    owner: "product-analytics",
    description: "Product usage events for funnel and retention dashboards.",
    sla: 93,
    yaml: `dataset: events
owner: product-analytics
sla_pass_rate: 0.93
engine: pandera
columns:
  event_id: { unique: true }
  event_name: { allowed: [view_item, add_to_cart, checkout, search] }
  event_ts: { freshness_hours: 6 }`,
  },
};

const state = {
  selected: "customers",
  datasets: {
    customers: { runs: 0, breaches: 0, passSum: 0, latest: null, checks: [], trend: [], reports: [] },
    orders: { runs: 0, breaches: 0, passSum: 0, latest: null, checks: [], trend: [], reports: [] },
    events: { runs: 0, breaches: 0, passSum: 0, latest: null, checks: [], trend: [], reports: [] },
  },
  recent: [],
};

function sparkline(values) {
  if (!values.length) return "";
  const w = 120, h = 28;
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 100);
  const span = Math.max(max - min, 1);
  const pts = values.map((v, i) => {
    const x = (i / Math.max(values.length - 1, 1)) * (w - 2) + 1;
    const y = h - 2 - ((v - min) / span) * (h - 4);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}"><polyline fill="none" stroke="currentColor" stroke-width="1.8" points="${pts}"/></svg>`;
}

function simulateChecks(dataset) {
  const catalog = {
    customers: [["pandera:email", 0.12], ["pandera:country", 0.1], ["pandera:customer_id", 0.08], ["row_count_between", 0.08]],
    orders: [["pandera:amount_eur", 0.12], ["fk:customer_id", 0.16], ["pandera:status", 0.1], ["row_count_between", 0.09]],
    events: [["pandera:event_name", 0.11], ["freshness:event_ts", 0.15], ["pandera:event_id", 0.08], ["row_count_between", 0.09]],
  };
  const checks = catalog[dataset].map(([name, failP]) => ({
    check_name: name,
    passed: Math.random() > failP,
    detail: Math.random() > failP ? `${name} ok` : `${name} failed on sample rows`,
  }));
  // fix detail based on passed
  checks.forEach((c) => {
    c.detail = c.passed ? `${c.check_name} ok` : `${c.check_name} failed on sample rows`;
  });
  const passedN = checks.filter((c) => c.passed).length;
  const passRate = Math.round((passedN / checks.length) * 1000) / 10;
  const sla = CONTRACTS[dataset].sla;
  const slaMet = passRate >= sla;
  const runId = crypto.randomUUID ? crypto.randomUUID() : `run-${Date.now()}`;
  return {
    id: runId,
    pass_rate: passRate,
    sla_pass_rate: sla,
    sla_met: slaMet,
    row_count: 40 + Math.floor(Math.random() * 160),
    checks_passed: passedN,
    checks_total: checks.length,
    duration_ms: +(2 + Math.random() * 12).toFixed(2),
    file_name: `${dataset}_${new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14)}.csv`,
    finished_at: new Date().toISOString(),
    checks,
    quarantine_sample: !slaMet,
  };
}

function downloadReport(run, format) {
  const payload = {
    run_id: run.id,
    dataset: run.dataset,
    file_name: run.file_name,
    engine: "pandera+contracts (browser demo)",
    pass_rate_pct: run.pass_rate,
    sla_met: run.sla_met,
    checks: run.checks.map((c) => ({ name: c.check_name, passed: c.passed, detail: c.detail })),
  };
  let blob;
  let filename;
  if (format === "json") {
    blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    filename = `${run.id.slice(0, 8)}.json`;
  } else if (format === "html") {
    const rows = payload.checks.map((c) => `<tr><td>${c.name}</td><td>${c.passed ? "PASS" : "FAIL"}</td><td>${c.detail}</td></tr>`).join("");
    const html = `<!DOCTYPE html><html><body><h1>${run.dataset} report</h1><p>Pass ${run.pass_rate}% · SLA ${run.sla_met ? "met" : "breach"}</p><table border="1">${rows}</table></body></html>`;
    blob = new Blob([html], { type: "text/html" });
    filename = `${run.id.slice(0, 8)}.html`;
  } else {
    blob = new Blob(["order_id,issue\nsample_1,contract_breach\nsample_2,orphan_fk\n"], { type: "text/csv" });
    filename = `${run.id.slice(0, 8)}_quarantine_sample.csv`;
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

function tickDataset(dataset) {
  const result = simulateChecks(dataset);
  const bucket = state.datasets[dataset];
  bucket.runs += 1;
  bucket.passSum += result.pass_rate;
  if (!result.sla_met) bucket.breaches += 1;
  bucket.latest = result;
  bucket.checks = result.checks;
  bucket.trend.push(result.pass_rate);
  bucket.trend = bucket.trend.slice(-24);
  const run = { ...result, dataset };
  bucket.reports.unshift(run);
  bucket.reports = bucket.reports.slice(0, 10);
  state.recent.unshift(run);
  state.recent = state.recent.slice(0, 20);
}

function render() {
  const list = document.getElementById("datasetList");
  list.innerHTML = Object.keys(state.datasets)
    .map((ds) => {
      const b = state.datasets[ds];
      const avg = b.runs ? (b.passSum / b.runs).toFixed(1) : "—";
      return `<button class="dataset-btn ${ds === state.selected ? "active" : ""}" data-ds="${ds}">
        <span class="name">${ds}</span>
        <span class="meta">${avg}% avg · ${b.breaches} breaches</span>
        <span class="spark-wrap">${sparkline(b.trend)}</span>
      </button>`;
    })
    .join("");
  list.querySelectorAll("button").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.selected = btn.dataset.ds;
      render();
    })
  );

  const ds = state.selected;
  const bucket = state.datasets[ds];
  const contract = CONTRACTS[ds];
  document.getElementById("datasetTitle").textContent = ds;
  document.getElementById("contractYaml").textContent = contract.yaml;

  if (!bucket.latest) {
    document.getElementById("scorecard").innerHTML = `<p class="empty">Waiting…</p>`;
    document.getElementById("checksWrap").innerHTML = "";
  } else {
    const p = bucket.latest.pass_rate;
    const run = { ...bucket.latest, dataset: ds, checks: bucket.checks };
    document.getElementById("scorecard").innerHTML = `
      <div class="score-row">
        <div class="score-ring" style="--p:${p}"><strong>${p}%</strong></div>
        <div style="flex:1;min-width:220px">
          <div class="sla-pill ${bucket.latest.sla_met ? "met" : "breach"}">${bucket.latest.sla_met ? "SLA met" : "SLA breach"} · threshold ${bucket.latest.sla_pass_rate}%</div>
          <p style="margin:10px 0 0;color:var(--muted);font-size:14px;line-height:1.5">
            Owner: <strong>${contract.owner}</strong> · engine <strong>Pandera</strong><br/>
            Latest file <span class="mono">${bucket.latest.file_name}</span> ·
            ${bucket.latest.row_count} rows ·
            ${bucket.latest.checks_passed}/${bucket.latest.checks_total} checks
          </p>
          <p style="margin:8px 0 0;color:var(--muted);font-size:13px">${contract.description}</p>
          <div class="trend-block">
            <span class="trend-label">Pass rate trend (last ${bucket.trend.length} runs)</span>
            ${sparkline(bucket.trend)}
          </div>
          <div class="actions">
            <button class="btn" id="dlJson">JSON report</button>
            <button class="btn" id="dlHtml">HTML report</button>
            <button class="btn danger" id="dlCsv" ${bucket.latest.quarantine_sample ? "" : "disabled"}>${bucket.latest.quarantine_sample ? "Quarantine sample" : "No quarantine sample"}</button>
          </div>
        </div>
      </div>`;
    document.getElementById("dlJson").onclick = () => downloadReport(run, "json");
    document.getElementById("dlHtml").onclick = () => downloadReport(run, "html");
    const csvBtn = document.getElementById("dlCsv");
    if (!csvBtn.disabled) csvBtn.onclick = () => downloadReport(run, "csv");

    document.getElementById("checksWrap").innerHTML = `
      <table class="checks"><thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead>
      <tbody>${bucket.checks.map((c) => `<tr><td class="mono">${c.check_name}</td><td><span class="tag ${c.passed ? "pass" : "fail"}">${c.passed ? "PASS" : "FAIL"}</span></td><td>${c.detail}</td></tr>`).join("")}</tbody></table>`;
  }

  const runs = Object.values(state.datasets).reduce((a, b) => a + b.runs, 0);
  const breaches = Object.values(state.datasets).reduce((a, b) => a + b.breaches, 0);
  const passSum = Object.values(state.datasets).reduce((a, b) => a + b.passSum, 0);
  document.getElementById("sumRuns").textContent = runs;
  document.getElementById("sumBreaches").textContent = breaches;
  document.getElementById("sumPass").textContent = runs ? `${(passSum / runs).toFixed(1)}%` : "0%";

  document.getElementById("runsWrap").innerHTML = `
    <table class="runs"><thead><tr><th>Dataset</th><th>File</th><th>Pass</th><th>SLA</th><th>Rows</th><th>When</th></tr></thead>
    <tbody>${state.recent.map((r) => `<tr>
      <td>${r.dataset}</td><td class="mono">${r.file_name}</td><td>${r.pass_rate}%</td>
      <td><span class="tag ${r.sla_met ? "pass" : "fail"}">${r.sla_met ? "MET" : "BREACH"}</span></td>
      <td>${r.row_count}</td>
      <td class="mono">${r.finished_at.replace("T", " ").slice(0, 19)}</td>
    </tr>`).join("")}</tbody></table>`;
}

function tick() {
  ["customers", "orders", "events"].forEach(tickDataset);
  render();
}

tick();
setInterval(tick, 4000);

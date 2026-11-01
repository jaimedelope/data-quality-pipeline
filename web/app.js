/** Browser demo: warehouse contract scorecards (not a streaming fraud UI). */

const CONTRACTS = {
  customers: {
    owner: "growth-analytics",
    description: "Customer dimension used by CRM and billing joins.",
    sla: 95,
    yaml: `dataset: customers
owner: growth-analytics
sla_pass_rate: 0.95
columns:
  customer_id: { unique: true, required: true }
  email: { required: true, pattern: email }
  country: { allowed: [ES, PT, FR, DE, IT, GB, US] }
expectations:
  - type: row_count_between
    min: 40
    max: 120`,
  },
  orders: {
    owner: "commerce-analytics",
    description: "Fact table of completed checkout events for revenue reporting.",
    sla: 97,
    yaml: `dataset: orders
owner: commerce-analytics
sla_pass_rate: 0.97
columns:
  order_id: { unique: true }
  customer_id: { references: customers.customer_id }
  amount_eur: { min: 0.01, max: 5000 }
  status: { allowed: [paid, refunded, chargeback] }
expectations:
  - type: row_count_between
    min: 60
    max: 180`,
  },
  events: {
    owner: "product-analytics",
    description: "Product usage events for funnel and retention dashboards.",
    sla: 93,
    yaml: `dataset: events
owner: product-analytics
sla_pass_rate: 0.93
columns:
  event_id: { unique: true }
  event_name: { allowed: [view_item, add_to_cart, checkout, search] }
  event_ts: { freshness_hours: 6 }
expectations:
  - type: row_count_between
    min: 80
    max: 250`,
  },
};

const state = {
  selected: "customers",
  datasets: {
    customers: { runs: 0, breaches: 0, passSum: 0, latest: null, checks: [] },
    orders: { runs: 0, breaches: 0, passSum: 0, latest: null, checks: [] },
    events: { runs: 0, breaches: 0, passSum: 0, latest: null, checks: [] },
  },
  recent: [],
};

function simulateChecks(dataset) {
  const catalog = {
    customers: [
      ["required:email", 0.12],
      ["unique:customer_id", 0.1],
      ["allowed:country", 0.1],
      ["pattern:email", 0.14],
      ["row_count_between", 0.08],
    ],
    orders: [
      ["required:customer_id", 0.1],
      ["unique:order_id", 0.08],
      ["fk:customer_id", 0.16],
      ["range:amount_eur", 0.12],
      ["allowed:status", 0.1],
      ["row_count_between", 0.09],
    ],
    events: [
      ["unique:event_id", 0.08],
      ["allowed:event_name", 0.11],
      ["freshness:event_ts", 0.15],
      ["required:session_id", 0.1],
      ["row_count_between", 0.09],
    ],
  };

  const checks = catalog[dataset].map(([name, failP]) => {
    const passed = Math.random() > failP;
    return {
      check_name: name,
      passed,
      detail: passed ? `${name} ok` : `${name} failed on sample rows`,
    };
  });
  const passedN = checks.filter((c) => c.passed).length;
  const passRate = Math.round((passedN / checks.length) * 1000) / 10;
  const sla = CONTRACTS[dataset].sla;
  const slaMet = passRate >= sla;
  const rows = 40 + Math.floor(Math.random() * 160);
  return {
    pass_rate: passRate,
    sla_pass_rate: sla,
    sla_met: slaMet,
    row_count: rows,
    checks_passed: passedN,
    checks_total: checks.length,
    duration_ms: +(2 + Math.random() * 12).toFixed(2),
    file_name: `${dataset}_${new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14)}.csv`,
    finished_at: new Date().toISOString(),
    checks,
  };
}

function tickDataset(dataset) {
  const result = simulateChecks(dataset);
  const bucket = state.datasets[dataset];
  bucket.runs += 1;
  bucket.passSum += result.pass_rate;
  if (!result.sla_met) bucket.breaches += 1;
  bucket.latest = result;
  bucket.checks = result.checks;
  state.recent.unshift({
    dataset,
    file_name: result.file_name,
    pass_rate: result.pass_rate,
    sla_met: result.sla_met,
    row_count: result.row_count,
    duration_ms: result.duration_ms,
    finished_at: result.finished_at,
  });
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
    document.getElementById("scorecard").innerHTML = `
      <div class="score-row">
        <div class="score-ring" style="--p:${p}"><strong>${p}%</strong></div>
        <div>
          <div class="sla-pill ${bucket.latest.sla_met ? "met" : "breach"}">${
            bucket.latest.sla_met ? "SLA met" : "SLA breach"
          } · threshold ${bucket.latest.sla_pass_rate}%</div>
          <p style="margin:10px 0 0;color:var(--muted);font-size:14px;line-height:1.5">
            Owner: <strong>${contract.owner}</strong><br/>
            Latest file <span class="mono">${bucket.latest.file_name}</span> ·
            ${bucket.latest.row_count} rows ·
            ${bucket.latest.checks_passed}/${bucket.latest.checks_total} checks
          </p>
          <p style="margin:8px 0 0;color:var(--muted);font-size:13px">${contract.description}</p>
        </div>
      </div>`;
    document.getElementById("checksWrap").innerHTML = `
      <table class="checks"><thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead>
      <tbody>${bucket.checks
        .map(
          (c) => `<tr><td class="mono">${c.check_name}</td><td><span class="tag ${
            c.passed ? "pass" : "fail"
          }">${c.passed ? "PASS" : "FAIL"}</span></td><td>${c.detail}</td></tr>`
        )
        .join("")}</tbody></table>`;
  }

  const runs = Object.values(state.datasets).reduce((a, b) => a + b.runs, 0);
  const breaches = Object.values(state.datasets).reduce((a, b) => a + b.breaches, 0);
  const passSum = Object.values(state.datasets).reduce((a, b) => a + b.passSum, 0);
  document.getElementById("sumRuns").textContent = runs;
  document.getElementById("sumBreaches").textContent = breaches;
  document.getElementById("sumPass").textContent = runs ? `${(passSum / runs).toFixed(1)}%` : "0%";

  document.getElementById("runsWrap").innerHTML = `
    <table class="runs"><thead><tr><th>Dataset</th><th>File</th><th>Pass</th><th>SLA</th><th>Rows</th><th>Latency</th><th>When</th></tr></thead>
    <tbody>${state.recent
      .map(
        (r) => `<tr>
          <td>${r.dataset}</td><td class="mono">${r.file_name}</td><td>${r.pass_rate}%</td>
          <td><span class="tag ${r.sla_met ? "pass" : "fail"}">${r.sla_met ? "MET" : "BREACH"}</span></td>
          <td>${r.row_count}</td><td>${r.duration_ms} ms</td>
          <td class="mono">${r.finished_at.replace("T", " ").slice(0, 19)}</td>
        </tr>`
      )
      .join("")}</tbody></table>`;
}

function tick() {
  ["customers", "orders", "events"].forEach(tickDataset);
  render();
}

tick();
setInterval(tick, 4000);

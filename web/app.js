const BATCH_INTERVAL_MS = 3500;
const BATCH_SIZE = 80;
const DEFECT_RATE = 0.12;
const FRESHNESS_MAX_AGE_MIN = 30;
const VOLUME_MIN = 40;
const VOLUME_MAX = 120;
const NULL_THRESHOLD = 0.05;
const FEED_RUNS = 50;
const FEED_FAILS = 100;

const CURRENCIES = ["EUR", "USD", "GBP"];
const STATUSES = ["pending", "paid", "shipped", "cancelled"];
const PRODUCTS = Array.from({ length: 40 }, (_, i) => `SKU-${String(i + 1).padStart(3, "0")}`);
const USERS = Array.from({ length: 60 }, (_, i) => `user_${String(i + 1).padStart(3, "0")}`);
const DEFECT_TYPES = [
  "null_user",
  "null_amount",
  "null_currency",
  "duplicate_order",
  "invalid_amount",
  "invalid_currency",
  "invalid_status",
  "future_ts",
  "stale_ts",
  "orphan_product",
];
const CHECK_ORDER = [
  "schema",
  "completeness",
  "uniqueness",
  "validity",
  "freshness",
  "referential",
  "volume",
];

const alertIcon = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 3.8 18.2A1.2 1.2 0 0 0 4.9 20h14.2a1.2 1.2 0 0 0 1.1-1.8L12 3z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M12 9v5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><circle cx="12" cy="16.6" r="1" fill="currentColor"/></svg>`;

function uuid() {
  return crypto.randomUUID ? crypto.randomUUID() : `id-${Math.random().toString(16).slice(2)}`;
}

function isoOffset(msDelta) {
  return new Date(Date.now() + msDelta).toISOString();
}

function makeGoodRow(orderId) {
  return {
    order_id: orderId || uuid(),
    user_id: USERS[Math.floor(Math.random() * USERS.length)],
    product_id: PRODUCTS[Math.floor(Math.random() * PRODUCTS.length)],
    amount: Number((5 + Math.random() * 245).toFixed(2)),
    currency: CURRENCIES[Math.floor(Math.random() * CURRENCIES.length)],
    status: STATUSES[Math.floor(Math.random() * STATUSES.length)],
    event_time: isoOffset(-(Math.random() * 600_000)),
    created_at_ms: Date.now(),
  };
}

function injectDefect(row, defect, previousIds) {
  const out = { ...row, injected_defect: defect };
  if (defect === "null_user") out.user_id = null;
  else if (defect === "null_amount") out.amount = null;
  else if (defect === "null_currency") out.currency = null;
  else if (defect === "duplicate_order" && previousIds.length) out.order_id = previousIds[Math.floor(Math.random() * previousIds.length)];
  else if (defect === "invalid_amount") out.amount = [-25.5, 0, 99999][Math.floor(Math.random() * 3)];
  else if (defect === "invalid_currency") out.currency = ["BTC", "XXX", "YEN"][Math.floor(Math.random() * 3)];
  else if (defect === "invalid_status") out.status = ["refunded_oops", "UNKNOWN", ""][Math.floor(Math.random() * 3)];
  else if (defect === "future_ts") out.event_time = isoOffset((1 + Math.random() * 47) * 3600_000);
  else if (defect === "stale_ts") out.event_time = isoOffset(-(2 + Math.random() * 70) * 3600_000);
  else if (defect === "orphan_product") out.product_id = `ORPHAN-${Math.floor(1000 + Math.random() * 9000)}`;
  return out;
}

function buildBatch() {
  let size = BATCH_SIZE;
  const roll = Math.random();
  if (roll < 0.08) size = Math.max(5, Math.floor(size * (0.15 + Math.random() * 0.2)));
  else if (roll < 0.16) size = Math.floor(size * (1.6 + Math.random() * 0.6));

  const rows = [];
  const seen = [];
  for (let i = 0; i < size; i += 1) {
    let row = makeGoodRow();
    if (Math.random() < DEFECT_RATE) {
      let defect = DEFECT_TYPES[Math.floor(Math.random() * DEFECT_TYPES.length)];
      if (defect === "duplicate_order" && !seen.length) defect = "orphan_product";
      row = injectDefect(row, defect, seen);
    }
    seen.push(row.order_id);
    rows.push(row);
  }
  return rows;
}

function sample(rows, pred, limit = 5) {
  return rows.filter(pred).slice(0, limit);
}

function runChecks(rows) {
  const checks = [];

  // schema
  const required = ["order_id", "user_id", "product_id", "amount", "currency", "status", "event_time"];
  const missing = required.filter((c) => !(c in (rows[0] || {})));
  checks.push({
    name: "schema",
    status: missing.length ? "fail" : "pass",
    metric: { missing_columns: missing },
    offenders: [],
    message: missing.length ? `missing=${missing}` : "all required columns present",
  });

  // completeness
  const nullRates = {};
  const failedCols = [];
  let completenessOffenders = [];
  for (const col of ["user_id", "amount", "currency"]) {
    const nullMask = (r) => r[col] === null || r[col] === undefined || r[col] === "";
    const rate = rows.length ? rows.filter(nullMask).length / rows.length : 0;
    nullRates[col] = Number(rate.toFixed(4));
    if (rate > NULL_THRESHOLD) {
      failedCols.push(col);
      completenessOffenders = completenessOffenders.concat(sample(rows, nullMask));
    }
  }
  checks.push({
    name: "completeness",
    status: failedCols.length ? "fail" : "pass",
    metric: { null_rates: nullRates, threshold: NULL_THRESHOLD },
    offenders: completenessOffenders.slice(0, 8),
    message: failedCols.length ? `high nulls in ${failedCols}` : "critical null rates ok",
  });

  // uniqueness
  const seen = new Set();
  const dups = new Set();
  for (const r of rows) {
    if (seen.has(r.order_id)) dups.add(r.order_id);
    else seen.add(r.order_id);
  }
  const dupCount = rows.filter((r) => dups.has(r.order_id)).length - dups.size;
  checks.push({
    name: "uniqueness",
    status: dups.size ? "fail" : "pass",
    metric: { duplicate_count: Math.max(0, dupCount) },
    offenders: sample(rows, (r) => dups.has(r.order_id)),
    message: dups.size ? `${dups.size} duplicate order_id values` : "order_id unique",
  });

  // validity
  const badAmount = (r) => r.amount === null || Number(r.amount) <= 0 || Number(r.amount) > 10000;
  const badCur = (r) => !CURRENCIES.includes(r.currency);
  const badStatus = (r) => !STATUSES.includes(r.status);
  const invalidAmount = rows.filter(badAmount).length;
  const invalidCurrency = rows.filter(badCur).length;
  const invalidStatus = rows.filter(badStatus).length;
  const validityIssues = invalidAmount + invalidCurrency + invalidStatus;
  checks.push({
    name: "validity",
    status: validityIssues ? "fail" : "pass",
    metric: { invalid_amount: invalidAmount, invalid_currency: invalidCurrency, invalid_status: invalidStatus },
    offenders: sample(rows, (r) => badAmount(r) || badCur(r) || badStatus(r)),
    message: validityIssues ? `${validityIssues} validity violations` : "amounts/currency/status valid",
  });

  // freshness
  const now = Date.now();
  const maxAgeMs = FRESHNESS_MAX_AGE_MIN * 60_000;
  const future = (r) => new Date(r.event_time).getTime() > now;
  const stale = (r) => now - new Date(r.event_time).getTime() > maxAgeMs;
  const futureN = rows.filter(future).length;
  const staleN = rows.filter(stale).length;
  checks.push({
    name: "freshness",
    status: futureN + staleN ? "fail" : "pass",
    metric: { future_count: futureN, stale_count: staleN, max_age_min: FRESHNESS_MAX_AGE_MIN },
    offenders: sample(rows, (r) => future(r) || stale(r)),
    message: futureN + staleN ? `future=${futureN} stale=${staleN}` : "event_time fresh",
  });

  // referential
  const orphan = (r) => !PRODUCTS.includes(r.product_id);
  const orphanCount = rows.filter(orphan).length;
  checks.push({
    name: "referential",
    status: orphanCount ? "fail" : "pass",
    metric: { orphan_count: orphanCount, catalog_size: PRODUCTS.length },
    offenders: sample(rows, orphan),
    message: orphanCount ? `${orphanCount} orphan product_ids` : "all product_ids in catalog",
  });

  // volume
  const n = rows.length;
  const volumeOk = n >= VOLUME_MIN && n <= VOLUME_MAX;
  checks.push({
    name: "volume",
    status: volumeOk ? "pass" : "fail",
    metric: { row_count: n, min: VOLUME_MIN, max: VOLUME_MAX },
    offenders: [],
    message: volumeOk ? `batch size ${n} in band` : `batch size ${n} outside [${VOLUME_MIN},${VOLUME_MAX}]`,
  });

  return checks;
}

function aggregate(checks) {
  const total = checks.length;
  const passed = checks.filter((c) => c.status === "pass").length;
  const failed = total - passed;
  return {
    checks_total: total,
    checks_passed: passed,
    checks_failed: failed,
    pass_rate_pct: total ? Number(((passed / total) * 100).toFixed(2)) : 0,
    batch_status: failed === 0 ? "pass" : "fail",
  };
}

const state = {
  batches_total: 0,
  batches_passed: 0,
  batches_failed: 0,
  rows_total: 0,
  latencies: [],
  pass_rates: [],
  checks: Object.fromEntries(CHECK_ORDER.map((n) => [n, { total: 0, pass: 0, fail: 0 }])),
  runs: [],
  failures: [],
  batchN: 0,
};

function percentile(sorted, p) {
  if (!sorted.length) return 0;
  return sorted[Math.floor(p * (sorted.length - 1))];
}

function mean(arr) {
  if (!arr.length) return 0;
  return arr.reduce((a, b) => a + b, 0) / arr.length;
}

function processBatch() {
  const started = performance.now();
  state.batchN += 1;
  const batchId = `${Math.floor(Date.now() / 1000)}_${String(state.batchN).padStart(4, "0")}`;
  const rows = buildBatch();
  const checks = runChecks(rows);
  const score = aggregate(checks);
  const latency = performance.now() - started;

  state.batches_total += 1;
  if (score.batch_status === "pass") state.batches_passed += 1;
  else state.batches_failed += 1;
  state.rows_total += rows.length;
  state.latencies.unshift(latency);
  if (state.latencies.length > 200) state.latencies.length = 200;
  state.pass_rates.unshift(score.pass_rate_pct);
  if (state.pass_rates.length > 100) state.pass_rates.length = 100;

  for (const c of checks) {
    const bucket = state.checks[c.name];
    bucket.total += 1;
    bucket[c.status] += 1;
  }

  const run = {
    batch_id: batchId,
    checked_at: new Date().toISOString(),
    row_count: rows.length,
    latency_ms: Number(latency.toFixed(2)),
    score,
    checks,
  };
  state.runs.unshift(run);
  if (state.runs.length > FEED_RUNS) state.runs.length = FEED_RUNS;

  for (const c of checks) {
    if (c.status !== "fail") continue;
    state.failures.unshift({
      batch_id: batchId,
      check: c.name,
      message: c.message,
      metric: c.metric,
      offenders: (c.offenders || []).slice(0, 3),
      checked_at: run.checked_at,
    });
  }
  if (state.failures.length > FEED_FAILS) state.failures.length = FEED_FAILS;
}

function render() {
  const statusEl = document.getElementById("status");
  const ordered = [...state.latencies].sort((a, b) => a - b);
  const avgPass = Number(mean(state.pass_rates).toFixed(2));

  document.getElementById("batchesTotal").textContent = state.batches_total.toLocaleString();
  document.getElementById("rowsTotal").textContent = state.rows_total.toLocaleString();
  document.getElementById("passRate").textContent = `${avgPass}%`;
  document.getElementById("batchesFailed").textContent = state.batches_failed.toLocaleString();
  document.getElementById("p50").textContent = `${percentile(ordered, 0.5).toFixed(2)} ms`;
  document.getElementById("p95").textContent = `${percentile(ordered, 0.95).toFixed(2)} ms`;

  document.getElementById("checkCards").innerHTML = CHECK_ORDER.map((name) => {
    const c = state.checks[name];
    const rate = c.total ? Number(((c.pass / c.total) * 100).toFixed(2)) : 0;
    const tone = c.fail > 0 ? "fail" : c.total > 0 ? "pass" : "";
    return `<article class="check-card ${tone}">
      <div class="name">${name}</div>
      <div class="rate">${rate}%</div>
      <div class="meta">${c.pass} pass · ${c.fail} fail</div>
    </article>`;
  }).join("");

  document.getElementById("runsBody").innerHTML = state.runs
    .slice(0, 20)
    .map((run) => {
      const status = run.score.batch_status;
      return `<tr class="${status === "fail" ? "fail" : ""}">
        <td>${run.batch_id}</td>
        <td>${run.row_count}</td>
        <td>${run.score.pass_rate_pct}%</td>
        <td><span class="badge ${status}">${status}</span></td>
        <td>${Number(run.latency_ms).toFixed(1)} ms</td>
      </tr>`;
    })
    .join("");

  document.getElementById("failList").innerHTML = state.failures.length
    ? state.failures
        .slice(0, 40)
        .map((f) => {
          const offenders = (f.offenders || []).length ? `<br/>sample: ${f.offenders.length} row(s)` : "";
          return `<li>
            <span class="fail-icon">${alertIcon}</span>
            <div>
              <div class="title">${f.check} · batch ${f.batch_id}</div>
              <div class="meta">${f.message}${offenders}</div>
            </div>
          </li>`;
        })
        .join("")
    : `<li><span class="fail-icon">${alertIcon}</span><div class="meta">Waiting for failed checks…</div></li>`;

  document.getElementById("statusText").textContent = "live (simulated)";
  statusEl.classList.add("ok");
}

// Seed a few batches so the UI isn't empty on first paint
for (let i = 0; i < 3; i += 1) processBatch();
render();
setInterval(() => {
  processBatch();
  render();
}, BATCH_INTERVAL_MS);

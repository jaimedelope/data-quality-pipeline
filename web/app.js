/** In-browser simulation of the Docker data-quality pipeline. */

const CATALOG = Array.from({ length: 40 }, (_, i) => `sku_${String(i + 1).padStart(3, "0")}`);
const CURRENCIES = ["EUR", "USD", "GBP"];
const STATUSES = ["pending", "paid", "shipped", "cancelled"];

const state = {
  runsTotal: 0,
  rowsTotal: 0,
  failedTotal: 0,
  durations: [],
  passRates: [],
  latestChecks: [],
  runs: [],
  failures: [],
};

function uuid() {
  return crypto.randomUUID ? crypto.randomUUID() : `b-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function cleanOrder(now) {
  return {
    order_id: uuid(),
    user_id: `user_${String(1 + Math.floor(Math.random() * 80)).padStart(3, "0")}`,
    product_id: CATALOG[Math.floor(Math.random() * CATALOG.length)],
    amount: +(5 + Math.random() * 395).toFixed(2),
    currency: CURRENCIES[Math.floor(Math.random() * CURRENCIES.length)],
    status: STATUSES[Math.floor(Math.random() * STATUSES.length)],
    event_time: new Date(now.getTime() - Math.random() * 120000).toISOString(),
  };
}

function injectDefect(order, now) {
  const kinds = [
    "null_user",
    "null_amount",
    "negative_amount",
    "bad_currency",
    "bad_status",
    "future_time",
    "stale_time",
    "orphan_product",
    "duplicate_id",
  ];
  const kind = kinds[Math.floor(Math.random() * kinds.length)];
  order.injected_defect = kind;
  if (kind === "null_user") order.user_id = null;
  if (kind === "null_amount") order.amount = null;
  if (kind === "negative_amount") order.amount = +(-1 - Math.random() * 200).toFixed(2);
  if (kind === "bad_currency") order.currency = "XXX";
  if (kind === "bad_status") order.status = "unknown";
  if (kind === "future_time") order.event_time = new Date(now.getTime() + 3600000).toISOString();
  if (kind === "stale_time") order.event_time = new Date(now.getTime() - 20 * 3600000).toISOString();
  if (kind === "orphan_product") order.product_id = "sku_missing_999";
  if (kind === "duplicate_id") order._dup = true;
  return order;
}

function makeBatch() {
  const now = new Date();
  let size = 100;
  if (Math.random() < 0.1) size = Math.random() < 0.5 ? 18 : 280;
  const rows = [];
  for (let i = 0; i < size; i++) {
    let row = cleanOrder(now);
    if (Math.random() < 0.12) row = injectDefect(row, now);
    rows.push(row);
  }
  const dups = rows.filter((r) => r._dup);
  if (dups.length && rows.length > 1) {
    dups[0].order_id = rows[0].order_id;
    dups.forEach((r) => delete r._dup);
  }
  return { batch_id: uuid(), produced_at: now.toISOString(), rows, catalog: CATALOG };
}

function runChecks(batch) {
  const t0 = performance.now();
  const rows = batch.rows;
  const now = Date.now();
  const catalog = new Set(batch.catalog);

  const completenessOff = [];
  let nullCells = 0;
  const critical = ["user_id", "amount", "currency", "order_id"];
  rows.forEach((r) => {
    critical.forEach((c) => {
      if (r[c] == null || r[c] === "") {
        nullCells += 1;
        if (completenessOff.length < 5) completenessOff.push({ order_id: r.order_id, field: c });
      }
    });
  });
  const nullRate = nullCells / Math.max(rows.length * critical.length, 1);

  const seen = new Map();
  rows.forEach((r) => {
    if (!r.order_id) return;
    seen.set(r.order_id, (seen.get(r.order_id) || 0) + 1);
  });
  const dups = [...seen.entries()].filter(([, n]) => n > 1).map(([id]) => id);

  let invalid = 0;
  const validityOff = [];
  rows.forEach((r) => {
    const issues = [];
    if (r.amount != null && Number(r.amount) <= 0) issues.push("amount<=0");
    if (r.currency != null && !CURRENCIES.includes(r.currency)) issues.push("bad_currency");
    if (r.status != null && !STATUSES.includes(r.status)) issues.push("bad_status");
    if (issues.length) {
      invalid += 1;
      if (validityOff.length < 5) validityOff.push({ order_id: r.order_id, issue: issues.join(",") });
    }
  });

  let stale = 0;
  const freshOff = [];
  rows.forEach((r) => {
    const ts = Date.parse(r.event_time);
    if (Number.isNaN(ts)) {
      stale += 1;
      return;
    }
    const ageMin = (now - ts) / 60000;
    if (ageMin < -1 || ageMin > 180) {
      stale += 1;
      if (freshOff.length < 5) freshOff.push({ order_id: r.order_id, issue: ageMin < 0 ? "future" : "stale" });
    }
  });

  let orphans = 0;
  const refOff = [];
  rows.forEach((r) => {
    if (!catalog.has(r.product_id)) {
      orphans += 1;
      if (refOff.length < 5) refOff.push({ order_id: r.order_id, product_id: r.product_id });
    }
  });

  const volumePass = rows.length >= 40 && rows.length <= 160;
  const checks = [
    { name: "schema", passed: true, metric: 1, detail: "All required columns present", offenders: [] },
    {
      name: "completeness",
      passed: nullRate <= 0.03,
      metric: +(1 - nullRate).toFixed(4),
      detail: `Null rate on critical fields: ${(nullRate * 100).toFixed(2)}%`,
      offenders: completenessOff,
    },
    {
      name: "uniqueness",
      passed: dups.length === 0,
      metric: +(1 - dups.length / Math.max(seen.size, 1)).toFixed(4),
      detail: dups.length ? `${dups.length} duplicate order_id values` : "order_id unique",
      offenders: dups.slice(0, 5).map((id) => ({ order_id: id, issue: "duplicate" })),
    },
    {
      name: "validity",
      passed: invalid / rows.length <= 0.05,
      metric: +(1 - invalid / rows.length).toFixed(4),
      detail: `Invalid rows: ${invalid}/${rows.length}`,
      offenders: validityOff,
    },
    {
      name: "freshness",
      passed: stale / rows.length <= 0.05,
      metric: +(1 - stale / rows.length).toFixed(4),
      detail: `Stale/future timestamps: ${stale}/${rows.length}`,
      offenders: freshOff,
    },
    {
      name: "referential",
      passed: orphans / rows.length <= 0.02,
      metric: +(1 - orphans / rows.length).toFixed(4),
      detail: `Orphan product_id rows: ${orphans}/${rows.length}`,
      offenders: refOff,
    },
    {
      name: "volume",
      passed: volumePass,
      metric: rows.length,
      detail: volumePass
        ? `Batch size ${rows.length} within [40, 160]`
        : `Batch size ${rows.length} outside [40, 160]`,
      offenders: volumePass ? [] : [{ issue: "volume_anomaly", row_count: rows.length }],
    },
  ];

  const passed = checks.filter((c) => c.passed).length;
  const failed = checks.filter((c) => !c.passed);
  const duration = +(performance.now() - t0).toFixed(2);
  return {
    batch_id: batch.batch_id,
    checked_at: new Date().toISOString(),
    row_count: rows.length,
    checks_passed: passed,
    checks_failed: failed.length,
    pass_rate_pct: +((passed / checks.length) * 100).toFixed(1),
    duration_ms: duration,
    checks,
    failed_checks: failed,
  };
}

function render() {
  document.getElementById("runsTotal").textContent = state.runsTotal;
  document.getElementById("rowsTotal").textContent = state.rowsTotal;
  document.getElementById("passRate").textContent = `${state.passRates[0] ?? 0}%`;
  const avgPass = state.passRates.length
    ? state.passRates.reduce((a, b) => a + b, 0) / state.passRates.length
    : 0;
  document.getElementById("avgPass").textContent = `${avgPass.toFixed(1)}%`;
  document.getElementById("failedTotal").textContent = state.failedTotal;
  const avgDur = state.durations.length
    ? state.durations.reduce((a, b) => a + b, 0) / state.durations.length
    : 0;
  document.getElementById("avgDuration").textContent = `${avgDur.toFixed(2)} ms`;

  const checksBody = document.getElementById("checksBody");
  checksBody.innerHTML = state.latestChecks
    .map(
      (c) => `<tr class="${c.passed ? "" : "anomaly"}"><td>${c.name}</td><td>${
        c.passed ? "PASS" : "FAIL"
      }</td><td>${c.metric}</td><td>${c.detail}</td></tr>`
    )
    .join("");

  const failList = document.getElementById("failList");
  if (!state.failures.length) {
    failList.innerHTML = `<li style="background:transparent;border-style:dashed"><div class="meta">No failures yet.</div></li>`;
  } else {
    failList.innerHTML = state.failures
      .map(
        (f) => `<li><div class="anom-icon">!</div><div><div class="title">${f.name}</div><div class="meta">${f.detail}</div></div></li>`
      )
      .join("");
  }

  document.getElementById("runsBody").innerHTML = state.runs
    .map((r) => {
      const when = (r.checked_at || "").replace("T", " ").slice(0, 19);
      return `<tr class="${r.checks_failed ? "anomaly" : ""}"><td>${r.batch_id.slice(
        0,
        8
      )}</td><td>${r.row_count}</td><td>${r.pass_rate_pct}%</td><td>${
        (r.failed_names || []).join(", ") || "—"
      }</td><td>${r.duration_ms} ms</td><td>${when}</td></tr>`;
    })
    .join("");
}

function tick() {
  const result = runChecks(makeBatch());
  state.runsTotal += 1;
  state.rowsTotal += result.row_count;
  state.failedTotal += result.checks_failed;
  state.durations.unshift(result.duration_ms);
  state.durations = state.durations.slice(0, 40);
  state.passRates.unshift(result.pass_rate_pct);
  state.passRates = state.passRates.slice(0, 40);
  state.latestChecks = result.checks;
  state.runs.unshift({
    batch_id: result.batch_id,
    checked_at: result.checked_at,
    row_count: result.row_count,
    pass_rate_pct: result.pass_rate_pct,
    checks_failed: result.checks_failed,
    duration_ms: result.duration_ms,
    failed_names: result.failed_checks.map((c) => c.name),
  });
  state.runs = state.runs.slice(0, 20);
  result.failed_checks.forEach((c) => {
    state.failures.unshift({ name: c.name, detail: c.detail });
  });
  state.failures = state.failures.slice(0, 20);
  render();
}

tick();
setInterval(tick, 3500);

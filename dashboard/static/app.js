let state = { contracts: [], datasets: [], recent: [], selected: null };

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} ${res.status}`);
  return res.json();
}

function renderDatasets() {
  const list = document.getElementById("datasetList");
  if (!state.datasets.length) {
    list.innerHTML = `<p class="empty" style="padding:8px">No runs yet</p>`;
    return;
  }
  list.innerHTML = state.datasets
    .map((d) => {
      const active = d.dataset === state.selected ? "active" : "";
      const latest = d.latest ? `${d.latest.pass_rate}%` : "—";
      return `<button class="dataset-btn ${active}" data-ds="${d.dataset}">
        <span class="name">${d.dataset}</span>
        <span class="meta">${latest} · ${d.breaches} breaches</span>
      </button>`;
    })
    .join("");
  list.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.selected = btn.dataset.ds;
      render();
    });
  });
}

function renderScorecard() {
  const wrap = document.getElementById("scorecard");
  const title = document.getElementById("datasetTitle");
  const ds = state.datasets.find((d) => d.dataset === state.selected);
  const contract = state.contracts.find((c) => c.dataset === state.selected);

  if (!ds || !ds.latest) {
    title.textContent = state.selected || "Select a dataset";
    wrap.innerHTML = `<p class="empty">No scorecard yet for this dataset.</p>`;
    document.getElementById("checksWrap").innerHTML = "—";
    document.getElementById("contractYaml").textContent = contract?.yaml || "—";
    return;
  }

  title.textContent = ds.dataset;
  const p = ds.latest.pass_rate;
  const slaClass = ds.latest.sla_met ? "met" : "breach";
  const slaText = ds.latest.sla_met ? "SLA met" : "SLA breach";
  wrap.innerHTML = `
    <div class="score-row">
      <div class="score-ring" style="--p:${p}"><strong>${p}%</strong></div>
      <div>
        <div class="sla-pill ${slaClass}">${slaText} · threshold ${ds.latest.sla_pass_rate}%</div>
        <p style="margin:10px 0 0;color:var(--muted);font-size:14px;line-height:1.5">
          Owner: <strong>${contract?.owner || "—"}</strong><br/>
          Latest file <span class="mono">${ds.latest.file_name}</span> ·
          ${ds.latest.row_count} rows ·
          ${ds.latest.checks_passed}/${ds.latest.checks_total} checks ·
          ${ds.latest.duration_ms} ms
        </p>
        <p style="margin:8px 0 0;color:var(--muted);font-size:13px">${contract?.description || ""}</p>
      </div>
    </div>`;

  const checks = ds.latest_checks || [];
  document.getElementById("checksWrap").innerHTML = `
    <table class="checks">
      <thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead>
      <tbody>
        ${checks
          .map(
            (c) => `<tr>
              <td class="mono">${c.check_name}</td>
              <td><span class="tag ${c.passed ? "pass" : "fail"}">${c.passed ? "PASS" : "FAIL"}</span></td>
              <td>${c.detail}</td>
            </tr>`
          )
          .join("")}
      </tbody>
    </table>`;

  document.getElementById("contractYaml").textContent = contract?.yaml || "—";
}

function renderRuns() {
  const wrap = document.getElementById("runsWrap");
  if (!state.recent.length) {
    wrap.innerHTML = `<p class="empty">No runs yet</p>`;
    return;
  }
  wrap.innerHTML = `
    <table class="runs">
      <thead><tr><th>Dataset</th><th>File</th><th>Pass</th><th>SLA</th><th>Rows</th><th>Latency</th><th>When</th></tr></thead>
      <tbody>
        ${state.recent
          .map(
            (r) => `<tr>
              <td>${r.dataset}</td>
              <td class="mono">${r.file_name}</td>
              <td>${r.pass_rate}%</td>
              <td><span class="tag ${r.sla_met ? "pass" : "fail"}">${r.sla_met ? "MET" : "BREACH"}</span></td>
              <td>${r.row_count}</td>
              <td>${r.duration_ms} ms</td>
              <td class="mono">${(r.finished_at || "").replace("T", " ").slice(0, 19)}</td>
            </tr>`
          )
          .join("")}
      </tbody>
    </table>`;
}

function render() {
  renderDatasets();
  renderScorecard();
  renderRuns();
}

async function tick() {
  try {
    const [contracts, scorecards] = await Promise.all([
      fetchJson("/api/contracts"),
      fetchJson("/api/scorecards"),
    ]);
    state.contracts = contracts.contracts || [];
    state.datasets = scorecards.datasets || [];
    state.recent = scorecards.recent_runs || [];
    if (!state.selected && state.datasets.length) state.selected = state.datasets[0].dataset;
    document.getElementById("sumRuns").textContent = scorecards.summary.runs;
    document.getElementById("sumBreaches").textContent = scorecards.summary.breaches;
    document.getElementById("sumPass").textContent = `${scorecards.summary.avg_pass}%`;
    const status = document.getElementById("status");
    status.textContent = scorecards.summary.runs ? "batch validator live" : "waiting for landing files…";
    status.classList.toggle("live", scorecards.summary.runs > 0);
    render();
  } catch (err) {
    document.getElementById("status").textContent = "disconnected";
    console.error(err);
  }
}

tick();
setInterval(tick, 3000);

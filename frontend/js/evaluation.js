let evalMode = "deterministic_eval";

document.addEventListener("DOMContentLoaded", async () => {
  qs("#runBtn").addEventListener("click", runEval);
  qs("#modeSelect").addEventListener("change", e => { evalMode = e.target.value; });
  try {
    const cases = await api("/api/evaluation/cases");
    renderCases(cases);
  } catch (e) {
    qs("#cases").innerHTML = notice(e.message, "error");
  }
});

function renderCases(cases) {
  let rows = cases.map(c =>
    "<tr>"
    + "<td>" + c.case_id + "</td>"
    + "<td>" + esc(c.name) + "</td>"
    + "<td>" + esc(c.expected_status || c.description || "") + "</td>"
    + "<td>" + esc(c.notes || "") + "</td>"
    + "</tr>"
  ).join("");
  qs("#cases").innerHTML =
    "<table><thead><tr><th>#</th><th>Case</th><th>Expected</th><th>Notes</th></tr></thead>"
    + "<tbody>" + rows + "</tbody></table>";
}

async function runEval() {
  const btn = qs("#runBtn");
  btn.disabled = true;
  btn.textContent = "Running…";
  qs("#results").innerHTML = '<div class="notice info"><span class="spinner" style="border-color:rgba(30,64,175,.4);border-top-color:#1e40af"></span>Executing evaluation — please wait…</div>';

  try {
    const data = await api("/api/evaluation/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: evalMode }),
    });
    renderResults(data);
  } catch (e) {
    qs("#results").innerHTML = notice(e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Run Evaluation";
  }
}

function renderResults(data) {
  const s = data.summary;
  const b = data.baseline;
  let html = '<div class="card">'
    + '<h2>Summary — extractor: <span class="badge neutral">' + esc(data.extractor) + '</span></h2>'
    + '<table>'
    + metric("Accuracy", pct(s.accuracy), s.correct + "/" + s.total)
    + metric("Exception Detection", pct(s.exception_detection))
    + metric("False Approvals", s.false_approvals, s.false_approvals === 0 ? "good" : "")
    + metric("False Exceptions", s.false_exceptions)
    + metric("Extraction Accuracy", s.extraction_accuracy != null ? pct(s.extraction_accuracy) : "n/a", s.extraction_fields_correct + "/" + s.extraction_fields_scored)
    + metric("Avg Processing", s.avg_processing_time_ms + " ms")
    + metric("Human Intervention", pct(s.human_intervention_percent / 100), s.human_intervention + "/" + s.total)
    + metric("System Failures", s.system_failures, s.system_failures === 0 ? "good" : "check cases")
    + '</table></div>';

  html += '<div class="card"><h2>Per-case Results</h2><table><thead><tr><th>#</th><th>Name</th><th>Expected</th><th>Actual</th><th>Correct</th><th>Time</th></tr></thead><tbody>';
  for (const c of data.cases) {
    const cls = c.correct_classification ? "ok" : "bad";
    html += '<tr>'
      + '<td>' + c.case_id + '</td>'
      + '<td>' + esc(c.name) + '</td>'
      + '<td>' + badge(c.expected_status) + '</td>'
      + '<td>' + badge(c.actual_status) + '</td>'
      + '<td class="' + cls + '">' + (c.correct_classification ? "OK" : "FAIL") + '</td>'
      + '<td>' + Math.round(c.processing_time_ms) + ' ms</td>'
      + '</tr>';
  }
  html += "</tbody></table></div>";

  // baseline
  html += '<div class="card"><h2>' + esc(b.label) + '</h2>'
    + '<p style="font-size:13px;color:#6b7280;margin:0 0 14px">' + esc(b.disclaimer) + '</p>'
    + '<table><tr><td>Total manual (synthetic)</td><td><strong>' + b.totals.total_manual_seconds + 's</strong></td></tr>'
    + '<tr><td>Total system</td><td><strong>' + b.totals.total_system_ms + ' ms</strong></td></tr>'
    + '<tr><td>Synthetic speed-up</td><td><strong>' + (b.totals.system_speedup_x_sec || "n/a") + 'x</strong></td></tr>'
    + '<tr><td>Manual human intervention</td><td>' + b.totals.manual_human_intervention_percent + '%</td></tr>'
    + '<tr><td>System human intervention</td><td>' + b.totals.system_human_intervention_percent + '%</td></tr>'
    + '</table></div>';

  qs("#results").innerHTML = html;
}

function pct(v) { return (v * 100).toFixed(2) + "%"; }
function metric(label, value, sub) {
  return "<tr><td><strong>" + esc(label) + "</strong></td><td>" + esc(value) + "</td>"
    + (sub ? "<td style='color:#6b7280;font-size:12px'>" + esc(sub) + "</td></tr>" : "<td></td></tr>");
}
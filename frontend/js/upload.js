// new.html: upload two PDFs, run compare, show result. Also used as a
// detail viewer via ?id=<comparison_id>.

const params = new URLSearchParams(location.search);
const loadedId = params.get("id");

async function onAnalyze() {
  const poFile = qs("#poFile").files[0];
  const invFile = qs("#invFile").files[0];
  const btn = qs("#compareBtn");
  const statusEl = qs("#statusText");

  if (!poFile || !invFile) {
    qs("#errors").innerHTML = notice("Please choose both a Purchase Order PDF and an Invoice PDF.", "error");
    return;
  }
  qs("#errors").innerHTML = "";
  btn.disabled = true;
  statusEl.className = "";
  statusEl.innerHTML = '<span class="spinner" style="border-color:rgba(37,99,235,.4);border-top-color:#2563eb"></span>Processing documents — this can take up to a minute…';
  qs("#result").innerHTML = "";

  const fd = new FormData();
  fd.append("po", poFile);
  fd.append("invoice", invFile);

  try {
    const data = await api("/api/analyze", { method: "POST", body: fd });
    statusEl.innerHTML = "";
    const detail = await api("/api/comparisons/" + data.comparison_id);
    renderResult(detail);
  } catch (e) {
    statusEl.innerHTML = "";
    qs("#errors").innerHTML = notice(e.message, "error");
  } finally {
    btn.disabled = false;
  }
}

function statusCell(c) {
  if (c.status === "match" || c.status === "converted") return '<span class="ok">✓</span>';
  if (c.status === "mismatch") return '<span class="bad">✗</span>';
  return '<span class="maybe">?</span>';
}

function renderResult(d) {
  const host = qs("#result");
  const docs = d.documents || {};
  const isInvalid = d.result === "INVALID DOCUMENT";
  const showReview = d.result === "EXCEPTION" && d.review_status === "pending";

  let html = '<div class="result-header">' + badge(d.result) + '</div>'
    + '<div class="result-meta">'
    + 'Compared ' + esc(docs.po_filename || "PO") + ' vs ' + esc(docs.invoice_filename || "Invoice")
    + ' · extractor: ' + esc(d.extractor || "—")
    + ' · ' + Math.round(d.processing_time_ms || 0) + ' ms'
    + '</div>';

  if (d.status_reason) {
    html += '<div class="notice ' + (isInvalid ? "error" : "info") + '">' + esc(d.status_reason) + "</div>";
  }

  // extracted document data
  const po = d.po || {};
  const inv = d.invoice || {};
  html += '<div class="card"><h2>Extracted Document Data</h2>'
    + '<table><thead><tr><th>Field</th><th>Purchase Order</th><th>Invoice</th></tr></thead><tbody>'
    + row("PO Number", po.po_number, inv.po_number)
    + row("Invoice Number", po.invoice_number, inv.invoice_number)
    + row("Vendor", po.vendor_name, inv.vendor_name)
    + row("Currency", po.currency, inv.currency)
    + row("Subtotal", vig(po.subtotal), vig(inv.subtotal))
    + row("Tax", vig(po.tax), vig(inv.tax))
    + row("Total", vig(po.total), vig(inv.total))
    + row("Line items", (po.items || []).length, (inv.items || []).length)
    + '</tbody></table>';

  if ((po.items || []).length || (inv.items || []).length) {
    const count = Math.max((po.items || []).length, (inv.items || []).length);
    let itemRows = "";
    for (let i = 0; i < count; i++) {
      const a = po.items && po.items[i] ? po.items[i] : {};
      const b = inv.items && inv.items[i] ? inv.items[i] : {};
      itemRows += '<tr>'
        + '<td>' + esc(a.description || "—") + '</td><td>' + vig(a.quantity) + '</td><td>' + fmtMoney(a.unit_price) + '</td><td>' + fmtMoney(a.total_price) + '</td>'
        + '<td>' + esc(b.description || "—") + '</td><td>' + vig(b.quantity) + '</td><td>' + fmtMoney(b.unit_price) + '</td><td>' + fmtMoney(b.total_price) + '</td>'
        + '</tr>';
    }
    html += '<h2 style="margin:20px 0 10px;font-size:15px">Line items (PO → Invoice)</h2>'
      + '<table><thead><tr>'
      + '<th>PO Description</th><th>PO Qty</th><th>PO Unit</th><th>PO Amount</th>'
      + '<th>Inv Description</th><th>Inv Qty</th><th>Inv Unit</th><th>Inv Amount</th>'
      + '</tr></thead><tbody>' + itemRows + '</tbody></table>';
  }
  html += '</div>';

  // field-by-field comparison
  if (d.field_comparisons && d.field_comparisons.length) {
    html += '<div class="card"><h2>Field Comparison</h2><table><thead><tr>'
      + '<th>Field</th><th>PO</th><th>Invoice</th><th>Difference</th><th>Result</th></tr></thead><tbody>';
    for (const c of d.field_comparisons) {
      const fx = c.fx
        ? ' <em style="color:#2563eb;font-size:12px">converted at ' + esc(c.fx.rate) + ' ' + esc(c.fx.from_currency) + ' → ' + esc(c.fx.to_currency) + ' (' + esc(c.fx.date) + ')</em>'
        : "";
      html += '<tr><td>' + esc(c.field) + '</td>'
        + '<td>' + esc(disp(c.po_value)) + '</td>'
        + '<td>' + esc(disp(c.invoice_value)) + '</td>'
        + '<td>' + esc(disp(c.difference)) + '</td>'
        + '<td>' + statusCell(c) + fx + '</td></tr>';
    }
    html += '</tbody></table>'
      + '<div class="footer-note">Difference values for money are shown as absolute differences. '
      + '"?": value missing on one side. When currencies differ, invoice amounts are '
      + 'converted to the PO currency (rate/date/source shown per row).</div></div>';
  }

  // reasons
  if (d.reasons && d.reasons.length) {
    html += '<div class="card"><h2>Why</h2><ul style="margin:0;padding-left:20px">'
      + d.reasons.map(r => "<li>" + esc(r) + "</li>").join("")
      + "</ul></div>";
  }

  // actions
  let actions = "";
  if (showReview) {
    actions += '<a class="btn" href="review.html?id=' + d.comparison_id + '">Review Exception</a> ';
  }
  html += '<div class="card"><h2>Actions</h2>' + actions + "</div><div></div>";

  host.innerHTML = html;
}

function disp(v) {
  if (v === null || v === undefined || v === "") return "—";
  return v;
}
function vig(v) { // value or gap
  return v === null || v === undefined || v === "" ? "—" : fmtMoney(v);
}
function row(label, a, b) {
  return "<tr><td>" + esc(label) + "</td><td>" + esc(disp(a)) + "</td><td>" + esc(disp(b)) + "</td></tr>";
}

document.addEventListener("DOMContentLoaded", () => {
  qs("#compareBtn").addEventListener("click", onAnalyze);
  if (loadedId) {
    qs("#uploadSection").style.display = "none";
    qs("#title").textContent = "Comparison Result";
    qs("#subtitle").textContent = "Detailed view of a saved comparison.";
    (async () => {
      try {
        const d = await api("/api/comparisons/" + loadedId);
        renderResult(d);
      } catch (e) {
        qs("#result").innerHTML = notice(e.message, "error");
      }
    })();
  }
});
document.addEventListener("DOMContentLoaded", async () => {
  const params = new URLSearchParams(location.search);
  const id = params.get("id");
  if (!id) { qs("#content").innerHTML = notice("No comparison id provided.", "error"); return; }

  try {
    const detail = await api("/api/comparisons/" + id);
    renderHeader(detail);

    if (detail.review) {
      qs("#form").innerHTML = notice(
        "This comparison was already reviewed: <strong>" + esc(detail.review.decision) + "</strong>"
        + (detail.review.note ? " — " + esc(detail.review.note) : "")
        + (detail.review.created_at ? " (" + esc(detail.review.created_at) + ")" : ""),
        detail.review.decision === "approve" ? "success" : "info"
      );
      return;
    }

    renderEvidence(detail);

    qs("#decisionBtn").addEventListener("click", async () => {
      const decision = qs('input[name="decision"]:checked');
      if (!decision) { qs("#formNotice").innerHTML = notice("Choose Approve or Reject.", "error"); return; }
      const note = qs("#note").value.trim();
      const btn = qs("#decisionBtn");
      btn.disabled = true;
      btn.textContent = "Saving…";
      try {
        await api("/api/comparisons/" + id + "/review", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision: decision.value, note: note || null }),
        });
        qs("#formNotice").innerHTML = notice("Review saved successfully.", "success");
        qs("#form").style.display = "none";
      } catch (e) {
        btn.disabled = false;
        btn.textContent = "Submit Review";
        qs("#formNotice").innerHTML = notice(e.message, "error");
      }
    });
  } catch (e) {
    qs("#content").innerHTML = notice(e.message, "error");
  }
});

function renderHeader(d) {
  qs("#header").innerHTML =
    badge(d.result)
    + '<div class="result-meta" style="margin-top:8px">'
    + 'PO <strong>' + esc(d.po.po_number || "—") + '</strong> · Invoice <strong>' + esc(d.invoice.invoice_number || "—") + '</strong>'
    + ' · ' + esc(d.po.vendor_name || "—")
    + '</div>';
}

function renderEvidence(d) {
  const po = d.po || {};
  const inv = d.invoice || {};
  let html = '<div class="card"><h2>PO and Invoice Data</h2>'
    + '<table><thead><tr><th>Field</th><th>Purchase Order</th><th>Invoice</th></tr></thead><tbody>'
    + "<tr><td>PO Number</td><td>" + esc(disp(po.po_number)) + "</td><td>" + esc(disp(inv.po_number)) + "</td></tr>"
    + "<tr><td>Invoice Number</td><td>" + esc(disp(po.invoice_number)) + "</td><td>" + esc(disp(inv.invoice_number)) + "</td></tr>"
    + "<tr><td>Vendor</td><td>" + esc(disp(po.vendor_name)) + "</td><td>" + esc(disp(inv.vendor_name)) + "</td></tr>"
    + "<tr><td>Currency</td><td>" + esc(disp(po.currency)) + "</td><td>" + esc(disp(inv.currency)) + "</td></tr>"
    + "<tr><td>Subtotal</td><td>" + esc(vig(po.subtotal)) + "</td><td>" + esc(vig(inv.subtotal)) + "</td></tr>"
    + "<tr><td>Tax</td><td>" + esc(vig(po.tax)) + "</td><td>" + esc(vig(inv.tax)) + "</td></tr>"
    + "<tr><td>Total</td><td>" + esc(vig(po.total)) + "</td><td>" + esc(vig(inv.total)) + "</td></tr>"
    + "</tbody></table></div>";

  if (d.reasons && d.reasons.length) {
    html += '<div class="card"><h2>Reason</h2><ul style="margin:0;padding-left:20px">'
      + d.reasons.map(r => "<li>" + esc(r) + "</li>").join("") + "</ul></div>";
  }

  // field comparison evidence
  if (d.field_comparisons && d.field_comparisons.length) {
    html += '<div class="card"><h2>Field Evidence</h2><table><thead><tr>'
      + '<th>Field</th><th>PO</th><th>Invoice</th><th>Diff</th><th>Result</th></tr></thead><tbody>';
    for (const c of d.field_comparisons) {
      const cls = (c.status === "match" || c.status === "converted") ? "ok" : c.status === "mismatch" ? "bad" : "maybe";
      const fx = c.fx
        ? ' <span style="color:#2563eb;font-size:11px">converted at ' + esc(c.fx.rate) + ' ' + esc(c.fx.from_currency) + ' → ' + esc(c.fx.to_currency) + ' (' + esc(c.fx.date) + ')</span>'
        : "";
      html += '<tr><td>' + esc(c.field) + '</td><td>' + esc(disp(c.po_value)) + '</td><td>' + esc(disp(c.invoice_value)) + '</td><td>' + esc(disp(c.difference)) + '</td><td class="' + cls + '">' + esc(c.status) + fx + '</td></tr>';
    }
    html += "</tbody></table></div>";
  }
  qs("#evidence").innerHTML = html;
}

function disp(v) { return v === null || v === undefined || v === "" ? "—" : v; }
function vig(v) {
  if (v === null || v === undefined || v === "") return "—";
  return Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
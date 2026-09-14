document.addEventListener("DOMContentLoaded", async () => {
  const resultSel = qs("#resultFilter");
  const reviewSel = qs("#reviewFilter");

  async function load() {
    const q = new URLSearchParams();
    if (resultSel.value) q.set("result", resultSel.value);
    if (reviewSel.value) q.set("review_status", reviewSel.value);
    try {
      const data = await api("/api/comparisons?" + q.toString());
      render(data.comparisons);
    } catch (e) {
      qs("#table").innerHTML = notice(e.message, "error");
    }
  }

  function render(items) {
    if (!items.length) {
      qs("#table").innerHTML = '<div class="empty">No comparisons match these filters.</div>';
      return;
    }
    qs("#table").innerHTML =
      "<table><thead><tr>"
      + "<th>Date</th><th>PO</th><th>Invoice</th><th>Vendor</th><th>Result</th><th>Review</th>"
      + "</tr></thead><tbody>"
      + items.map(r =>
          '<tr class="clickable" onclick="location.href=\'new.html?id=' + r.comparison_id + '\'">'
          + "<td>" + esc(r.date) + "</td>"
          + "<td>" + esc(disp(r.po_number)) + "</td>"
          + "<td>" + esc(disp(r.invoice_number)) + "</td>"
          + "<td>" + esc(disp(r.vendor)) + "</td>"
          + "<td>" + badge(r.result) + "</td>"
          + "<td>" + badgeReview(r.review_status) + "</td>"
          + "</tr>"
        ).join("")
      + "</tbody></table>";
  }

  function badgeReview(s) {
    if (s === "approved") return '<span class="badge approve">Approved</span>';
    if (s === "rejected") return '<span class="badge reject">Rejected</span>';
    if (s === "pending") return '<span class="badge pending">Pending</span>';
    return '<span class="badge neutral">—</span>';
  }

  function disp(v) { return v === null || v === undefined ? "—" : v; }

  resultSel.addEventListener("change", load);
  reviewSel.addEventListener("change", load);
  load();
});
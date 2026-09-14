async function api(path, opts) {
  const res = await fetch(path, opts);
  let body = null;
  try { body = await res.json(); } catch (e) { /* non-JSON */ }
  if (!res.ok) {
    const msg = (body && (body.detail || body.message))
      || ("Request failed (" + res.status + "). Please try again.");
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return body;
}

function qs(sel) { return document.querySelector(sel); }
function qsa(sel) { return Array.from(document.querySelectorAll(sel)); }

function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtMoney(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return esc(v);
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function badge(result) {
  if (result === "INVALID DOCUMENT") return '<span class="badge INVALID">Invalid Document</span>';
  if (result === "MATCH") return '<span class="badge MATCH">Match</span>';
  if (result === "EXCEPTION") return '<span class="badge EXCEPTION">Exception</span>';
  return '<span class="badge neutral">' + esc(result || "—") + '</span>';
}

function notice(msg, type) {
  return '<div class="notice ' + (type || "info") + '">' + esc(msg) + "</div>";
}

// Shared page header / navigation (kept in JS to avoid repetition).
function renderNav(active) {
  const links = [
    ["index.html", "Dashboard"],
    ["new.html", "New Comparison"],
    ["history.html", "History"],
    ["evaluation.html", "Evaluation"],
  ];
  return '<header><div class="brand">InvoiceMatch <span>AI OS</span></div>'
    + '<nav>' + links.map(([href, label]) =>
        '<a href="' + href + '" class="' + (active === href ? "active" : "") + '">' + label + "</a>"
      ).join("") + "</nav></header>";
}
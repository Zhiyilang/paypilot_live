"""AG Grid audit console: the whole catalog scored against the spending policy.

Why a grid at all? Because a single-agent demo proves one decision; a grid
proves the *policy surface*. Every product is run through the same
deterministic policy engine that gates real money, so a reviewer can sort,
filter and poke at hundreds of decisions at once and see that the guardrails
hold everywhere - not just in the one scenario the demo happens to show.

The page is served with zero build step: AG Grid Community is pulled from a
CDN, and if the CDN is unreachable the same data is rendered as a plain HTML
table so the console never appears broken offline.
"""
import json

from .policy import Policy, check

# ---------------------------------------------------------------- data layer

def audit_rows(products, policy=None, daily_spent=0.0):
    """Run every product through the policy engine. Pure, deterministic."""
    policy = policy or Policy()
    rows = []
    for p in products:
        d = check(policy, p, daily_spent=daily_spent)
        rows.append({
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "merchant": p.merchant,
            "price": p.price,
            "rating": p.rating,
            "stock": p.stock,
            "tags": ", ".join(p.tags),
            "decision": d.action,          # allow | block | hold
            "rule": d.rule,
            "reason": d.detail,
            "evidence": list(d.evidence),
        })
    return rows


def summarize(rows):
    out = {"total": len(rows), "allow": 0, "block": 0, "hold": 0,
           "exposure": 0.0, "blocked_value": 0.0}
    for r in rows:
        out[r["decision"]] = out.get(r["decision"], 0) + 1
        if r["decision"] == "allow":
            out["exposure"] += r["price"]
        elif r["decision"] == "block":
            out["blocked_value"] += r["price"]
    out["exposure"] = round(out["exposure"], 2)
    out["blocked_value"] = round(out["blocked_value"], 2)
    return out


# ------------------------------------------------------------------- HTML/CSS

_HEAD = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PayPilot &middot; Policy Audit Grid</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ag-grid-community@32.3.3/styles/ag-grid.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ag-grid-community@32.3.3/styles/ag-theme-quartz.css">
<style>
:root{--blue:#4f7cff;--ink:#1a1f2b;--mut:#5a6472;--line:#e3e6ea;--bg:#f6f7f9;
      --ok:#0e9f6e;--warn:#b7791f;--bad:#e02424}
*{box-sizing:border-box}
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink);margin:0}
.wrap{max-width:1180px;margin:0 auto;padding:24px 20px 60px}
header h1{font-size:22px;margin:0}
header p{color:var(--mut);margin:6px 0 0;font-size:14px;max-width:760px}
.bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:16px 0 10px}
button{background:var(--blue);color:#fff;border:0;border-radius:8px;padding:8px 14px;font-size:13px;cursor:pointer}
button.ghost{background:#fff;color:var(--ink);border:1px solid var(--line)}
input[type=range]{width:190px}
label.ctl{font-size:13px;color:var(--mut);display:flex;gap:8px;align-items:center}
.kpis{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.kpi{background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:120px}
.kpi .n{font-size:20px;font-weight:700}
.kpi .l{font-size:11.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}
#grid{height:540px;width:100%}
.badge{display:inline-block;border-radius:6px;padding:1px 8px;font-size:11.5px;font-weight:700;color:#fff}
.b-allow{background:var(--ok)}.b-block{background:var(--bad)}.b-hold{background:var(--warn)}
.ev{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-top:12px;font-size:13px}
.ev h3{margin:0 0 6px;font-size:14px}
.ev ul{margin:0;padding-left:18px;color:var(--mut)}
table.fallback{border-collapse:collapse;width:100%;background:#fff;font-size:13px}
table.fallback th,table.fallback td{border-bottom:1px solid #eef0f3;padding:6px 9px;text-align:left}
.note{font-size:12.5px;color:var(--mut);margin-top:8px}
code{background:#eef1f5;border-radius:4px;padding:1px 5px;font-size:12.5px}
</style></head><body><div class="wrap">
<header><h1>Policy Audit Grid</h1>
<p>Every catalog item run through the same deterministic policy engine that gates
real money. Sort, filter and click any row to see the exact rule and evidence
behind the decision &mdash; and drag <em>already spent today</em> to watch the
daily-limit rule flip rows to BLOCK in real time.</p></header>
<div class="kpis">__KPIS__</div>
<div class="bar">
  <label class="ctl">already spent today
    <input type="range" id="spent" min="0" max="600" step="10" value="__SPENT__">
    <span id="spentVal">$__SPENT__</span>
  </label>
  <label class="ctl"><input type="checkbox" id="onlyBlocked"> show blocked only</label>
  <button class="ghost" id="csv">Export CSV</button>
  <button class="ghost" id="reset">Clear filters</button>
  <span class="note" id="mode"></span>
</div>
<div id="grid" class="ag-theme-quartz"></div>
<div class="ev" id="ev"><h3>Evidence</h3><p class="note">Click a row to see the rule and
field-level evidence that produced its decision.</p></div>
<p class="note">Data: <code>examples/catalog.csv</code> &middot; engine:
<code>paypilot.policy.check()</code> &middot; served by
<code>paypilot.grid</code>. No build step; AG Grid Community from CDN with an
offline HTML-table fallback.</p>
</div>
<script src="https://cdn.jsdelivr.net/npm/ag-grid-community@32.3.3/dist/ag-grid-community.min.js"></script>
<script>
const ROWS = __ROWS__;
const fmt = n => '$' + Number(n).toFixed(2);
function badge(d){ return '<span class="badge b-'+d+'">'+d.toUpperCase()+'</span>'; }
const COLS = [
  {field:'id', headerName:'ID', width:90, pinned:'left'},
  {field:'name', headerName:'Product', flex:2, minWidth:180},
  {field:'category', headerName:'Category', width:130},
  {field:'merchant', headerName:'Merchant', width:150},
  {field:'price', headerName:'Price', width:110, type:'numericColumn',
   valueFormatter:p=>fmt(p.value)},
  {field:'rating', headerName:'Rating', width:100, type:'numericColumn',
   valueFormatter:p=>Number(p.value).toFixed(1)},
  {field:'stock', headerName:'Stock', width:90, type:'numericColumn'},
  {field:'decision', headerName:'Decision', width:120,
   cellRenderer:p=>badge(p.value),
   comparator:(a,b,_,_)=>a.localeCompare(b)},
  {field:'rule', headerName:'Rule', width:190},
  {field:'reason', headerName:'Reason', flex:2, minWidth:220, tooltipField:'reason'}
];
const DEFAULTS = {sortable:true, resizable:true, filter:true, flex:1};
const OPTS = {
  rowData: ROWS,
  columnDefs: COLS,
  defaultColDef: DEFAULTS,
  pagination: true,
  paginationPageSize: 25,
  sideBar: {toolPanels:['filters','columns'], defaultToolPanel:false},
  rowClassRules: {
    'row-block': p => p.data.decision === 'block',
    'row-hold':  p => p.data.decision === 'hold'
  },
  onRowClicked: e => {
    const d = e.data;
    document.getElementById('ev').innerHTML =
      '<h3>' + d.name + ' &mdash; ' + badge(d.decision) + ' <code>' + d.rule + '</code></h3>' +
      '<div>' + d.reason + '</div>' +
      '<ul>' + (d.evidence||[]).map(x => '<li>'+x+'</li>').join('') + '</ul>';
  }
};
let api = null;
function drawFallback(){
  const cols = ['id','name','category','merchant','price','rating','decision','rule'];
  let h = '<table class="fallback"><tr>' +
    cols.map(c=>'<th>'+c+'</th>').join('') + '</tr>';
  ROWS.forEach(r => {
    h += '<tr>' + cols.map(c =>
      '<td>' + (c==='decision' ? badge(r[c]) :
                (c==='price' ? fmt(r[c]) : r[c])) + '</td>').join('') + '</tr>';
  });
  document.getElementById('grid').innerHTML = h + '</table>';
  document.getElementById('mode').textContent = 'offline fallback (AG Grid CDN unreachable)';
}
if (typeof agGrid !== 'undefined' && agGrid.createGrid) {
  api = agGrid.createGrid(document.getElementById('grid'), OPTS);
  document.getElementById('mode').textContent = 'AG Grid Community (CDN)';
} else { drawFallback(); }
function reload(spent){
  fetch('/api/rows?spent=' + encodeURIComponent(spent))
    .then(r => r.json())
    .then(d => {
      ROWS.length = 0; d.rows.forEach(x => ROWS.push(x));
      if (api) { api.setGridOption('rowData', ROWS); }
      else { drawFallback(); }
      const k = d.summary;
      document.querySelector('.kpis').innerHTML =
        kpi(k.total,'rows audited') + kpi(k.allow,'allowed') +
        kpi(k.block,'blocked') + kpi(k.hold,'need approval') +
        kpi('$'+k.exposure,'buying power') + kpi('$'+k.blocked_value,'value blocked');
    });
}
function kpi(n,l){ return '<div class="kpi"><div class="n">'+n+'</div><div class="l">'+l+'</div></div>'; }
const slider = document.getElementById('spent');
slider.addEventListener('input', e => {
  document.getElementById('spentVal').textContent = '$' + e.target.value;
  reload(e.target.value);
});
document.getElementById('csv').addEventListener('click', () => {
  if (api && api.exportDataAsCsv) { api.exportDataAsCsv({fileName:'paypilot-policy-audit.csv'}); return; }
  const cols = Object.keys(ROWS[0]||{}).filter(c=>c!=='evidence');
  const esc = v => '"' + String(v==null?'':v).replace(/"/g,'""') + '"';
  const body = [cols.join(',')].concat(ROWS.map(r => cols.map(c=>esc(r[c])).join(','))).join('\\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([body],{type:'text/csv'}));
  a.download = 'paypilot-policy-audit.csv'; a.click();
});
document.getElementById('reset').addEventListener('click', () => {
  if (!api) return;
  api.setFilterModel(null); api.setGridOption('quickFilterText','');
});
document.getElementById('onlyBlocked').addEventListener('change', e => {
  if (!api) return;
  if (e.target.checked) { api.setColumnFilterModel('decision', {filter:'block'}); }
  else { api.setColumnFilterModel('decision', null); }
  api.onFilterChanged();
});
</script></body></html>"""


def _kpis(s):
    def one(n, label):
        return '<div class="kpi"><div class="n">%s</div><div class="l">%s</div></div>' % (n, label)
    return (one(s["total"], "rows audited") + one(s["allow"], "allowed")
            + one(s["block"], "blocked") + one(s["hold"], "need approval")
            + one("$%s" % s["exposure"], "buying power")
            + one("$%s" % s["blocked_value"], "value blocked"))


def _embed_json(obj):
    """JSON safe to drop inside a <script> block.

    json.dumps leaves "</script>" intact, which would let a crafted product
    name terminate the script element early. Escaping the slash is enough and
    keeps the payload valid JavaScript.
    """
    return (json.dumps(obj, ensure_ascii=False)
            .replace("</", "<\\/")
            .replace("<!--", "<\\!--"))


def render_grid_page(products, policy=None, daily_spent=0.0):
    """Return the full HTML page for the audit grid."""
    policy = policy or Policy()
    rows = audit_rows(products, policy, daily_spent=daily_spent)
    page = (_HEAD
            .replace("__ROWS__", _embed_json(rows))
            .replace("__KPIS__", _kpis(summarize(rows)))
            .replace("__SPENT__", "%.0f" % daily_spent))
    return page


def rows_payload(products, policy=None, daily_spent=0.0):
    """JSON payload for /api/rows."""
    rows = audit_rows(products, policy, daily_spent=daily_spent)
    return {"rows": rows, "summary": summarize(rows), "daily_spent": daily_spent}

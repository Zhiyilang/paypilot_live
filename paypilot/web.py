"""Web demo UI (stdlib http.server): an agent console showing the full loop -
intent, ranked candidates, policy decisions, payment execution, receipt.

Run:  python -m paypilot serve --port 8780
Mode: mock by default (no keys needed). Set PAYPAL_CLIENT_ID/PAYPAL_SECRET and
start with `serve --live` to point at the real sandbox.
"""
import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .agent import ShopAgent
from .catalog import load_catalog
from .grid import render_grid_page, rows_payload
from .mock_paypal import MockPayPal
from .paypal import PayPalClient
from .policy import Policy
from .reasoning import get_brain
from .renewals import (analyze, parse_transactions_csv, render_renewals_page)

E = html.escape

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PayPilot - agentic commerce with guardrails</title>
<style>
:root{--blue:#4f7cff;--ink:#1a1f2b;--mut:#5a6472;--line:#e3e6ea;--bg:#f6f7f9;--ok:#0e9f6e;--warn:#b7791f;--bad:#e02424}
*{box-sizing:border-box}
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink);margin:0}
.wrap{max-width:920px;margin:0 auto;padding:28px 20px 60px}
header h1{font-size:24px;margin:0}
header p{color:var(--mut);margin:6px 0 0;font-size:14px}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:20px;margin-top:18px}
label{display:block;font-weight:600;font-size:13px;margin-bottom:6px}
textarea{width:100%;min-height:74px;border:1px solid var(--line);border-radius:8px;padding:10px;font-size:14px;font-family:inherit;resize:vertical}
.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;align-items:center}
button{background:var(--blue);color:#fff;border:0;border-radius:8px;padding:9px 16px;font-size:14px;cursor:pointer}
button.ghost{background:#eef1f6;color:var(--ink)}
.chip{background:#eef1f6;border:1px solid var(--line);border-radius:999px;padding:7px 13px;font-size:13px;cursor:pointer}
.chip:hover{background:#e2e8f5}
.status{display:inline-block;border-radius:6px;padding:2px 9px;font-size:12px;font-weight:700}
.st-completed{background:#def7ec;color:var(--ok)}.st-blocked{background:#fde8e8;color:var(--bad)}
.st-awaiting_approval{background:#fdf3d8;color:var(--warn)}.st-no_match{background:#eef1f6;color:var(--mut)}
.step{border-left:3px solid var(--blue);padding:8px 14px;margin:10px 0;background:#fbfcfe;border-radius:0 8px 8px 0}
.phase{font-weight:700;font-size:11.5px;letter-spacing:.08em;color:var(--blue)}
.detail{font-size:14px;margin:4px 0}
ul.ev{margin:4px 0 0;padding-left:18px;color:var(--mut);font-size:12.5px}
table{border-collapse:collapse;width:100%;margin:14px 0}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #eef0f3;font-size:14px;vertical-align:top}
th{width:140px;color:var(--mut);font-weight:600}
code{background:#eef1f5;border-radius:4px;padding:1px 5px;font-size:13px}
.mode{font-size:12px;color:var(--mut)}
h2{font-size:16px;margin:0 0 8px}
</style></head><body><div class="wrap">
<header><h1>PayPilot</h1>
<p>An AI shopping agent with policy guardrails. It reads your request, picks a product with evidence, checks spending policy <em>before</em> touching money, and pays with a real PayPal sandbox checkout.</p>
<p><a href="/grid">Open the Policy Audit Grid &rarr;</a> <span class="mode">(every catalog item scored against the policy engine, in AG Grid)</span>
&middot; <a href="/docs">API docs</a> <span class="mode">(OpenAPI 3.0.3 + Swagger UI)</span>
&middot; <a href="/renewals">Renewal Radar</a> <span class="mode">(subscriptions, price hikes, duplicates, polite nudges)</span></p></header>
<div class="card">
<form method="post" action="/run">
<label for="req">What should the agent buy?</label>
<textarea id="req" name="request" placeholder="e.g. Birthday gift for my mom, under $50 - she loves gardening">__REQUEST__</textarea>
<div class="row">
<button type="submit">Run agent</button>
<label class="row" style="font-weight:500;font-size:13px;color:var(--mut)"><input type="checkbox" name="approve" value="1" __CHECKED__> pre-approve orders above the approval threshold</label>
</div>
</form>
<div class="row" style="margin-top:14px">
<span style="font-size:13px;color:var(--mut)">Or try:</span>
__CHIPS__
</div>
<p class="mode">PayPal mode: <strong>__MODE__</strong> &middot; brain: <strong>__BRAIN__</strong></p>
</div>
__RESULT__
</div></body></html>"""


def _chips(scenarios):
    return "".join('<span class="chip" onclick="document.getElementById(\'req\').value=this.textContent">%s</span>'
                   % E(s[0]) for s in scenarios)


_STATUS_LABELS = {
    "completed": "Payment completed",
    "blocked": "Blocked by policy",
    "awaiting_approval": "Awaiting human approval",
    "no_match": "No matching product",
}


def _result_html(res):
    if res is None:
        return ""
    p = res.chosen.product if res.chosen else None
    rows = [("Status", '<span class="status st-%s">%s</span>'
             % (E(res.status), E(_STATUS_LABELS.get(res.status, res.status))))]
    if p:
        rows += [("Product", "%s <code>%s</code>" % (E(p.name), E(p.id))),
                 ("Merchant", E(p.merchant)),
                 ("Category", E(p.category)),
                 ("Price", "$%.2f" % p.price),
                 ("Rating", "%.1f / 5" % p.rating)]
    if res.policy:
        rows.append(("Policy", "%s (%s)" % (E(res.policy.action.upper()),
                                            E(res.policy.rule))))
    if res.order:
        rows.append(("PayPal order", "<code>%s</code>" % E(res.order.get("id"))))
    if res.capture_id:
        rows.append(("Capture", "<code>%s</code>" % E(res.capture_id)))
    if res.explanation:
        rows.append(("Why", E(res.explanation)))
    summary = "".join("<tr><th>%s</th><td>%s</td></tr>" % (k, v) for k, v in rows)
    trail = "".join(
        '<div class="step"><div class="phase">%s</div><div class="detail">%s</div>'
        '<ul class="ev">%s</ul></div>'
        % (E(s.phase), E(s.detail),
           "".join("<li>%s</li>" % E(x) for x in s.evidence) or "<li>(no evidence)</li>")
        for s in res.steps)
    return ('<div class="card"><h2>Result</h2><table>%s</table>'
            '<h2>Decision trail</h2>%s</div>' % (summary, trail))


def _render_page(request, checked, result, mode="mock (offline)", brain="offline"):
    from .cli import SCENARIOS
    page = PAGE
    for key, val in (("__REQUEST__", E(request)), ("__CHECKED__", checked),
                     ("__CHIPS__", _chips(SCENARIOS)),
                     ("__MODE__", mode),
                     ("__BRAIN__", brain),
                     ("__RESULT__", result)):
        page = page.replace(key, val)
    return page


def make_handler(catalog_path, live):
    agent_state = {"brain": "offline"}

    def fresh_agent():
        products = load_catalog(catalog_path)
        brain = get_brain(prefer_llm=True)
        agent_state["brain"] = "llm" if brain.__class__.__name__ == "LLMBrain" else "offline"
        if live:
            paypal = PayPalClient(
                client_id=os.environ.get("PAYPAL_CLIENT_ID", ""),
                secret=os.environ.get("PAYPAL_SECRET", ""))
        else:
            paypal = PayPalClient(mock=MockPayPal())
        return ShopAgent(products, brain=brain, paypal=paypal)

    from .cli import SCENARIOS
    products_cache = load_catalog(catalog_path)

    txn_path = os.environ.get("PAYPILOT_TRANSACTIONS") or os.path.join(
        os.path.dirname(__file__), "..", "examples", "transactions.csv")
    try:
        with open(txn_path, encoding="utf-8-sig") as f:
            txns_cache = parse_transactions_csv(f.read())
    except OSError:
        txns_cache = []

    def make_agent(daily_spent=0.0):
        brain = get_brain(prefer_llm=True)
        if live:
            paypal = PayPalClient(
                client_id=os.environ.get("PAYPAL_CLIENT_ID", ""),
                secret=os.environ.get("PAYPAL_SECRET", ""))
        else:
            paypal = PayPalClient(mock=MockPayPal())
        agent = ShopAgent(products_cache, brain=brain, paypal=paypal,
                          daily_spent=daily_spent)
        agent.mode = "sandbox" if live else "mock"
        return agent

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, code=200, ctype="text/html; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def _query(self):
            return {k: v[0] for k, v in
                    parse_qs(urlparse(self.path).query).items() if v}

        def _spent(self):
            try:
                return max(0.0, float(self._query().get("spent", "0")))
            except (TypeError, ValueError):
                return 0.0

        def do_GET(self):
            path = urlparse(self.path).path
            if path.startswith("/healthz"):
                self._send('{"ok":true}', ctype="application/json")
                return
            if path.startswith("/api/rows"):
                payload = rows_payload(products_cache, Policy(),
                                       daily_spent=self._spent())
                self._send(json.dumps(payload, ensure_ascii=False),
                           ctype="application/json")
                return
            if path.startswith("/grid"):
                self._send(render_grid_page(products_cache, Policy(),
                                            daily_spent=self._spent()))
                return
            if path.startswith("/openapi.yaml") or path.startswith("/openapi.yml"):
                from .api import load_spec
                self._send(load_spec(), ctype="application/yaml; charset=utf-8")
                return
            if path.startswith("/docs"):
                from .api import render_docs
                self._send(render_docs())
                return
            if path.startswith("/renewals"):
                self._send(render_renewals_page(txns_cache))
                return
            if path.startswith("/api/"):
                from .api import dispatch, ok, to_json
                if path.startswith("/api/v1/renewals"):
                    self._send(to_json(ok(analyze(txns_cache))[1]), 200,
                               "application/json")
                    return
                status, body = dispatch(products_cache, make_agent, live,
                                        path, "GET", self._query(), None)
                self._send(to_json(body), status, "application/json")
                return
            page = _render_page("", "", "",
                                mode="LIVE sandbox" if live else "mock (offline)")
            self._send(page)

        def do_POST(self):
            if not self.path.startswith("/run"):
                path = urlparse(self.path).path
                if not path.startswith("/api/"):
                    self._send("not found", 404, "text/plain")
                    return
                from .api import dispatch, to_json
                n = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(n).decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw) if raw else {}
                except ValueError:
                    status, body = (400, {
                        "ok": False,
                        "error": {"code": "INVALID_JSON",
                                  "message": "request body is not valid JSON"}})
                    self._send(to_json(body), status, "application/json")
                    return
                status, body = dispatch(products_cache, make_agent, live,
                                        path, "POST", {}, payload)
                self._send(to_json(body), status, "application/json")
                return
            n = int(self.headers.get("Content-Length", 0))
            form = parse_qs(self.rfile.read(n).decode("utf-8"))
            request = (form.get("request") or [""])[0].strip()
            approve = (form.get("approve") or [""])[0] == "1"
            if not request:
                self._send("empty request", 400, "text/plain")
                return
            agent = fresh_agent()
            res = agent.run(request, approve=approve)
            page = _render_page(request, "checked" if approve else "",
                                _result_html(res),
                                mode="LIVE sandbox" if live else "mock (offline)",
                                brain=agent_state["brain"])
            self._send(page)

    return Handler


def serve(port=None, catalog_path=None, live=False):
    # port=None -> honour the PORT env var (Render assigns a random one).
    # A hard-coded default here would silently ignore $PORT -> host 502.
    # Only fall back when port is None: `port or ...` treats an explicit 0 as
    # "unset" and turns an ephemeral-port request into the fixed default.
    if port is None:
        port = int(os.environ.get("PORT", "8780"))
    if catalog_path is None:
        catalog_path = os.path.join(os.path.dirname(__file__), "..",
                                    "examples", "catalog.csv")
    handler = make_handler(catalog_path, live)
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    print("PayPilot web demo on http://127.0.0.1:%d  (PayPal mode: %s)"
          % (port, "LIVE sandbox" if live else "mock"))
    httpd.serve_forever()

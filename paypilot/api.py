"""PayPilot REST API - the same agent loop, reachable over HTTP as JSON.

Everything here delegates to the same modules the CLI and the web UI use, so
there is exactly one implementation of "how PayPilot decides". The API exists
so the guardrails can be called by *other* agents, scripts and dashboards, not
only by someone typing into a textarea.

It is also described by a real OpenAPI 3.0.3 document (`openapi.yaml`), which
is what the agent consumes as context: see `paypilot/context.py`.

Routes (all mounted under the stdlib web server in `web.py`):

    GET  /api/v1/health          -> liveness + mode
    GET  /api/v1/catalog         -> products (?category=&max_price=&q=&sort=)
    POST /api/v1/quote           -> intent + ranked candidates, no payment
    POST /api/v1/policy/check    -> {product_id, daily_spent} -> decision
    POST /api/v1/agent/run       -> the full loop, optionally paying
    GET  /api/v1/context         -> API context for an LLM agent
    GET  /openapi.yaml           -> the spec itself
    GET  /docs                   -> Swagger UI over that spec
"""
import json
import os

from . import policy as policy_mod
from .catalog import search
from .policy import Policy
from .reasoning import get_brain

API_VERSION = "v1"


# ------------------------------------------------------------------ envelope

def ok(data, **meta):
    body = {"ok": True, "data": data}
    if meta:
        body["meta"] = meta
    return 200, body


def err(code, message, status=400, **extra):
    body = {"ok": False, "error": {"code": code, "message": message}}
    if extra:
        body["error"].update(extra)
    return status, body


# ------------------------------------------------------------------- helpers

def _product_dict(p):
    return {
        "id": p.id, "name": p.name, "category": p.category,
        "merchant": p.merchant, "price": p.price, "rating": p.rating,
        "stock": p.stock, "tags": list(p.tags),
    }


def _intent_dict(i):
    return {
        "occasion": i.occasion, "recipient": i.recipient, "budget": i.budget,
        "keywords": list(i.keywords), "category_hint": i.category_hint,
        "cheapest": bool(i.cheapest), "source": i.source,
    }


def _decision_dict(d):
    return {"action": d.action, "rule": d.rule, "detail": d.detail,
            "evidence": list(d.evidence)}


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# -------------------------------------------------------------------- routes

def health(products, live):
    return ok({"status": "ok", "api_version": API_VERSION,
               "paypal_mode": "sandbox" if live else "mock",
               "catalog_size": len(products),
               "brain": type(get_brain()).__name__})


def list_catalog(products, params):
    category = params.get("category") or None
    max_price = params.get("max_price")
    max_price = _float(max_price) if max_price is not None else None
    q = (params.get("q") or "").strip()
    sort = params.get("sort") or "value"
    keywords = [w.lower() for w in q.split()] if q else None
    cands = search(products, keywords=keywords, category=category,
                   max_price=max_price,
                   sort="price_asc" if sort == "price_asc" else "value")
    return ok([{"product": _product_dict(c.product), "score": c.score,
                "evidence": list(c.evidence)} for c in cands],
              count=len(cands), sort=sort)


def quote(products, payload):
    request = (payload.get("request") or "").strip()
    if not request:
        return err("EMPTY_REQUEST", "`request` is required")
    brain = get_brain()
    intent = brain.parse(request)
    sort = "price_asc" if intent.cheapest else "value"
    cands = search(products, keywords=intent.keywords,
                   category=intent.category_hint or None,
                   max_price=intent.budget, sort=sort)
    return ok({
        "request": request,
        "intent": _intent_dict(intent),
        "candidates": [
            {"rank": i + 1, "product": _product_dict(c.product),
             "score": c.score, "evidence": list(c.evidence)}
            for i, c in enumerate(cands[:10])
        ],
        "note": "quote does not move money - call /agent/run to act on it",
    }, count=len(cands))


def check_policy(products, payload):
    pid = (payload.get("product_id") or "").strip()
    if not pid:
        return err("MISSING_PRODUCT_ID", "`product_id` is required")
    match = [p for p in products if p.id == pid]
    if not match:
        return err("UNKNOWN_PRODUCT", "no product with id %r" % pid,
                   status=404, product_id=pid)
    daily_spent = _float(payload.get("daily_spent"), 0.0)
    policy = _policy_from(payload)
    dec = policy_mod.check(policy, match[0], daily_spent=daily_spent)
    return ok({"product": _product_dict(match[0]),
               "daily_spent": daily_spent,
               "policy": _policy_dict(policy),
               "decision": _decision_dict(dec)})


def _policy_from(payload):
    p = payload.get("policy") or {}
    return Policy(
        max_per_order=_float(p.get("max_per_order"), Policy.max_per_order),
        daily_limit=_float(p.get("daily_limit"), Policy.daily_limit),
        approval_threshold=_float(p.get("approval_threshold"),
                                  Policy.approval_threshold),
        allowed_categories=tuple(p.get("allowed_categories")
                                 or Policy.allowed_categories),
        blocked_merchants=tuple(p.get("blocked_merchants") or ()),
    )


def _policy_dict(p):
    return {"max_per_order": p.max_per_order, "daily_limit": p.daily_limit,
            "approval_threshold": p.approval_threshold,
            "allowed_categories": list(p.allowed_categories),
            "blocked_merchants": list(p.blocked_merchants)}


def run_agent(agent_factory, payload):
    request = (payload.get("request") or "").strip()
    if not request:
        return err("EMPTY_REQUEST", "`request` is required")
    approve = bool(payload.get("approve"))
    agent = agent_factory(daily_spent=_float(payload.get("daily_spent"), 0.0))
    res = agent.run(request, approve=approve)
    data = {
        "request": res.request,
        "status": res.status,
        "intent": _intent_dict(res.intent),
        "chosen": _product_dict(res.chosen.product) if res.chosen else None,
        "policy": _decision_dict(res.policy) if res.policy else None,
        "order": res.order,
        "capture_id": res.capture_id,
        "amount": res.amount,
        "explanation": res.explanation,
        "trail": [{"phase": s.phase, "detail": s.detail,
                   "evidence": list(s.evidence)} for s in res.steps],
    }
    return ok(data, paypal_mode=getattr(agent, "mode", "unknown"))


# ------------------------------------------------------------------ dispatch

def dispatch(products, agent_factory, live, path, method, params, payload):
    """Route an /api/v1/* call. Returns (status_code, json_body_dict)."""
    if path == "/api/v1/health":
        return health(products, live)
    if path == "/api/v1/catalog":
        return list_catalog(products, params)
    if path == "/api/v1/context":
        from .context import build_context
        return ok(build_context(products))
    if method != "POST":
        return err("METHOD_NOT_ALLOWED", "%s is not allowed on %s" % (method, path),
                   status=405)
    payload = payload if isinstance(payload, dict) else {}
    if path == "/api/v1/quote":
        return quote(products, payload)
    if path == "/api/v1/policy/check":
        return check_policy(products, payload)
    if path == "/api/v1/agent/run":
        return run_agent(agent_factory, payload)
    return err("NOT_FOUND", "unknown endpoint %s" % path, status=404)


def spec_path():
    """Absolute path of the OpenAPI document shipped with the package."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "openapi.yaml")


def load_spec():
    with open(spec_path(), encoding="utf-8") as f:
        return f.read()


def render_docs():
    """Swagger UI over the local spec, with a plain-text fallback listing."""
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PayPilot API docs</title>
<link rel="stylesheet"
 href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
<style>body{margin:0;background:#fafafa}#fallback{max-width:820px;margin:0 auto;
padding:28px;font-family:-apple-system,Segoe UI,Roboto,sans-serif;line-height:1.6}
code{background:#eef1f5;border-radius:4px;padding:1px 5px}</style></head><body>
<div id="swagger"></div>
<div id="fallback" style="display:none">
<h1>PayPilot API</h1>
<p>Swagger UI could not load (offline?). The machine-readable spec is still
served at <a href="/openapi.yaml"><code>/openapi.yaml</code></a>, and the
agent-consumable digest at
<a href="/api/v1/context"><code>/api/v1/context</code></a>.</p>
<p>Endpoints: <code>GET /api/v1/health</code>,
<code>GET /api/v1/catalog</code>, <code>POST /api/v1/quote</code>,
<code>POST /api/v1/policy/check</code>, <code>POST /api/v1/agent/run</code>.</p>
</div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
if (typeof SwaggerUIBundle === 'undefined') {
  document.getElementById('fallback').style.display = 'block';
} else {
  SwaggerUIBundle({url:'/openapi.yaml', dom_id:'#swagger',
    presets:[SwaggerUIBundle.presets.apis]});
}
</script></body></html>"""


def to_json(body):
    return json.dumps(body, ensure_ascii=False, indent=2)

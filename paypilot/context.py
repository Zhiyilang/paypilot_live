"""API context for AI agents - the "give an agent the API" layer.

An agent that has to call an API needs three things, and none of them is the
raw OpenAPI YAML (too big, too noisy):

1. **What can I call** - a compact catalogue of operations with the exact
   parameter names and a copy-pasteable example.
2. **What are the rules** - the domain constraints the API will enforce, so
   the agent stops asking for things that can only be refused.
3. **What does the data look like** - just enough shape (categories, price
   range, merchants) for the agent to form a plausible request.

`build_context()` assembles exactly that, sized to a token budget so it can be
dropped into a system prompt without crowding out the user's task.

This is also the seam for APIMatic: point `PAYPILOT_EXTRA_SPEC` at a spec that
APIMatic generated for any other API (the PayPal REST API included) and that
spec is summarised into the same context block, so an agent can be handed
PayPilot *and* an arbitrary third-party API in one prompt.
"""
import json
import os
import re

MAX_CHARS = 6000          # ~1.5k tokens: small enough to always fit


# --------------------------------------------------------- spec -> operations

_YAML_OP = re.compile(
    r"^  (?P<path>/\S+):\s*$\n(?P<body>(?:^    .*$\n?)+)", re.MULTILINE)
_YAML_METHOD = re.compile(r"^    (?P<verb>get|post|put|delete|patch):\s*$",
                          re.MULTILINE)
_YAML_FIELD = re.compile(r"^ {4,10}(?P<key>[a-zA-Z_]+):\s*(?P<val>.*)$",
                         re.MULTILINE)


def _yaml_to_ops(text):
    """Deliberately small YAML reader: enough for our own spec, no dependency.

    It only understands the two-level `paths: -> /route: -> verb:` shape plus
    `summary:` / `description:` / `operationId:` scalar fields. Anything else
    is ignored rather than guessed at.
    """
    ops = []
    for m in _YAML_OP.finditer(text):
        path = m.group("path")
        body = m.group("body")
        for vm in _YAML_METHOD.finditer(body):
            verb = vm.group("verb").upper()
            # look ahead from the verb for scalar fields until next verb/route
            start = vm.end()
            nxt = _YAML_METHOD.search(body, start)
            chunk = body[start:nxt.start() if nxt else len(body)]
            fields = {fm.group("key"): fm.group("val").strip()
                      for fm in _YAML_FIELD.finditer(chunk)}
            summary = fields.get("summary", "").strip("'\"")
            ops.append({
                "method": verb, "path": path,
                "summary": summary,
                "operation_id": fields.get("operationId", "").strip("'\""),
            })
    return ops


def load_extra_spec():
    """Optional external spec (e.g. an APIMatic-generated one) from env."""
    path = os.environ.get("PAYPILOT_EXTRA_SPEC", "").strip()
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        return {"path": path, "text": f.read()}


def summarize_spec(text, limit=12):
    ops = _yaml_to_ops(text)
    title = ""
    m = re.search(r"^\s{0,4}title:\s*(.+)$", text, re.MULTILINE)
    if m:
        title = m.group(1).strip().strip("'\"")
    return {"title": title, "operations": ops[:limit],
            "operation_count": len(ops)}


# ------------------------------------------------------------------- context

def _catalog_shape(products):
    cats = {}
    merchants = set()
    prices = []
    for p in products:
        cats[p.category] = cats.get(p.category, 0) + 1
        merchants.add(p.merchant)
        prices.append(p.price)
    return {
        "size": len(products),
        "categories": dict(sorted(cats.items())),
        "merchant_count": len(merchants),
        "price_range": [min(prices), max(prices)] if prices else [0, 0],
    }


def _policy_rules(policy=None):
    from .policy import Policy
    p = policy or Policy()
    return [
        {"rule": "BLOCKED_MERCHANT", "effect": "block",
         "condition": "merchant is on the blocklist"},
        {"rule": "CATEGORY_NOT_ALLOWED", "effect": "block",
         "condition": "category not in %s" % list(p.allowed_categories)},
        {"rule": "MAX_PER_ORDER", "effect": "block",
         "condition": "price > $%.2f" % p.max_per_order},
        {"rule": "DAILY_LIMIT", "effect": "block",
         "condition": "daily_spent + price > $%.2f" % p.daily_limit},
        {"rule": "APPROVAL_THRESHOLD", "effect": "hold (needs a human)",
         "condition": "price >= $%.2f" % p.approval_threshold},
    ]


_EXAMPLES = {
    "GET /api/v1/health": 'curl -s "$BASE/api/v1/health"',
    "GET /api/v1/catalog": 'curl -s "$BASE/api/v1/catalog?category=books&max_price=40"',
    "POST /api/v1/quote": ('curl -s -X POST "$BASE/api/v1/quote" -H "Content-Type: application/json" '
                           '-d \'{"request":"birthday gift for my mom, under $50, she gardens"}\''),
    "POST /api/v1/policy/check": ('curl -s -X POST "$BASE/api/v1/policy/check" -H "Content-Type: application/json" '
                                  '-d \'{"product_id":"G01","daily_spent":0}\''),
    "POST /api/v1/agent/run": ('curl -s -X POST "$BASE/api/v1/agent/run" -H "Content-Type: application/json" '
                               '-d \'{"request":"cheapest gardening book","approve":false}\''),
}


def build_context(products, policy=None, include_spec=True):
    """Return the full context block as a plain dict."""
    from .policy import Policy
    policy = policy or Policy()
    ops = []
    if include_spec:
        try:
            from .api import load_spec
            ops = _yaml_to_ops(load_spec())
        except OSError:
            ops = []
    extra = load_extra_spec()
    ctx = {
        "service": "PayPilot",
        "purpose": ("An AI shopping agent with hard spending guardrails. "
                    "It parses a natural-language request, ranks catalog "
                    "items with evidence, checks policy BEFORE touching "
                    "money, then pays through PayPal and returns a receipt."),
        "base_url": "http://127.0.0.1:8780 (or your deployed origin)",
        "contract": [
            "Call /api/v1/quote first - it never moves money.",
            "Call /api/v1/policy/check before assuming a purchase is allowed.",
            "Only call /api/v1/agent/run when you intend to actually pay; "
            "pass approve=true only with explicit human consent.",
            "A hold is not a failure: it means a human must approve.",
        ],
        "operations": [
            {"method": o["method"], "path": o["path"], "summary": o["summary"]}
            for o in ops
        ],
        "examples": _EXAMPLES,
        "policy_rules": _policy_rules(policy),
        "policy_defaults": {
            "max_per_order": policy.max_per_order,
            "daily_limit": policy.daily_limit,
            "approval_threshold": policy.approval_threshold,
            "allowed_categories": list(policy.allowed_categories),
        },
        "catalog_shape": _catalog_shape(products),
        "response_envelope": {
            "success": {"ok": True, "data": "...", "meta": "..."},
            "failure": {"ok": False, "error": {"code": "...", "message": "..."}},
        },
    }
    if extra:
        ctx["external_api"] = summarize_spec(extra["text"])
        ctx["external_api"]["source"] = os.path.basename(extra["path"])
    return ctx


def render_context_markdown(ctx):
    """Same context, as Markdown - what you actually paste into a prompt."""
    lines = ["# %s API context" % ctx["service"], "",
             ctx["purpose"], "",
             "Base URL: `%s`" % ctx["base_url"], "",
             "## How to use it", ""]
    lines += ["%d. %s" % (i + 1, c) for i, c in enumerate(ctx["contract"])]
    lines += ["", "## Operations", ""]
    if ctx["operations"]:
        lines.append("| Method | Path | Summary |")
        lines.append("|---|---|---|")
        for o in ctx["operations"]:
            lines.append("| %s | `%s` | %s |" % (o["method"], o["path"],
                                                 o["summary"]))
    else:
        lines.append("(spec unavailable - endpoints still listed below)")
        for k in ctx["examples"]:
            lines.append("- `%s`" % k)
    lines += ["", "## Spending policy the API enforces", ""]
    lines.append("| Rule | Effect | Condition |")
    lines.append("|---|---|---|")
    for r in ctx["policy_rules"]:
        lines.append("| `%s` | %s | %s |" % (r["rule"], r["effect"],
                                             r["condition"]))
    shape = ctx["catalog_shape"]
    lines += ["", "## Catalog shape", "",
              "- %d products across %s" % (shape["size"],
                                           ", ".join(shape["categories"])),
              "- %d merchants, price range $%.2f-$%.2f"
              % (shape["merchant_count"], shape["price_range"][0],
                 shape["price_range"][1])]
    lines += ["", "## Examples", "", "```bash"]
    for cmd in ctx["examples"].values():
        lines.append(cmd)
    lines += ["```"]
    ext = ctx.get("external_api")
    if ext:
        lines += ["", "## External API (%s)" % ext.get("source", "?"), "",
                  "%s - %d operations" % (ext.get("title") or "untitled",
                                          ext.get("operation_count", 0))]
        lines.append("| Method | Path | Summary |")
        lines.append("|---|---|---|")
        for o in ext.get("operations", []):
            lines.append("| %s | `%s` | %s |" % (o["method"], o["path"],
                                                 o["summary"]))
    return "\n".join(lines)


def context_text(products=None, policy=None, budget=MAX_CHARS):
    """Markdown context, hard-trimmed to `budget` characters."""
    ctx = build_context(products or [], policy=policy)
    md = render_context_markdown(ctx)
    if len(md) <= budget:
        return md
    # trim the longest-to-shortest sections rather than blind truncation
    head = md[:budget]
    cut = head.rfind("\n## ")
    return (head[:cut] if cut > budget // 2 else head) + \
        "\n\n_(context trimmed to fit the token budget - full spec at /openapi.yaml)_"


def context_json(products=None, policy=None):
    return json.dumps(build_context(products or [], policy=policy),
                      ensure_ascii=False, indent=2)

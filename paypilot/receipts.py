"""Receipt rendering: plain text (CLI) and HTML (web UI / submission demo)."""
import html


def _summary_lines(res):
    p = res.chosen.product if res.chosen else None
    lines = [
        "PayPilot receipt",
        "=" * 60,
        "request      : %s" % res.request,
        "status       : %s" % res.status.upper(),
    ]
    if p:
        lines += [
            "product      : %s (%s)" % (p.name, p.id),
            "merchant     : %s" % p.merchant,
            "category     : %s" % p.category,
            "price        : $%.2f" % p.price,
            "rating       : %.1f/5" % p.rating,
        ]
    if res.policy:
        lines += ["policy       : %s (%s)" % (res.policy.action.upper(),
                                              res.policy.rule)]
    if res.order:
        lines += ["paypal order : %s" % res.order.get("id")]
    if res.capture_id:
        lines += ["capture id   : %s" % res.capture_id]
    if res.explanation:
        lines += ["why          : %s" % res.explanation]
    lines += ["", "decision trail:"]
    for s in res.steps:
        lines.append("  [%s] %s" % (s.phase, s.detail))
    return "\n".join(lines)


_STATUS_LABEL = {
    "completed": "Payment completed",
    "blocked": "Blocked by policy",
    "awaiting_approval": "Awaiting human approval",
    "no_match": "No matching product",
}


def _summary_html(res):
    e = html.escape
    p = res.chosen.product if res.chosen else None
    rows = [("Request", e(res.request)),
            ("Status", _STATUS_LABEL.get(res.status, res.status))]
    if p:
        rows += [("Product", "%s <code>%s</code>" % (e(p.name), e(p.id))),
                 ("Merchant", e(p.merchant)),
                 ("Category", e(p.category)),
                 ("Price", "$%.2f" % p.price),
                 ("Rating", "%.1f / 5" % p.rating)]
    if res.policy:
        rows += [("Policy", "%s (%s)" % (e(res.policy.action.upper()),
                                         e(res.policy.rule)))]
    if res.order:
        rows.append(("PayPal order", "<code>%s</code>" % e(res.order.get("id"))))
    if res.capture_id:
        rows.append(("Capture id", "<code>%s</code>" % e(res.capture_id)))
    if res.explanation:
        rows.append(("Why", e(res.explanation)))
    summary = "".join("<tr><th>%s</th><td>%s</td></tr>" % (k, v) for k, v in rows)
    trail = "".join(
        '<div class="step"><div class="phase">%s</div>'
        '<div class="detail">%s</div><ul class="ev">%s</ul></div>'
        % (e(s.phase), e(s.detail),
           "".join("<li>%s</li>" % e(x) for x in s.evidence) or "<li>(no evidence)</li>")
        for s in res.steps)
    return summary, trail


def render(res):
    text = _summary_lines(res)
    summary, trail = _summary_html(res)
    doc = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>PayPilot receipt</title>
<style>
 body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f6f7f9;color:#1a1f2b;margin:0;padding:24px}
 .card{max-width:860px;margin:0 auto;background:#fff;border:1px solid #e3e6ea;border-radius:12px;padding:24px}
 h1{font-size:20px;margin:0 0 16px}
 table{border-collapse:collapse;width:100%;margin-bottom:20px}
 th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #eef0f3;font-size:14px;vertical-align:top}
 th{width:130px;color:#5a6472;font-weight:600}
 code{background:#eef1f5;border-radius:4px;padding:1px 5px;font-size:13px}
 .step{border-left:3px solid #4f7cff;padding:8px 14px;margin:10px 0;background:#fbfcfe;border-radius:0 8px 8px 0}
 .phase{font-weight:700;font-size:12px;letter-spacing:.08em;color:#4f7cff}
 .detail{font-size:14px;margin:4px 0}
 ul.ev{margin:4px 0 0;padding-left:18px;color:#5a6472;font-size:12.5px}
</style></head><body><div class="card">
<h1>PayPilot receipt</h1>
<table>__SUMMARY__</table>
<h1>Decision trail</h1>
__TRAIL__
</div></body></html>""".replace("__SUMMARY__", summary).replace("__TRAIL__", trail)
    return text, doc

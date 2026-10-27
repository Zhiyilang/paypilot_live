"""Renewal Radar - find the money leaving on autopilot.

A shopping agent that only helps you *spend* is half a product. The same
transaction history that proves an agent can pay also shows what it is paying
for month after month: subscriptions nobody cancelled, silent price hikes,
duplicate charges, annual renewals that arrive as a surprise.

This module turns a raw transaction list into three things:

1. **A calendar** - the next 60 days of predicted charges, so a renewal is a
   scheduled event instead of an ambush.
2. **A verdict per subscription** - keep / review / cancel / renegotiate, each
   with the evidence that produced it (amounts, dates, deltas).
3. **A nudge** - a polite, ready-to-send message per flagged subscription.
   AI-written when a brain is configured, template-written offline. Ruthless
   honesty about what it does not know: it never invents a charge.

Deterministic by default; the LLM only ever words the message, never decides
the facts.
"""
import html as _html
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

E = _html.escape

# ----------------------------------------------------------------- structures


@dataclass
class Txn:
    id: str
    day: date
    merchant: str
    amount: float
    currency: str = "USD"
    status: str = "COMPLETED"


@dataclass
class Subscription:
    merchant: str
    cadence: str                 # weekly | monthly | quarterly | yearly | irregular
    amount: float                # most recent charge
    previous_amount: float       # median of earlier charges at the same level
    charges: list = field(default_factory=list)   # Txn objects, oldest first
    next_charge: object = None   # date or None
    flags: list = field(default_factory=list)     # PRICE_HIKE / DUPLICATE / DORMANT
    verdict: str = "keep"        # keep | review | cancel | renegotiate
    annual_cost: float = 0.0
    nudge: str = ""
    evidence: list = field(default_factory=list)


# ------------------------------------------------------------------- parsing

_CSV_HEAD = ("date", "merchant", "amount")


def parse_transactions_csv(text):
    """Parse `date,merchant,amount[,id][,status]` (header optional)."""
    import csv
    import io

    text = (text or "").replace("\ufeff", "")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        return []
    start = 0
    low = [c.strip().lower() for c in rows[0]]
    if all(any(h in c for c in low) for h in _CSV_HEAD):
        start = 1
    out = []
    for i, row in enumerate(rows[start:]):
        if len(row) < 3:
            continue
        day = _parse_date(row[0])
        merchant = row[1].strip()
        amount = _parse_money(row[2])
        if day is None or amount is None or not merchant:
            continue
        out.append(Txn(id=row[3].strip() if len(row) > 3 and row[3].strip()
                       else "T%03d" % (i + 1),
                       day=day, merchant=merchant, amount=amount,
                       status=row[4].strip() if len(row) > 4 and row[4].strip()
                       else "COMPLETED"))
    return out


def parse_paypal_transactions(payload):
    """Parse the shape PayPal's reporting/transaction APIs return.

    Accepts either `{"transactions": [...]}` or a bare list. Unknown or missing
    fields are skipped rather than guessed - the one rule this module never
    breaks is "never invent a charge".
    """
    rows = payload.get("transactions", payload) if isinstance(payload, dict) \
        else payload
    if not isinstance(rows, list):
        return []
    out = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        raw = r.get("transaction_info") or r
        day = _parse_date(str(raw.get("transaction_initiation_date")
                              or raw.get("date") or ""))
        # PayPal nests amounts under transaction_info but often leaves the
        # counterparty at the top level, so look in both places
        merchant = (r.get("merchant") or raw.get("merchant")
                    or r.get("counterparty") or raw.get("counterparty")
                    or raw.get("description") or "")
        if isinstance(merchant, dict):
            merchant = merchant.get("name", "")
        amt = raw.get("transaction_amount") or {}
        if isinstance(amt, dict):
            amount = _parse_money(str(amt.get("value", "")))
            currency = amt.get("currency_code", "USD")
        else:
            amount = _parse_money(str(amt))
            currency = "USD"
        if day is None or amount is None or not str(merchant).strip():
            continue
        out.append(Txn(id=str(raw.get("transaction_id")
                              or r.get("id") or "T%03d" % (i + 1)),
                       day=day, merchant=str(merchant).strip(),
                       amount=amount, currency=currency,
                       status=str(raw.get("transaction_status", "COMPLETED"))))
    return out


_DATE_PATTERNS = [
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})"), (1, 2, 3)),   # 2026-03-05
    (re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})"), (1, 2, 3)),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), (3, 1, 2)),  # 3/5/2026
]

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def _parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    s = s.split("T")[0]          # keep "Mar 5, 2026" intact, drop ISO time
    for pat, (yi, mi, di) in _DATE_PATTERNS:
        m = pat.match(s)
        if m:
            try:
                return date(int(m.group(yi)), int(m.group(mi)), int(m.group(di)))
            except ValueError:
                return None
    m = re.match(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$", s)
    if m and m.group(1).lower()[:3] in _MONTHS:
        try:
            return date(int(m.group(3)), _MONTHS[m.group(1).lower()[:3]],
                        int(m.group(2)))
        except ValueError:
            return None
    return None


def _parse_money(s):
    s = (s or "").strip().replace("$", "").replace(",", "").replace("USD", "")
    s = s.strip()
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        v = float(m.group(0))
    except ValueError:
        return None
    return -v if neg else v


# ------------------------------------------------------------------ detection

_CADENCES = [
    ("weekly", 5, 9),
    ("monthly", 25, 35),
    ("quarterly", 80, 100),
    ("yearly", 350, 380),
]


def _median(xs):
    if not xs:
        return 0.0
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _cadence_of(gaps):
    if not gaps:
        return "irregular"
    med = _median(gaps)
    for name, lo, hi in _CADENCES:
        if lo <= med <= hi:
            return name
    return "irregular"


def _step_days(cadence, gaps):
    table = {"weekly": 7, "monthly": 30, "quarterly": 91, "yearly": 365}
    if cadence in table:
        return table[cadence]
    return int(round(_median(gaps))) if gaps else 30


def detect_subscriptions(txns, today=None, price_hike_pct=0.05):
    """Group transactions by merchant and classify the recurring ones."""
    today = today or date.today()
    by_merchant = {}
    for t in txns:
        if (t.status or "").upper() not in ("", "COMPLETED", "S", "SUCCESS"):
            continue
        by_merchant.setdefault(_norm_merchant(t.merchant), []).append(t)

    subs = []
    for key, group in by_merchant.items():
        group.sort(key=lambda t: t.day)
        if len(group) < 2:
            continue                      # one charge proves no recurrence
        gaps = [(group[i + 1].day - group[i].day).days
                for i in range(len(group) - 1)]
        cadence = _cadence_of(gaps)
        if cadence == "irregular":
            # two identical charges a few days apart is still worth surfacing,
            # even though it says nothing about a cycle
            if not (len(group) == 2 and gaps and gaps[0] <= 7
                    and abs(abs(group[0].amount) - abs(group[1].amount)) < 0.02):
                continue
            cadence = "unknown"
        amounts = [round(abs(t.amount), 2) for t in group]
        latest = amounts[-1]
        earlier = amounts[:-1]
        prev = _median(earlier) if earlier else latest
        step = _step_days(cadence, gaps)
        last = group[-1].day
        if cadence == "unknown":
            next_charge = None          # refuse to predict what we cannot see
        else:
            next_charge = last + timedelta(days=step)
            while next_charge < today:
                next_charge += timedelta(days=step)

        flags, evidence = [], []
        if prev > 0 and latest > prev * (1 + price_hike_pct):
            flags.append("PRICE_HIKE")
            evidence.append(
                "latest charge $%.2f on %s vs earlier median $%.2f (+%.0f%%)"
                % (latest, last.isoformat(), prev,
                   100.0 * (latest - prev) / prev))
        # a true duplicate lands within days, not on the next cycle - otherwise
        # every weekly coffee would look like a double charge
        for i in range(len(group) - 1):
            if (group[i + 1].day - group[i].day).days <= 3 and \
                    abs(amounts[i + 1] - amounts[i]) < 0.02:
                flags.append("DUPLICATE")
                evidence.append(
                    "two identical $%.2f charges %d days apart (%s, %s)"
                    % (amounts[i], (group[i + 1].day - group[i].day).days,
                       group[i].day.isoformat(),
                       group[i + 1].day.isoformat()))
                break
        # dormant only relative to its own cycle - a yearly renewal is not
        # "dormant" nine months out, a monthly one two months silent is
        if cadence != "unknown" and (today - last).days > max(60, 2 * step):
            flags.append("DORMANT")
            evidence.append("last charge %d days ago on a %s cycle"
                            % ((today - last).days, cadence))
        evidence.append("%d charges, ~%d days apart (%s)"
                        % (len(group), step, cadence))

        multiple = {"weekly": 52, "monthly": 12, "quarterly": 4,
                    "yearly": 1}.get(cadence, 12)
        annual = multiple * latest
        if cadence == "unknown":
            evidence.append("cadence unknown - annualised at 12 cycles "
                            "(verify before acting)")

        if "DUPLICATE" in flags:
            verdict = "cancel"
        elif "PRICE_HIKE" in flags:
            verdict = "renegotiate"
        elif "DORMANT" in flags:
            verdict = "review"
        else:
            verdict = "keep"

        subs.append(Subscription(
            merchant=group[-1].merchant, cadence=cadence, amount=latest,
            previous_amount=round(prev, 2), charges=group,
            next_charge=next_charge, flags=sorted(set(flags)), verdict=verdict,
            annual_cost=round(annual, 2), evidence=evidence))
    subs.sort(key=lambda s: -s.annual_cost)
    return subs


def _norm_merchant(name):
    s = re.sub(r"\s+", " ", (name or "")).strip().lower()
    s = re.sub(r"[*#].*$", "", s)
    s = re.sub(r"\b(inc|llc|ltd|corp|co|gmbh|plc)\b\.?", "", s)
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------- nudges

_TEMPLATES = {
    "cancel": ("Hi {merchant} team - I was billed twice within days for the "
               "same {thing} ({amounts}). Could you refund the duplicate and "
               "confirm it is cancelled? Thanks!"),
    "renegotiate": ("Hi {merchant} team - my {thing} went from "
                    "${previous} to ${latest}. Is there a current plan or "
                    "loyalty rate closer to the old price? Happy to stay if "
                    "it can be matched."),
    "review": ("Hi {merchant} team - I have not really used this "
               "{thing} recently. Before I cancel, is there a pause or a "
               "cheaper tier I should know about?"),
    "keep": ("Hi {merchant} team - all good here, just confirming my "
             "{thing} is active at ${latest}. Thanks!"),
}


def write_nudges(subs, brain=None):
    """Attach a polite message to each subscription. Returns the same list."""
    for s in subs:
        thing = ("%s subscription" % s.cadence if s.cadence != "unknown"
                 else "charge")
        facts = {
            "merchant": s.merchant, "cadence": s.cadence, "thing": thing,
            "latest": "%.2f" % s.amount, "previous": "%.2f" % s.previous_amount,
            "amounts": "$%.2f" % s.amount,
        }
        text = None
        if brain is not None:
            try:
                text = brain.nudge(s, facts)
            except Exception:
                text = None
        s.nudge = (text or _TEMPLATES.get(s.verdict, _TEMPLATES["keep"])
                   .format(**facts))
    return subs


def savings_plan(subs):
    """Conservative annual saving: cancel = full, renegotiate = 15%, review = 30%."""
    rate = {"cancel": 1.0, "renegotiate": 0.15, "review": 0.30, "keep": 0.0}
    total = 0.0
    lines = []
    for s in subs:
        save = round(s.annual_cost * rate.get(s.verdict, 0.0), 2)
        total += save
        if save > 0:
            lines.append({
                "merchant": s.merchant, "verdict": s.verdict,
                "annual_cost": s.annual_cost, "estimated_saving": save,
                "flags": s.flags,
                "why": "; ".join(s.evidence[:2]),
            })
    lines.sort(key=lambda d: -d["estimated_saving"])
    return {"annual_leak": round(sum(s.annual_cost for s in subs), 2),
            "recoverable": round(total, 2),
            "items": lines}


def renewal_calendar(subs, today=None, days=60):
    """Events in the next `days`, sorted by date, with a heat level."""
    today = today or date.today()
    end = today + timedelta(days=days)
    events = []
    for s in subs:
        d = s.next_charge
        guard = 0
        while d and d <= end and guard < 40:
            if d >= today:
                events.append({
                    "date": d.isoformat(), "merchant": s.merchant,
                    "amount": s.amount, "cadence": s.cadence,
                    "verdict": s.verdict, "flags": s.flags,
                    "heat": "high" if s.verdict in ("cancel", "renegotiate")
                            else ("medium" if s.verdict == "review" else "low"),
                })
            step = _step_days(s.cadence, []) or 30
            d += timedelta(days=step)
            guard += 1
    events.sort(key=lambda e: (e["date"], -e["amount"]))
    return events


def analyze(txns, today=None, brain=None, days=60):
    """One call, the whole picture."""
    today = today or date.today()
    subs = detect_subscriptions(txns, today=today)
    write_nudges(subs, brain=brain)
    return {
        "as_of": today.isoformat(),
        "transaction_count": len(txns),
        "subscriptions": [to_dict(s) for s in subs],
        "calendar": renewal_calendar(subs, today=today, days=days),
        "plan": savings_plan(subs),
    }


_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PayPilot &middot; Renewal Radar</title>
<style>
:root{--blue:#4f7cff;--ink:#1a1f2b;--mut:#5a6472;--line:#e3e6ea;--bg:#f6f7f9;
      --ok:#0e9f6e;--warn:#b7791f;--bad:#e02424}
*{box-sizing:border-box}
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink);margin:0}
.wrap{max-width:1080px;margin:0 auto;padding:24px 20px 60px}
header h1{font-size:22px;margin:0}
header p{color:var(--mut);margin:6px 0 0;font-size:14px;max-width:760px}
.kpis{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}
.kpi{background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:130px}
.kpi .n{font-size:20px;font-weight:700}
.kpi .l{font-size:11.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-top:14px}
.cal{width:100%;border-collapse:collapse;font-size:12.5px}
.cal th,.cal td{border:1px solid var(--line);padding:6px 7px;vertical-align:top;text-align:left}
.cal th{background:#fafbfc;font-weight:600;color:var(--mut);width:14.2%}
.cal td .d{font-weight:700;font-size:12px;color:var(--mut)}
.cal td.today{outline:2px solid var(--blue)}
.ev{border-radius:6px;padding:2px 6px;margin:2px 0;font-size:11.5px;display:block}
.h-high{background:#fde8e8;color:var(--bad)}
.h-medium{background:#fdf3d8;color:var(--warn)}
.h-low{background:#eef1f6;color:var(--mut)}
table.sub{border-collapse:collapse;width:100%;font-size:13.5px;margin-top:6px}
table.sub th,table.sub td{border-bottom:1px solid #eef0f3;padding:7px 9px;text-align:left}
.badge{display:inline-block;border-radius:6px;padding:1px 8px;font-size:11.5px;font-weight:700;color:#fff}
.v-cancel{background:var(--bad)}.v-renegotiate{background:var(--warn)}
.v-review{background:#6b7280}.v-keep{background:var(--ok)}
pre.nudge{background:#fbfcfe;border:1px solid var(--line);border-radius:8px;
padding:10px 12px;white-space:pre-wrap;font-family:inherit;font-size:13px;margin:6px 0 0}
.note{font-size:12.5px;color:var(--mut)}
ul.evidence{margin:4px 0 0;padding-left:18px;color:var(--mut);font-size:12.5px}
code{background:#eef1f5;border-radius:4px;padding:1px 5px;font-size:12.5px}
</style></head><body><div class="wrap">
<header><h1>Renewal Radar</h1>
<p>The money that leaves on autopilot, made visible. Same transaction history
the agent pays from &mdash; read the other way round: what renews next, what
quietly went up, what is being charged twice, and a polite message you can send
for each one.</p></header>
<div class="kpis">__KPIS__</div>
<div class="card"><h2 style="font-size:15px;margin:0 0 8px">Next 60 days</h2>
__CAL__
<p class="note">Predicted from each merchant's own cadence. A charge we cannot
predict (an unexplained duplicate) is left off the calendar rather than guessed.</p></div>
<div class="card"><h2 style="font-size:15px;margin:0 0 4px">Subscriptions &amp; verdicts</h2>
__TABLE__
</div>
<div class="card"><h2 style="font-size:15px;margin:0 0 6px">Saving plan</h2>
__PLAN__
<p class="note">Cancel counts at 100%, renegotiate at 15%, review at 30% &mdash;
deliberately conservative. Every verdict above carries its evidence.</p></div>
<p class="note">Source: <code>examples/transactions.csv</code> &middot; engine:
<code>paypilot.renewals</code> &middot; JSON at
<a href="/api/v1/renewals"><code>/api/v1/renewals</code></a>. Facts are
deterministic; only the wording of each nudge can be AI-written.</p>
</div></body></html>"""


def _kpis(a):
    p = a["plan"]
    def one(n, l):
        return '<div class="kpi"><div class="n">%s</div><div class="l">%s</div></div>' % (n, l)
    return (one(len(a["subscriptions"]), "subscriptions found")
            + one("$%.0f" % p["annual_leak"], "annual recurring")
            + one("$%.0f" % p["recoverable"], "recoverable / yr")
            + one(len(a["calendar"]), "charges in 60d")
            + one(a["as_of"], "as of"))


def _calendar(a, today):
    from collections import defaultdict
    by_day = defaultdict(list)
    for e in a["calendar"]:
        by_day[e["date"]].append(e)
    start = today - timedelta(days=today.weekday())       # Monday of this week
    rows = []
    rows.append("<tr>" + "".join("<th>%s</th>" % d for d in
                ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) + "</tr>")
    day = start
    for _ in range(5):                                    # 5 weeks of grid
        cells = []
        for _ in range(7):
            iso = day.isoformat()
            cls = " class='today'" if iso == today.isoformat() else ""
            body = '<span class="d">%d</span>' % day.day
            for e in by_day.get(iso, []):
                body += ('<span class="ev h-%s">%s $%.2f</span>'
                         % (e["heat"], E(e["merchant"][:18]), e["amount"]))
            cells.append("<td%s>%s</td>" % (cls, body))
            day += timedelta(days=1)
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return "<table class='cal'>%s</table>" % "".join(rows)


def _table(a):
    rows = []
    for s in a["subscriptions"]:
        badges = "".join('<span class="badge" style="background:#6b7280">%s</span> '
                         % f for f in s["flags"])
        rows.append(
            "<tr><td><strong>%s</strong><br><span class='note'>%s &middot; %d charges "
            "&middot; since %s</span><ul class='evidence'>%s</ul>"
            "<pre class='nudge'>%s</pre></td>"
            "<td style='width:130px'>$%.2f<br><span class='note'>$%.0f/yr</span></td>"
            "<td style='width:120px'><span class='badge v-%s'>%s</span><br>%s</td>"
            "<td style='width:110px'>%s</td></tr>"
            % (E(s["merchant"]), s["cadence"], s["charge_count"], s["first_seen"],
               "".join("<li>%s</li>" % E(e) for e in s["evidence"]),
               E(s["nudge"]),
               s["amount"], s["annual_cost"], s["verdict"], s["verdict"],
               badges, s["next_charge"] or "-"))
    head = ("<tr><th>Subscription &amp; ready-to-send nudge</th><th>Amount</th>"
            "<th>Verdict</th><th>Next charge</th></tr>")
    return "<table class='sub'>%s%s</table>" % (head, "".join(rows))


def _plan(a):
    p = a["plan"]
    if not p["items"]:
        return "<p class='note'>Nothing flagged &mdash; every recurring charge looks clean.</p>"
    rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>$%.2f</td><td><strong>$%.2f</strong></td>"
        "<td class='note'>%s</td></tr>"
        % (E(i["merchant"]), i["verdict"], i["annual_cost"],
           i["estimated_saving"], E(i["why"][:80]))
        for i in p["items"])
    return ("<table class='sub'><tr><th>Merchant</th><th>Action</th>"
            "<th>Annual cost</th><th>Est. saving</th><th>Why</th></tr>%s</table>"
            % rows)


def render_renewals_page(txns, today=None, brain=None):
    today = today or date.today()
    a = analyze(txns, today=today, brain=brain)
    return (_PAGE.replace("__KPIS__", _kpis(a))
                 .replace("__CAL__", _calendar(a, today))
                 .replace("__TABLE__", _table(a))
                 .replace("__PLAN__", _plan(a)))


def to_dict(s):
    return {
        "merchant": s.merchant, "cadence": s.cadence,
        "amount": s.amount, "previous_amount": s.previous_amount,
        "charge_count": len(s.charges),
        "first_seen": s.charges[0].day.isoformat(),
        "last_charge": s.charges[-1].day.isoformat(),
        "next_charge": s.next_charge.isoformat() if s.next_charge else None,
        "annual_cost": s.annual_cost, "flags": s.flags, "verdict": s.verdict,
        "evidence": list(s.evidence), "nudge": s.nudge,
    }

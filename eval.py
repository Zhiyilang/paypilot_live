"""PayPilot eval suite - runs fully offline against the mock PayPal client.

Covers intent parsing, ranking, every policy guardrail, the mocked PayPal
OAuth -> order -> capture flow, receipts, and brain fallback behaviour.

Usage:  python eval.py        (exit code 0 = all pass)
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paypilot.agent import ShopAgent, _extract_capture
from paypilot.catalog import load_catalog, search
from paypilot.mock_paypal import MockPayPal
from paypilot.paypal import PayPalClient
from paypilot.policy import Policy, check as policy_check
from paypilot.reasoning import OfflineBrain, LLMBrain, BrainError, get_brain

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(HERE, "examples", "catalog.csv")
PRODUCTS = load_catalog(CATALOG)

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        FAILURES.append(name)
        print("FAIL  %s  %s" % (name, extra))


def make_agent(policy=None, daily_spent=0.0):
    return ShopAgent(PRODUCTS, policy=policy, brain=OfflineBrain(),
                     paypal=PayPalClient(mock=MockPayPal()),
                     daily_spent=daily_spent)


def main():
    # ---- catalog ------------------------------------------------------
    check("catalog_loads_all_products", len(PRODUCTS) == 17,
          "got %d" % len(PRODUCTS))
    check("catalog_out_of_stock_excluded",
          all(p.stock > 0 for p in PRODUCTS))

    # ---- offline intent parsing ---------------------------------------
    brain = OfflineBrain()
    i1 = brain.parse("Birthday gift for my mom, under $50 - she loves gardening")
    check("intent_budget_under_50", i1.budget == 50.0, str(i1.budget))
    check("intent_occasion_birthday", i1.occasion == "birthday", i1.occasion)
    check("intent_recipient_mom", i1.recipient == "mom", i1.recipient)
    check("intent_category_garden", i1.category_hint == "garden",
          i1.category_hint)
    i2 = brain.parse("Find me the cheapest mechanical keyboard")
    check("intent_cheapest_flag", i2.cheapest is True)
    check("intent_no_budget_without_amount", i2.budget is None)
    i3 = brain.parse("Buy a luxury watch for $500 as a treat")
    check("intent_budget_dollar_500", i3.budget == 500.0, str(i3.budget))

    # ---- search / ranking ----------------------------------------------
    cands = search(PRODUCTS, keywords=i1.keywords, category="garden",
                   max_price=50.0)
    check("search_respects_budget", all(c.product.price <= 50.0 for c in cands))
    check("search_respects_category",
          all(c.product.category == "garden" for c in cands))
    check("search_returns_candidates", len(cands) >= 3, str(len(cands)))
    check("every_candidate_has_evidence",
          all(len(c.evidence) >= 2 for c in cands))
    cheap = search(PRODUCTS, keywords=["keyboard"], category="electronics",
                   sort="price_asc")
    check("search_cheapest_sort_ascending",
          [c.product.price for c in cheap] ==
          sorted(c.product.price for c in cheap))
    check("search_no_match_category",
          search(PRODUCTS, category="nonexistent") == [])

    # ---- happy path: gift for mom --------------------------------------
    agent = make_agent()
    res = agent.run("Birthday gift for my mom, under $50 - she loves gardening")
    check("gift_completed", res.status == "completed", res.status)
    check("gift_picks_tool_set", res.chosen.product.id == "G001",
          res.chosen.product.id if res.chosen else "none")
    check("gift_amount", abs(res.amount - 34.99) < 0.001, str(res.amount))
    check("gift_capture_id", res.capture_id.startswith("MOCK-CAPTURE-"),
          res.capture_id)
    check("gift_order_created", bool(res.order and res.order.get("id")))
    check("gift_receipt_mentions_product",
          "Deluxe Garden Tool Set" in res.receipt_text)
    check("gift_receipt_html_has_capture",
          res.capture_id in res.receipt_html)
    phases = [s.phase for s in res.steps]
    check("gift_phase_order",
          phases == ["PERCEIVE", "SEARCH", "SELECT", "POLICY",
                     "EXPLAIN", "EXECUTE", "CAPTURE"], str(phases))
    check("gift_policy_allowed", res.policy.action == "allow",
          res.policy.rule)

    # ---- cheapest keyboard ----------------------------------------------
    res = make_agent().run("Find me the cheapest mechanical keyboard")
    check("keyboard_completed", res.status == "completed", res.status)
    check("keyboard_cheapest_pick", res.chosen.product.id == "T001",
          res.chosen.product.id if res.chosen else "none")
    check("keyboard_amount_79_99", abs(res.amount - 79.99) < 0.001)

    # ---- policy: category allowlist -------------------------------------
    res = make_agent().run("Buy a luxury watch for $500 as a treat")
    check("watch_blocked", res.status == "blocked", res.status)
    check("watch_rule_category",
          res.policy.rule == "CATEGORY_NOT_ALLOWED", res.policy.rule)
    check("watch_no_payment", res.order is None and res.capture_id == "")

    # ---- policy: approval threshold (hold -> approve -> pay) ------------
    res = make_agent().run("Get me nice wireless earbuds under $150")
    check("earbuds_held_for_approval",
          res.status == "awaiting_approval", res.status)
    check("earbuds_hold_rule",
          res.policy.rule == "APPROVAL_THRESHOLD", res.policy.rule)
    check("earbuds_no_payment_while_held", res.order is None)
    res2 = make_agent().run("Get me nice wireless earbuds under $150",
                            approve=True)
    check("earbuds_paid_after_approval", res2.status == "completed", res2.status)
    check("earbuds_pick", res2.chosen.product.id == "T002",
          res2.chosen.product.id if res2.chosen else "none")

    # ---- policy: per-order limit ----------------------------------------
    tight = Policy(max_per_order=30.0)
    res = make_agent(policy=tight).run(
        "Birthday gift for my mom, under $50 - she loves gardening")
    check("per_order_blocked", res.status == "blocked", res.status)
    check("per_order_rule", res.policy.rule == "MAX_PER_ORDER",
          res.policy.rule)

    # ---- policy: daily limit --------------------------------------------
    res = make_agent(daily_spent=490.0).run(
        "Birthday gift for my mom, under $50 - she loves gardening")
    check("daily_limit_blocked", res.status == "blocked", res.status)
    check("daily_limit_rule", res.policy.rule == "DAILY_LIMIT",
          res.policy.rule)

    # ---- policy: merchant blocklist --------------------------------------
    blocked = Policy(blocked_merchants=("GreenThumb Co",))
    res = make_agent(policy=blocked).run(
        "Birthday gift for my mom, under $50 - she loves gardening")
    check("merchant_blocked", res.status == "blocked", res.status)
    check("merchant_rule", res.policy.rule == "BLOCKED_MERCHANT",
          res.policy.rule)

    # ---- policy unit check ------------------------------------------------
    d = policy_check(Policy(), PRODUCTS[0], 0.0)
    check("policy_unit_allow", d.action == "allow" and d.rule == "ALL_CHECKS_PASSED",
          "%s/%s" % (d.action, d.rule))
    check("policy_allow_has_evidence", len(d.evidence) >= 3)

    # ---- no match ---------------------------------------------------------
    res = make_agent().run("Find a gift under $5")
    check("no_match_status", res.status == "no_match", res.status)

    # ---- mock PayPal client ------------------------------------------------
    mock = MockPayPal()
    client = PayPalClient(mock=mock)
    tok = client.token()
    check("mock_token", tok.startswith("MOCK-"), tok)
    order = client.create_order(34.99, description="test", ref_id="G001")
    check("mock_order_created", order["status"] == "CREATED")
    check("mock_order_amount", order["purchase_units"][0]["amount"]["value"] == "34.99")
    check("mock_order_approve_url", order["approve_url"].startswith("https://"))
    cap = client.capture_order(order["id"])
    cid, cstatus = _extract_capture(cap)
    check("mock_capture_completed", cstatus == "COMPLETED", str(cstatus))
    check("mock_capture_id", bool(cid and cid.startswith("MOCK-CAPTURE-")))
    check("mock_get_order", client.get_order(order["id"])["id"] == order["id"])

    # ---- AG Grid policy audit console ----------------------------------------
    from paypilot.catalog import Product
    from paypilot.grid import (audit_rows, render_grid_page, rows_payload,
                               summarize)

    pol = Policy()
    rows = audit_rows(PRODUCTS, pol)
    check("grid_audits_every_row", len(rows) == len(PRODUCTS),
          "%d vs %d" % (len(rows), len(PRODUCTS)))
    check("grid_decisions_are_valid",
          all(r["decision"] in ("allow", "block", "hold") for r in rows))
    check("grid_every_row_cites_a_rule", all(r["rule"] for r in rows))
    check("grid_every_row_has_evidence", all(r["evidence"] for r in rows))
    s = summarize(rows)
    check("grid_summary_counts_match",
          s["allow"] + s["block"] + s["hold"] == s["total"], str(s))
    check("grid_over_cap_is_blocked",
          all(r["decision"] == "block" for r in rows if r["price"] > pol.max_per_order))
    check("grid_approval_threshold_holds",
          all(r["decision"] == "hold" for r in rows
              if pol.approval_threshold <= r["price"] <= pol.max_per_order
              and r["category"] in pol.allowed_categories))
    check("grid_disallowed_category_blocked",
          all(r["rule"] == "CATEGORY_NOT_ALLOWED" for r in rows
              if r["category"] not in pol.allowed_categories))

    # daily spend is the interesting dial: raising it flips rows to DAILY_LIMIT
    base_blocked = summarize(rows)["block"]
    loaded = audit_rows(PRODUCTS, pol, daily_spent=480.0)
    check("grid_daily_limit_flips_rows",
          summarize(loaded)["block"] > base_blocked,
          "%d -> %d" % (base_blocked, summarize(loaded)["block"]))
    check("grid_daily_limit_rule_named",
          any(r["rule"] == "DAILY_LIMIT" for r in loaded))
    check("grid_deterministic",
          audit_rows(PRODUCTS, pol) == audit_rows(PRODUCTS, pol))

    # payload + page
    payload = rows_payload(PRODUCTS, pol, daily_spent=50.0)
    check("grid_payload_roundtrip",
          json.loads(json.dumps(payload))["summary"]["total"] == len(PRODUCTS))
    page = render_grid_page(PRODUCTS, pol)
    check("grid_page_loads_aggrid", "ag-grid-community" in page)
    check("grid_page_has_grid_mount", 'id="grid"' in page)
    check("grid_page_declares_columns", "columnDefs" in page)
    check("grid_page_emits_rows", "const ROWS =" in page)
    check("grid_page_has_fallback", "drawFallback" in page)
    check("grid_page_no_unreplaced_tokens", "__" not in page.split("<script>")[0])

    # hostile product name must not escape the <script> block
    nasty = [Product(id="X1", name="</script><script>alert(1)</script>",
                     category="books", price=9.0, rating=4.0, tags=[],
                     merchant="M", stock=1)]
    nasty_page = render_grid_page(nasty, pol)
    check("grid_escapes_script_breakout",
            nasty_page.count("</script>") == nasty_page.count("<script>"),
            "script tags unbalanced")
    check("grid_neutralises_breakout_payload",
          "<script>alert(1)</script>" not in nasty_page)

    # live HTTP: /grid renders and /api/rows returns JSON
    from http.server import ThreadingHTTPServer
    from paypilot.web import make_handler
    import threading
    import urllib.request

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(CATALOG, False))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = "http://127.0.0.1:%d" % httpd.server_address[1]
        with urllib.request.urlopen(base + "/grid", timeout=10) as r:
            body = r.read().decode("utf-8")
        check("http_grid_serves_page", "<title>PayPilot" in body and "ROWS =" in body)
        with urllib.request.urlopen(base + "/api/rows?spent=300", timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        check("http_api_rows_json", data["summary"]["total"] == len(PRODUCTS))
        check("http_api_rows_honours_spent",
              data["daily_spent"] == 300.0 and data["summary"]["block"] > 0)
        with urllib.request.urlopen(base + "/grid?spent=9999", timeout=10) as r:
            big = r.read().decode("utf-8")
        check("http_grid_extreme_spent_still_renders", "ROWS =" in big)
    finally:
        httpd.shutdown()

    # ---- REST API + OpenAPI + agent context -----------------------------------
    from paypilot.api import dispatch, load_spec, render_docs, to_json
    from paypilot.context import (build_context, context_text,
                                  render_context_markdown, summarize_spec,
                                  _yaml_to_ops)

    spec = load_spec()
    check("spec_declares_openapi_303", spec.startswith("openapi: 3.0.3"))
    ops = _yaml_to_ops(spec)
    check("spec_parses_six_operations", len(ops) == 6, str(len(ops)))
    check("spec_operations_have_summaries", all(o["summary"] for o in ops))
    check("spec_covers_all_endpoints",
          {o["path"] for o in ops} == {
              "/api/v1/health", "/api/v1/context", "/api/v1/catalog",
              "/api/v1/quote", "/api/v1/policy/check", "/api/v1/agent/run"},
          str({o["path"] for o in ops}))

    ctx = build_context(PRODUCTS)
    check("context_lists_operations", len(ctx["operations"]) == 6)
    check("context_states_rules", len(ctx["policy_rules"]) == 5)
    check("context_names_all_rules",
          {r["rule"] for r in ctx["policy_rules"]} == {
              "BLOCKED_MERCHANT", "CATEGORY_NOT_ALLOWED", "MAX_PER_ORDER",
              "DAILY_LIMIT", "APPROVAL_THRESHOLD"})
    check("context_describes_catalog", ctx["catalog_shape"]["size"] == len(PRODUCTS))
    check("context_has_contract", len(ctx["contract"]) >= 3)
    check("context_has_examples", len(ctx["examples"]) >= 5)
    md = context_text(PRODUCTS)
    check("context_markdown_fits_budget", len(md) <= 6000, str(len(md)))
    check("context_markdown_mentions_guardrail",
          "policy" in md.lower() and "APPROVAL_THRESHOLD" in md)
    check("context_is_stable", context_text(PRODUCTS) == md)
    trimmed = context_text(PRODUCTS, budget=400)
    check("context_respects_tiny_budget", len(trimmed) <= 700, str(len(trimmed)))

    # external spec injection (the APIMatic seam): drop a foreign spec in via env
    import tempfile
    ext = ("openapi: 3.0.0\ninfo:\n  title: Acme Payments\n  version: 1.0.0\n"
           "paths:\n  /v2/checkout/orders:\n    post:\n"
           "      summary: Create an order\n      operationId: createOrder\n"
           "  /v2/payments/captures:\n    get:\n"
           "      summary: Show capture\n      operationId: showCapture\n")
    tmpdir = tempfile.mkdtemp()
    extpath = os.path.join(tmpdir, "acme.yaml")
    with open(extpath, "w", encoding="utf-8") as f:
        f.write(ext)
    prev = os.environ.get("PAYPILOT_EXTRA_SPEC")
    os.environ["PAYPILOT_EXTRA_SPEC"] = extpath
    try:
        merged = build_context(PRODUCTS)
        check("context_absorbs_external_spec",
              merged.get("external_api", {}).get("operation_count") == 2,
              str(merged.get("external_api")))
        check("context_external_title", merged["external_api"]["title"] == "Acme Payments")
        check("context_external_in_markdown",
              "Acme Payments" in render_context_markdown(merged))
        check("summarize_spec_reads_paths",
              summarize_spec(ext)["operation_count"] == 2)
    finally:
        if prev is None:
            os.environ.pop("PAYPILOT_EXTRA_SPEC", None)
        else:
            os.environ["PAYPILOT_EXTRA_SPEC"] = prev
    check("context_external_spec_cleared",
          "external_api" not in build_context(PRODUCTS))

    # live HTTP over the REST API
    from http.server import ThreadingHTTPServer
    from paypilot.web import make_handler
    import threading
    import urllib.request
    import urllib.error

    def make_agent_api(daily_spent=0.0):
        from paypilot.agent import ShopAgent
        from paypilot.mock_paypal import MockPayPal
        from paypilot.reasoning import get_brain
        a = ShopAgent(PRODUCTS, brain=get_brain(prefer_llm=False),
                      paypal=PayPalClient(mock=MockPayPal()),
                      daily_spent=daily_spent)
        a.mode = "mock"
        return a

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(CATALOG, False))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def get(path):
        with urllib.request.urlopen(base + path, timeout=15) as r:
            return r.status, r.read().decode("utf-8")

    def post(path, obj, raw=None):
        data = (raw if raw is not None else json.dumps(obj)).encode("utf-8")
        req = urllib.request.Request(
            base + path, data=data, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    try:
        base = "http://127.0.0.1:%d" % httpd.server_address[1]
        st, body = get("/openapi.yaml")
        check("api_http_spec_served", st == 200 and body.startswith("openapi: 3.0.3"))
        st, body = get("/docs")
        check("api_http_docs_served", st == 200 and "swagger" in body.lower())
        check("api_http_docs_has_fallback", "fallback" in body)
        st, body = get("/api/v1/health")
        health = json.loads(body)
        check("api_http_health_ok",
              st == 200 and health["ok"] and health["data"]["status"] == "ok")
        check("api_http_health_reports_catalog",
              health["data"]["catalog_size"] == len(PRODUCTS))

        st, body = get("/api/v1/catalog?category=books&max_price=40")
        cat = json.loads(body)
        check("api_http_catalog_filters",
              cat["ok"] and all(c["product"]["category"] == "books"
                                and c["product"]["price"] <= 40
                                for c in cat["data"]))
        st, body = post("/api/v1/quote",
                        {"request": "birthday gift for my mom, under $50, she gardens"})
        q = json.loads(body)
        check("api_http_quote_ok", st == 200 and q["ok"])
        check("api_http_quote_has_intent", "budget" in q["data"]["intent"])
        check("api_http_quote_has_candidates", len(q["data"]["candidates"]) > 0)
        check("api_http_quote_moves_no_money",
              "does not move money" in q["data"]["note"])

        st, body = post("/api/v1/quote", {"request": "   "})
        check("api_http_empty_request_rejected",
              st == 400 and json.loads(body)["error"]["code"] == "EMPTY_REQUEST")

        pid = PRODUCTS[0].id
        st, body = post("/api/v1/policy/check", {"product_id": pid})
        pc = json.loads(body)
        check("api_http_policy_check_ok", st == 200 and pc["ok"])
        check("api_http_policy_check_decision",
              pc["data"]["decision"]["action"] in ("allow", "block", "hold"))
        check("api_http_policy_check_cites_rule", bool(pc["data"]["decision"]["rule"]))
        st, body = post("/api/v1/policy/check", {"product_id": "NOPE"})
        check("api_http_policy_unknown_product",
              st == 404 and json.loads(body)["error"]["code"] == "UNKNOWN_PRODUCT")
        st, body = post("/api/v1/policy/check",
                        {"product_id": pid, "daily_spent": 99999})
        check("api_http_policy_daily_limit_via_api",
              json.loads(body)["data"]["decision"]["action"] == "block")

        st, body = post("/api/v1/agent/run",
                        {"request": "cheapest gardening book"})
        run = json.loads(body)
        check("api_http_agent_run_ok", st == 200 and run["ok"])
        check("api_http_agent_run_status",
              run["data"]["status"] in ("completed", "blocked",
                                        "awaiting_approval", "no_match"))
        check("api_http_agent_run_has_trail",
              [t["phase"] for t in run["data"]["trail"]][:1] == ["PERCEIVE"])

        st, body = post("/api/v1/nope", {})
        check("api_http_unknown_endpoint_404",
              st == 404 and json.loads(body)["error"]["code"] == "NOT_FOUND")
        st, body = post("/api/v1/quote", None, raw="{not json")
        check("api_http_bad_json_400",
              st == 400 and json.loads(body)["error"]["code"] == "INVALID_JSON")
        st, body = get("/api/v1/context")
        cctx = json.loads(body)
        check("api_http_context_endpoint",
              cctx["ok"] and len(cctx["data"]["operations"]) == 6)
    finally:
        httpd.shutdown()

    # ---- Renewal Radar -------------------------------------------------------
    import contextlib
    import io
    from datetime import date as _date, timedelta as _td
    from paypilot.cli import main as cli_main
    from paypilot.renewals import (analyze, detect_subscriptions,
                                   parse_paypal_transactions,
                                   parse_transactions_csv, render_renewals_page,
                                   savings_plan, renewal_calendar, write_nudges)

    TXN_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "examples", "transactions.csv")
    with open(TXN_CSV, encoding="utf-8") as f:
        txns = parse_transactions_csv(f.read())
    check("renewals_parses_transactions", len(txns) == 42, str(len(txns)))
    TODAY = _date(2026, 10, 15)
    a = analyze(txns, today=TODAY)
    subs = {s["merchant"]: s for s in a["subscriptions"]}
    check("renewals_finds_subscriptions", len(a["subscriptions"]) == 6,
          str(sorted(subs)))

    check("renewals_price_hike_flagged",
          "PRICE_HIKE" in subs["NETFLIX.COM"]["flags"])
    check("renewals_price_hike_verdict",
          subs["NETFLIX.COM"]["verdict"] == "renegotiate")
    check("renewals_price_hike_evidence_cites_amounts",
          any("15.49" in e and "22.99" in e
              for e in subs["NETFLIX.COM"]["evidence"]))
    check("renewals_clean_subscription_is_keep",
          subs["SPOTIFY USA"]["verdict"] == "keep"
          and subs["SPOTIFY USA"]["flags"] == [])
    check("renewals_duplicate_flagged",
          "DUPLICATE" in subs["ADOBE SYSTEMS"]["flags"])
    check("renewals_duplicate_verdict_cancel",
          subs["ADOBE SYSTEMS"]["verdict"] == "cancel")
    check("renewals_duplicate_refuses_to_predict",
          subs["ADOBE SYSTEMS"]["next_charge"] is None)
    check("renewals_dormant_flagged",
          "DORMANT" in subs["IRON YARD GYM"]["flags"])
    check("renewals_yearly_not_mistaken_for_dormant",
          "DORMANT" not in subs["APPLE ICLOUD"]["flags"],
          str(subs["APPLE ICLOUD"]["flags"]))
    check("renewals_yearly_cadence", subs["APPLE ICLOUD"]["cadence"] == "yearly")
    check("renewals_weekly_not_mistaken_for_duplicate",
          "DUPLICATE" not in subs["BLUE BOTTLE COFFEE"]["flags"])
    check("renewals_weekly_cadence",
          subs["BLUE BOTTLE COFFEE"]["cadence"] == "weekly")
    check("renewals_one_off_spend_not_a_subscription",
          not any("AMAZON" in m or "UBER" in m for m in subs))
    check("renewals_monthly_cadence", subs["SPOTIFY USA"]["cadence"] == "monthly")

    check("renewals_next_charge_never_in_the_past",
          all(s["next_charge"] is None or s["next_charge"] >= TODAY.isoformat()
              for s in a["subscriptions"]))
    cal = a["calendar"]
    check("renewals_calendar_within_window",
          all(TODAY.isoformat() <= e["date"] <= (TODAY + _td(days=60)).isoformat()
              for e in cal))
    check("renewals_calendar_sorted",
          [e["date"] for e in cal] == sorted(e["date"] for e in cal))
    check("renewals_calendar_excludes_unpredictable",
          not any(e["merchant"] == "ADOBE SYSTEMS" for e in cal))

    plan = a["plan"]
    check("renewals_plan_conservative", plan["recoverable"] <= plan["annual_leak"])
    check("renewals_plan_items_only_for_actionable",
          all(i["verdict"] != "keep" for i in plan["items"]))
    check("renewals_plan_positive", plan["recoverable"] > 0)
    check("renewals_every_sub_has_nudge",
          all(s["nudge"] for s in a["subscriptions"]))
    check("renewals_nudge_addresses_merchant",
          all(s["merchant"].split()[0].lower() in s["nudge"].lower()
              for s in a["subscriptions"]))
    check("renewals_nudge_states_the_amount",
          any("%.2f" % s["amount"] in s["nudge"] for s in a["subscriptions"]))

    check("renewals_empty_input_safe", analyze([], today=TODAY)["subscriptions"] == [])
    check("renewals_single_charge_is_not_a_subscription",
          detect_subscriptions(
              parse_transactions_csv("2026-01-01,ACME,9.99\n"), today=TODAY) == [])
    check("renewals_ignores_refunded_rows",
          detect_subscriptions(parse_transactions_csv(
              "2026-01-01,ACME,9.99\n2026-02-01,ACME,9.99\n"), today=TODAY)
          != [])

    # date format tolerance
    for d in ("2026-03-05", "2026/3/5", "3/5/2026",
              '"Mar 5, 2026"', "2026-03-05T10:11:12Z"):
        got = parse_transactions_csv("%s,ACME,9.99\n%s,ACME,9.99\n" % (d, d))
        check("renewals_parses_date_%s" % d.replace("/", "_").replace(" ", "_")
              .replace(",", "").replace(":", ""),
              len(got) == 2 and got[0].day == _date(2026, 3, 5), str(got[:1]))
    check("renewals_parses_paypal_shape",
          len(parse_paypal_transactions({"transactions": [
              {"transaction_info": {
                  "transaction_id": "A1",
                  "transaction_initiation_date": "2026-01-05T00:00:00Z",
                  "transaction_amount": {"value": "15.49",
                                         "currency_code": "USD"}},
               "merchant": {"name": "ACME"}},
              {"transaction_info": {
                  "transaction_id": "A2",
                  "transaction_initiation_date": "2026-02-05T00:00:00Z",
                  "transaction_amount": {"value": "15.49",
                                         "currency_code": "USD"}},
               "merchant": {"name": "ACME"}}]})) == 2)
    check("renewals_paypal_shape_detected",
          len(detect_subscriptions(parse_paypal_transactions([
              {"transaction_info": {
                  "transaction_initiation_date": "2026-01-05",
                  "transaction_amount": {"value": "15.49"}},
               "merchant": {"name": "ACME"}},
              {"transaction_info": {
                  "transaction_initiation_date": "2026-02-04",
                  "transaction_amount": {"value": "15.49"}},
               "merchant": {"name": "ACME"}}]), today=TODAY)) == 1)
    check("renewals_skips_garbage_rows",
          parse_transactions_csv("date,merchant,amount\nnotadate,ACME,9.99\n"
                                 "2026-01-01,,9.99\n2026-01-01,ACME,abc\n") == [])

    page = render_renewals_page(txns, today=TODAY)
    check("renewals_page_has_calendar", "<table class='cal'>" in page)
    check("renewals_page_has_table", "NETFLIX.COM" in page)
    check("renewals_page_has_plan", "Est. saving" in page)
    check("renewals_page_no_unreplaced_tokens",
          "__KPIS__" not in page and "__CAL__" not in page)
    check("renewals_page_escapes_nudge",
          "<script" not in page.replace("<style", "").replace("</style", ""))

    # hostile merchant name must not inject markup
    bad = parse_transactions_csv(
        "2026-01-01,<img src=x onerror=alert(1)>,9.99\n"
        "2026-02-01,<img src=x onerror=alert(1)>,9.99\n")
    bad_page = render_renewals_page(bad, today=TODAY)
    check("renewals_page_neutralises_html_injection",
          "<img src=x" not in bad_page and "&lt;img" in bad_page)

    # live HTTP
    from http.server import ThreadingHTTPServer
    from paypilot.web import make_handler
    import threading
    import urllib.request

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(CATALOG, False))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = "http://127.0.0.1:%d" % httpd.server_address[1]
        with urllib.request.urlopen(base + "/renewals", timeout=20) as r:
            rp = r.read().decode("utf-8")
        check("renewals_http_page", r.status == 200 and "Renewal Radar" in rp)
        with urllib.request.urlopen(base + "/api/v1/renewals", timeout=20) as r:
            rd = json.loads(r.read().decode("utf-8"))
        check("renewals_http_api_ok", rd["ok"])
        check("renewals_http_api_has_subs", len(rd["data"]["subscriptions"]) >= 1,
              str(rd["data"])[:80])
        check("renewals_http_api_has_plan", "recoverable" in rd["data"]["plan"])
    finally:
        httpd.shutdown()

    buf4 = io.StringIO()
    with contextlib.redirect_stdout(buf4):
        rc_ren = cli_main(["renewals"])
    check("renewals_cli_runs", rc_ren == 0 and "Renewal Radar" in buf4.getvalue())
    check("renewals_cli_reports_hike", "PRICE_HIKE" in buf4.getvalue())

    # CLI surface for the same context
    import contextlib
    import io
    from paypilot.cli import main as cli_main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_ctx = cli_main(["context", "--format", "json"])
    check("cli_context_json_exits_zero", rc_ctx == 0)
    check("cli_context_json_valid",
          json.loads(buf.getvalue())["service"] == "PayPilot")
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        rc_doc = cli_main(["apidocs"])
    check("cli_apidocs_prints_spec",
          rc_doc == 0 and buf2.getvalue().startswith("openapi: 3.0.3"))
    buf3 = io.StringIO()
    with contextlib.redirect_stdout(buf3):
        cli_main(["context"])
    check("cli_context_markdown_default", buf3.getvalue().startswith("# PayPilot API context"))

    # ---- LLM brain fallback --------------------------------------------------
    try:
        LLMBrain("http://127.0.0.1:1/unreachable", "k", "m").parse("buy a book")
        check("llm_unreachable_raises", False, "no exception raised")
    except BrainError:
        check("llm_unreachable_raises", True)
    b = get_brain(prefer_llm=False)
    check("get_brain_offline_default", isinstance(b, OfflineBrain))

    # ---- deploy regression (2026-10-17): serve must honour $PORT ------------
    # Same bug class fixed in tasteweaver: a hard-coded --port default silently
    # ignored the PORT env var, so Render's assigned port was never bound and
    # the hosted demo returned 502. Assert the CLI path binds $PORT.
    _port = 18795
    _env = dict(os.environ)
    _env["PORT"] = str(_port)
    _env.pop("PAYPILOT_PAYPAL_MODE", None)
    _srv = subprocess.Popen([sys.executable, "-m", "paypilot", "serve"],
                            cwd=HERE, env=_env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _ok = False
        for _ in range(20):
            time.sleep(0.3)
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/" % _port, timeout=2) as _r:
                    _ok = _r.status == 200
                    break
            except Exception:
                continue
        check("cli_serve_port_env", _ok, "PORT=%d was not bound" % _port)
    finally:
        _srv.terminate()
        try:
            _srv.wait(timeout=5)
        except Exception:
            _srv.kill()

    print()
    print("=" * 60)
    print("%d passed, %d failed" % (PASS, FAIL))
    if FAILURES:
        print("failed: %s" % ", ".join(FAILURES))
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

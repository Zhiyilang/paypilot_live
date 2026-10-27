"""PayPilot command line.

Examples:
    python -m paypilot demo                     # 4 scripted scenarios, mock PayPal
    python -m paypilot shop "birthday gift for my mom under $50"
    python -m paypilot shop "wireless earbuds" --approve
    python -m paypilot serve --port 8780        # web demo UI

Modes (env or flags):
    PAYPILOT_PAYPAL_MODE=mock|live   (default mock - runs offline, no keys)
    PAYPAL_CLIENT_ID / PAYPAL_SECRET (live sandbox credentials)
    PAYPILOT_LLM_API_KEY / PAYPILOT_LLM_BASE_URL / PAYPILOT_LLM_MODEL (optional AI brain)
"""
import argparse
import os
import sys

from .agent import ShopAgent
from .catalog import load_catalog
from .mock_paypal import MockPayPal
from .paypal import PayPalClient
from .policy import Policy
from .reasoning import get_brain

DEFAULT_CATALOG = os.path.join(os.path.dirname(__file__), "..", "examples", "catalog.csv")

SCENARIOS = [
    ("Birthday gift for my mom, under $50 - she loves gardening", False,
     "happy path: allowed and paid"),
    ("Find me the cheapest mechanical keyboard", False,
     "cheapest-sort ranking, allowed and paid"),
    ("Buy a luxury watch for $500 as a treat", False,
     "blocked: luxury category not in allowlist"),
    ("Get me nice wireless earbuds under $150", True,
     "held for approval, then paid after human approval"),
]


def build_agent(catalog_path, mock=True, daily_spent=0.0, policy=None):
    products = load_catalog(catalog_path)
    brain = get_brain(prefer_llm=True)
    if mock:
        paypal = PayPalClient(mock=MockPayPal())
    else:
        paypal = PayPalClient(
            client_id=os.environ.get("PAYPAL_CLIENT_ID", ""),
            secret=os.environ.get("PAYPAL_SECRET", ""))
    return ShopAgent(products, policy=policy, brain=brain, paypal=paypal,
                     daily_spent=daily_spent)


def _print_result(res, fmt):
    if fmt in ("text", "both"):
        print(res.receipt_text)
        print()
    if fmt in ("html", "both"):
        print(res.receipt_html)


def cmd_shop(args):
    agent = build_agent(args.catalog, mock=not args.live)
    res = agent.run(args.request, approve=args.approve)
    _print_result(res, args.format)
    return 0 if res.status in ("completed",) else (2 if res.status == "blocked" else 3)


def cmd_demo(args):
    agent = build_agent(args.catalog, mock=not args.live)
    print("PayPilot demo - %d scenarios (PayPal mode: %s)\n"
          % (len(SCENARIOS), "LIVE sandbox" if args.live else "mock/offline"))
    for i, (req, approve, note) in enumerate(SCENARIOS, 1):
        print("=" * 70)
        print("Scenario %d: %s" % (i, req))
        print("Expectation: %s" % note)
        print("-" * 70)
        res = agent.run(req, approve=approve)
        print(res.receipt_text)
        print()
    return 0


def cmd_serve(args):
    from .web import serve
    serve(port=args.port, catalog_path=args.catalog, live=args.live)


def cmd_context(args):
    """Print the API + policy context an external agent needs."""
    from .catalog import load_catalog
    from .context import context_json, context_text
    products = load_catalog(args.catalog)
    if args.format == "json":
        print(context_json(products))
    else:
        print(context_text(products))
    return 0


def cmd_apidocs(args):
    """Print the OpenAPI document."""
    from .api import load_spec
    print(load_spec())
    return 0


def cmd_renewals(args):
    """Renewal Radar: what renews next, what went up, what is double-charged."""
    from .renewals import analyze, parse_transactions_csv
    with open(args.file, encoding="utf-8-sig") as f:
        txns = parse_transactions_csv(f.read())
    a = analyze(txns)
    p = a["plan"]
    print("Renewal Radar - %d transactions, %d subscriptions"
          % (a["transaction_count"], len(a["subscriptions"])))
    print("  annual recurring $%.2f - recoverable ~$%.2f"
          % (p["annual_leak"], p["recoverable"]))
    print()
    for s in a["subscriptions"]:
        print("  %-22s %-9s $%-8.2f %-12s next=%-11s %s"
              % (s["merchant"], s["cadence"], s["amount"], s["verdict"],
                 s["next_charge"] or "-", ",".join(s["flags"]) or "-"))
        for e in s["evidence"][:2]:
            print("        - %s" % e)
        print("        nudge: %s" % s["nudge"])
    if args.json:
        import json
        print(json.dumps(a, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="paypilot",
                                 description="AI shopping agent with policy guardrails")
    ap.add_argument("--catalog", default=DEFAULT_CATALOG,
                    help="path to product catalog CSV")
    sub = ap.add_subparsers(dest="cmd")

    p_shop = sub.add_parser("shop", help="run one shopping request")
    p_shop.add_argument("request", help="natural-language shopping request")
    p_shop.add_argument("--approve", action="store_true",
                        help="pre-approve orders held by the approval threshold")
    p_shop.add_argument("--live", action="store_true",
                        help="use live PayPal sandbox (needs credentials)")
    p_shop.add_argument("--format", choices=["text", "html", "both"],
                        default="text")
    p_shop.set_defaults(func=cmd_shop)

    p_demo = sub.add_parser("demo", help="run the 4 scripted scenarios")
    p_demo.add_argument("--live", action="store_true")
    p_demo.set_defaults(func=cmd_demo)

    p_serve = sub.add_parser("serve", help="start the web demo UI")
    # default MUST be None: web.serve() falls back to the PORT env var, which
    # is how Render/Heroku-style hosts tell us the port to bind. A hard-coded
    # default here would silently ignore $PORT and the host would 502.
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--live", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    p_ctx = sub.add_parser("context",
                           help="print API + policy context for an LLM agent")
    p_ctx.add_argument("--format", choices=["markdown", "json"],
                       default="markdown")
    p_ctx.set_defaults(func=cmd_context)

    p_spec = sub.add_parser("apidocs", help="print the OpenAPI 3.0.3 spec")
    p_spec.set_defaults(func=cmd_apidocs)

    p_ren = sub.add_parser("renewals",
                           help="find recurring charges, hikes and duplicates")
    p_ren.add_argument("--file", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "examples", "transactions.csv"),
        help="transaction CSV (date,merchant,amount)")
    p_ren.add_argument("--json", action="store_true")
    p_ren.set_defaults(func=cmd_renewals)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

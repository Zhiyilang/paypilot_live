"""PayPilot deployment entry point (for hosted demo / cloud platforms).

Reads PORT from the environment (default 8780) and serves the web demo UI.
PayPal mode: mock (offline) by default so any judge can run it with zero setup;
set PAYPAL_CLIENT_ID/PAYPAL_SECRET and PAYPILOT_PAYPAL_MODE=live for sandbox.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paypilot.web import serve

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8780"))
    catalog = os.environ.get(
        "PAYPILOT_CATALOG",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "examples", "catalog.csv"))
    live = os.environ.get("PAYPILOT_PAYPAL_MODE", "mock").lower() == "live"
    serve(port=port, catalog_path=catalog, live=live)

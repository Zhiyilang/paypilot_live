"""In-process PayPal mock: same interface as the live REST client.

Powers offline tests, the eval suite, and one-command demo mode, so the agent's
payment code path is identical whether you are online or not.
"""
import time
import uuid


class MockPayPal:
    def __init__(self):
        self.orders = {}
        self.tokens = []
        self.fail_next_capture = False

    def token(self):
        tok = "MOCK-" + uuid.uuid4().hex[:16]
        self.tokens.append(tok)
        return tok

    def create_order(self, amount, currency="USD", description="", ref_id=""):
        oid = "MOCK-ORDER-" + uuid.uuid4().hex[:10].upper()
        order = {
            "id": oid,
            "status": "CREATED",
            "intent": "CAPTURE",
            "purchase_units": [{
                "amount": {"currency_code": currency, "value": "%.2f" % amount},
                "description": description[:127],
                "reference_id": ref_id[:256],
            }],
            "approve_url": "https://www.sandbox.paypal.com/checkoutnow?token=" + oid,
            "created": time.time(),
        }
        self.orders[oid] = order
        return order

    def capture_order(self, order_id):
        o = self.orders.get(order_id)
        if not o:
            raise KeyError("unknown order %s" % order_id)
        if self.fail_next_capture:
            self.fail_next_capture = False
            raise RuntimeError("mock capture failure (simulated)")
        if o["status"] == "COMPLETED":
            return o
        cid = "MOCK-CAPTURE-" + uuid.uuid4().hex[:10].upper()
        o["status"] = "COMPLETED"
        o["capture"] = {"id": cid, "status": "COMPLETED",
                        "amount": o["purchase_units"][0]["amount"],
                        "time": time.time()}
        return o

    def get_order(self, order_id):
        o = self.orders.get(order_id)
        if not o:
            raise KeyError("unknown order %s" % order_id)
        return o

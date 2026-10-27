"""The PayPilot agent loop: PERCEIVE -> SEARCH -> SELECT -> POLICY ->
APPROVAL -> EXPLAIN -> EXECUTE -> CAPTURE -> RECEIPT.

Every phase appends an evidence-cited step, so the full decision trail -
including what the policy engine rejected and why - is auditable.
"""
from dataclasses import dataclass, field

from . import catalog as catalog_mod
from .policy import Policy, check as policy_check
from .reasoning import get_brain, OfflineBrain
from . import receipts as receipts_mod


@dataclass
class Step:
    phase: str
    detail: str
    evidence: list = field(default_factory=list)


@dataclass
class RunResult:
    request: str
    intent: object
    steps: list
    candidates: list
    chosen: object
    policy: object
    status: str                      # completed | blocked | awaiting_approval | no_match
    order: dict = None
    capture_id: str = ""
    amount: float = 0.0
    explanation: str = ""
    receipt_text: str = ""
    receipt_html: str = ""


def _extract_capture(resp):
    """Normalize capture id/status across live PayPal and mock shapes."""
    try:
        caps = resp["purchase_units"][0]["payments"]["captures"]
        return caps[0]["id"], caps[0]["status"]
    except (KeyError, IndexError, TypeError):
        c = resp.get("capture")
        if c:
            return c["id"], c["status"]
        return None, resp.get("status")


class ShopAgent:
    def __init__(self, products, policy=None, brain=None, paypal=None,
                 daily_spent=0.0):
        self.products = products
        self.policy = policy or Policy()
        self.brain = brain or get_brain()
        self.paypal = paypal
        self.daily_spent = daily_spent

    def run(self, request_text, approve=False):
        steps = []

        # 1. PERCEIVE - parse intent (LLM or offline heuristic)
        intent = self.brain.parse(request_text)
        steps.append(Step(
            "PERCEIVE",
            "understood the request via %s engine" % intent.source,
            ["occasion=%s" % (intent.occasion or "-"),
             "recipient=%s" % (intent.recipient or "-"),
             "budget=%s" % ("$%.2f" % intent.budget if intent.budget else "-"),
             "category_hint=%s" % (intent.category_hint or "-"),
             "keywords=%s" % intent.keywords]))

        # 2. SEARCH - rank candidates with evidence
        sort = "price_asc" if intent.cheapest else "value"
        cands = catalog_mod.search(
            self.products, keywords=intent.keywords,
            category=intent.category_hint or None,
            max_price=intent.budget, sort=sort)
        steps.append(Step(
            "SEARCH",
            "ranked %d candidate(s) from the catalog" % len(cands),
            ["%s %s $%.2f (score %.2f)" % (c.product.id, c.product.name,
                                           c.product.price, c.score)
             for c in cands[:3]] or ["no candidates survived the filters"]))
        if not cands:
            res = RunResult(request_text, intent, steps, [], None, None,
                            "no_match")
            res.receipt_text, res.receipt_html = receipts_mod.render(res)
            return res

        # 3. SELECT - best candidate
        chosen = cands[0]
        steps.append(Step(
            "SELECT",
            "selected %s (%s) at $%.2f" % (chosen.product.name,
                                           chosen.product.id,
                                           chosen.product.price),
            chosen.evidence +
            ["selection score %.2f - highest of %d candidate(s)"
             % (chosen.score, len(cands))]))

        # 4. POLICY - guardrails (cannot be overridden by the model)
        dec = policy_check(self.policy, chosen.product, self.daily_spent)
        steps.append(Step("POLICY", "%s - %s" % (dec.action.upper(), dec.detail),
                          dec.evidence))
        if dec.action == "block":
            res = RunResult(request_text, intent, steps, cands, chosen, dec,
                            "blocked")
            res.receipt_text, res.receipt_html = receipts_mod.render(res)
            return res
        if dec.action == "hold" and not approve:
            steps.append(Step("APPROVAL",
                              "held for human approval - no payment attempted",
                              ["price $%.2f >= approval threshold $%.2f"
                               % (chosen.product.price,
                                  self.policy.approval_threshold)]))
            res = RunResult(request_text, intent, steps, cands, chosen, dec,
                            "awaiting_approval")
            res.receipt_text, res.receipt_html = receipts_mod.render(res)
            return res
        if dec.action == "hold" and approve:
            steps.append(Step("APPROVAL", "human approved the purchase",
                              ["approve=True was passed to the agent"]))

        # 5. EXPLAIN - natural-language justification (AI)
        explanation = self.brain.explain(intent, chosen.product, chosen.evidence)
        steps.append(Step("EXPLAIN", explanation,
                          ["engine: %s" % intent.source]))

        # 6. EXECUTE - create the PayPal order
        if self.paypal is None:
            steps.append(Step("EXECUTE", "no payment client configured - dry run",
                              []))
            res = RunResult(request_text, intent, steps, cands, chosen, dec,
                            "completed", amount=chosen.product.price,
                            explanation=explanation)
            res.receipt_text, res.receipt_html = receipts_mod.render(res)
            return res
        order = self.paypal.create_order(
            chosen.product.price,
            description="PayPilot: %s" % chosen.product.name,
            ref_id=chosen.product.id)
        steps.append(Step(
            "EXECUTE",
            "created PayPal order %s (%s)" % (order.get("id"), order.get("status")),
            ["intent=CAPTURE",
             "amount=$%.2f %s" % (chosen.product.price,
                                  order["purchase_units"][0]["amount"]["currency_code"]),
             "approve_url=%s" % order.get("approve_url", "-")]))

        # 7. CAPTURE - take the payment
        cap = self.paypal.capture_order(order["id"])
        cid, cstatus = _extract_capture(cap)
        steps.append(Step(
            "CAPTURE",
            "payment %s (capture id %s)" % (cstatus, cid),
            ["order=%s" % order.get("id"),
             "capture=%s" % (cid or "-")]))

        res = RunResult(request_text, intent, steps, cands, chosen, dec,
                        "completed", order=order, capture_id=cid or "",
                        amount=chosen.product.price, explanation=explanation)
        res.receipt_text, res.receipt_html = receipts_mod.render(res)
        return res

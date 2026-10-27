"""Transparent spending-policy engine: the guardrails around the agent's wallet.

An autonomous agent that can spend money needs rules it cannot talk its way
past. Every decision cites the exact policy field that produced it.
"""
from dataclasses import dataclass, field


@dataclass
class Policy:
    max_per_order: float = 200.0        # hard cap on a single order
    daily_limit: float = 500.0          # hard cap on total spend per day
    approval_threshold: float = 100.0   # at/above this, a human must approve
    allowed_categories: tuple = ("garden", "electronics", "apparel", "books")
    blocked_merchants: tuple = ()


@dataclass
class PolicyDecision:
    action: str      # "allow" | "block" | "hold"
    rule: str        # rule id, e.g. "MAX_PER_ORDER"
    detail: str
    evidence: list = field(default_factory=list)


def check(policy, product, daily_spent=0.0):
    """Evaluate one purchase against the policy. First violated rule wins."""
    if product.merchant.lower() in [m.lower() for m in policy.blocked_merchants]:
        return PolicyDecision(
            "block", "BLOCKED_MERCHANT",
            "merchant '%s' is on the blocklist" % product.merchant,
            ["policy.blocked_merchants contains '%s'" % product.merchant])
    if product.category not in policy.allowed_categories:
        return PolicyDecision(
            "block", "CATEGORY_NOT_ALLOWED",
            "category '%s' is not in the allowlist" % product.category,
            ["product %s category = '%s'" % (product.id, product.category),
             "policy.allowed_categories = %s" % (list(policy.allowed_categories),)])
    if product.price > policy.max_per_order:
        return PolicyDecision(
            "block", "MAX_PER_ORDER",
            "price $%.2f exceeds the per-order limit of $%.2f"
            % (product.price, policy.max_per_order),
            ["product %s price $%.2f" % (product.id, product.price),
             "policy.max_per_order = $%.2f" % policy.max_per_order])
    if daily_spent + product.price > policy.daily_limit:
        return PolicyDecision(
            "block", "DAILY_LIMIT",
            "daily spend would reach $%.2f, over the $%.2f daily limit"
            % (daily_spent + product.price, policy.daily_limit),
            ["already spent today $%.2f" % daily_spent,
             "policy.daily_limit = $%.2f" % policy.daily_limit])
    if product.price >= policy.approval_threshold:
        return PolicyDecision(
            "hold", "APPROVAL_THRESHOLD",
            "price $%.2f is at or above the $%.2f approval threshold - "
            "a human must approve" % (product.price, policy.approval_threshold),
            ["product %s price $%.2f" % (product.id, product.price),
             "policy.approval_threshold = $%.2f" % policy.approval_threshold])
    ev = [
        "price $%.2f within per-order limit $%.2f" % (product.price, policy.max_per_order),
        "category '%s' allowed" % product.category,
        "daily total would be $%.2f of $%.2f" % (daily_spent + product.price, policy.daily_limit),
        "price $%.2f below approval threshold $%.2f" % (product.price, policy.approval_threshold),
    ]
    return PolicyDecision("allow", "ALL_CHECKS_PASSED", "order is within policy", ev)

"""The agent's brain: intent parsing + natural-language justification.

Two engines behind one interface:
- OfflineBrain: deterministic heuristics, no network, fully testable.
- LLMBrain: any OpenAI-compatible chat endpoint (env PAYPILOT_LLM_BASE_URL /
  PAYPILOT_LLM_API_KEY / PAYPILOT_LLM_MODEL), e.g. Xiaomi MiMo. Falls back to
  OfflineBrain on any failure, so the agent never breaks because a model is
  unavailable.

Both engines emit a structured Intent; the rest of the agent is engine-agnostic.
"""
import json
import os
import re
import urllib.request
from dataclasses import dataclass, field


@dataclass
class Intent:
    raw: str
    occasion: str = ""
    recipient: str = ""
    budget: float = None
    keywords: list = field(default_factory=list)
    category_hint: str = ""
    cheapest: bool = False
    source: str = "offline"


class BrainError(Exception):
    pass


CATEGORY_HINTS = {
    "garden": ["garden", "gardening", "plant", "plants", "flower", "flowers",
               "mom", "mother", "mum", "green thumb", "outdoor"],
    "electronics": ["keyboard", "earbuds", "headphone", "headphones", "usb",
                    "electronics", "gadget", "tech", "computer", "laptop", "mouse"],
    "apparel": ["scarf", "socks", "mug", "wallet", "apparel", "clothes",
                "jacket", "hat"],
    "books": ["book", "books", "novel", "journal", "cookbook", "read",
              "reading"],
    "luxury": ["luxury", "watch", "designer", "sunglasses", "premium"],
}

STOP_WORDS = {
    "the", "a", "an", "for", "my", "our", "me", "you", "and", "or", "but",
    "with", "under", "below", "less", "than", "up", "to", "max", "maximum",
    "buy", "find", "get", "give", "want", "need", "please", "she", "he",
    "they", "her", "his", "their", "loves", "love", "likes", "like", "that",
    "this", "some", "something", "nice", "good", "gift", "present", "treat",
    "birthday", "anniversary", "christmas", "holiday", "graduation",
    "wedding", "cheapest", "lowest", "price", "dollars", "usd",
}

BUDGET_PHRASE_RE = re.compile(
    r"(?:under|below|less\s+than|up\s+to|max(?:imum)?(?:\s+of)?|budget(?:\s+of)?)\s*"
    r"\$?\s*(\d+(?:\.\d+)?)", re.I)
DOLLAR_RE = re.compile(r"\$\s*(\d+(?:\.\d+)?)")


def _offline_parse(text):
    t = text.lower()
    budget = None
    m = BUDGET_PHRASE_RE.search(t)
    if m:
        budget = float(m.group(1))
    else:
        m = DOLLAR_RE.search(t)
        if m:
            budget = float(m.group(1))
    occasion = ""
    for occ in ("birthday", "anniversary", "christmas", "holiday",
                "graduation", "wedding", "thank"):
        if occ in t:
            occasion = occ
            break
    recipient = ""
    m = re.search(r"\bfor\s+(?:my|our)\s+(\w+)", t)
    if m:
        recipient = m.group(1)
    keywords = []
    for w in re.findall(r"[a-z]{3,}", t):
        if w not in STOP_WORDS and w not in keywords:
            keywords.append(w)
    category_hint = ""
    best_hits = 0
    for cat, hints in CATEGORY_HINTS.items():
        hits = sum(1 for h in hints if h in t)
        if hits > best_hits:
            best_hits = hits
            category_hint = cat
    cheapest = bool(re.search(r"cheapest|lowest\s+price|least\s+expensive", t))
    return Intent(raw=text, occasion=occasion, recipient=recipient,
                  budget=budget, keywords=keywords, category_hint=category_hint,
                  cheapest=cheapest, source="offline")


class OfflineBrain:
    """Deterministic heuristic brain. No network. Used as the universal fallback."""

    def parse(self, text):
        return _offline_parse(text)

    def explain(self, intent, product, evidence):
        bits = []
        if intent.occasion:
            bits.append("for a %s" % intent.occasion)
        if intent.recipient:
            bits.append("for your %s" % intent.recipient)
        who = (" ".join(bits)) if bits else "for you"
        return ("I picked %s ($%.2f, rated %.1f/5) %s. It matches your request on "
                "%d signal(s) and is the best-rated option that fits your budget."
                % (product.name, product.price, product.rating, who, len(evidence)))


_LLM_SYSTEM = (
    "You are the intent parser of a careful shopping agent. Extract the user's "
    "shopping intent and reply with ONLY a JSON object, no prose, no code "
    "fences. Schema: {\"occasion\": string, \"recipient\": string, "
    "\"budget\": number|null, \"keywords\": string[], \"category_hint\": string, "
    "\"cheapest\": boolean}. category_hint must be one of: garden, electronics, "
    "apparel, books, luxury, or \"\" if unclear. budget is the maximum price in "
    "USD as a number, or null. keywords are 1-6 lowercase product words.")


class LLMBrain:
    """OpenAI-compatible chat-completions brain (works with Xiaomi MiMo etc.)."""

    def __init__(self, base_url, api_key, model):
        if not api_key:
            raise BrainError("PAYPILOT_LLM_API_KEY is not set")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def _chat(self, messages, temperature=0.0):
        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }).encode()
        req = urllib.request.Request(
            self.base_url + "/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.api_key,
                     "User-Agent": "PayPilot/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read().decode())
        except Exception as e:
            raise BrainError("LLM request failed: %s" % e)
        try:
            return d["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise BrainError("unexpected LLM response shape")

    def parse(self, text):
        content = self._chat([
            {"role": "system", "content": _LLM_SYSTEM},
            {"role": "user", "content": text},
        ])
        try:
            raw = content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            d = json.loads(raw)
            budget = d.get("budget")
            return Intent(
                raw=text,
                occasion=str(d.get("occasion") or ""),
                recipient=str(d.get("recipient") or ""),
                budget=float(budget) if budget else None,
                keywords=[str(k).lower() for k in (d.get("keywords") or [])][:6],
                category_hint=str(d.get("category_hint") or ""),
                cheapest=bool(d.get("cheapest")),
                source="llm",
            )
        except Exception as e:
            raise BrainError("could not parse LLM intent JSON: %s" % e)

    def explain(self, intent, product, evidence):
        try:
            return self._chat([
                {"role": "system", "content":
                 "You are a shopping agent explaining one purchase decision in "
                 "2-3 plain sentences. Be concrete: name the product, the price, "
                 "the rating, and why it fits the request. No markdown."},
                {"role": "user", "content":
                 "Request: %s\nChosen: %s ($%.2f, rated %.1f/5, category %s)\n"
                 "Evidence: %s" % (intent.raw, product.name, product.price,
                                   product.rating, product.category,
                                   "; ".join(evidence))},
            ], temperature=0.3)
        except BrainError:
            return OfflineBrain().explain(intent, product, evidence)


def get_brain(prefer_llm=True):
    """Return an LLMBrain when configured, else OfflineBrain. Never raises."""
    if prefer_llm and os.environ.get("PAYPILOT_LLM_API_KEY"):
        try:
            return LLMBrain(
                os.environ.get("PAYPILOT_LLM_BASE_URL",
                               "https://token-plan-cn.xiaomimimo.com/v1"),
                os.environ["PAYPILOT_LLM_API_KEY"],
                os.environ.get("PAYPILOT_LLM_MODEL", "mimo-v2-flash"))
        except BrainError:
            pass
    return OfflineBrain()

"""Product catalog: CSV loading and evidence-cited candidate ranking."""
import csv
import re
from dataclasses import dataclass, field


@dataclass
class Product:
    id: str
    name: str
    category: str
    price: float
    rating: float
    tags: list
    merchant: str
    stock: int

    @property
    def value_score(self):
        """Rating dominates; mild price penalty so value beats hype."""
        return self.rating * 2.0 - (self.price / 100.0)


def load_catalog(path):
    """Load a product catalog from CSV. Returns list[Product]."""
    products = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            products.append(Product(
                id=row["id"].strip(),
                name=row["name"].strip(),
                category=row["category"].strip().lower(),
                price=float(row["price"]),
                rating=float(row["rating"]),
                tags=[t.strip().lower() for t in (row.get("tags") or "").split(";") if t.strip()],
                merchant=(row.get("merchant") or "").strip(),
                stock=int(float(row.get("stock") or 0)),
            ))
    return products


@dataclass
class Candidate:
    product: Product
    score: float
    evidence: list = field(default_factory=list)


def search(products, keywords=None, category=None, max_price=None,
           sort="value", in_stock_only=True):
    """Rank products against an intent.

    Every candidate carries evidence strings explaining why it matched,
    so the agent can cite file/row/field-level reasons for its choice.
    sort: "value" (default) or "price_asc" (for "cheapest" requests).
    """
    keywords = [k.lower() for k in (keywords or [])]
    out = []
    for p in products:
        if in_stock_only and p.stock <= 0:
            continue
        if category and p.category != category.lower():
            continue
        if max_price is not None and p.price > max_price:
            continue
        ev = ["in stock (%d units)" % p.stock, "rating %.1f/5" % p.rating]
        if max_price is not None:
            ev.append("price $%.2f within budget $%.2f (headroom $%.2f)"
                      % (p.price, max_price, max_price - p.price))
        else:
            ev.append("price $%.2f" % p.price)
        score = p.value_score
        haystack = " ".join([p.name, p.category] + p.tags).lower()
        tokens = set(re.findall(r"[a-z]+", haystack))
        hits = 0
        for kw in keywords:
            if not kw:
                continue
            matched = kw in haystack
            if not matched:
                # stem-ish match: "gardening" ~ "garden" (min 4 chars both ways)
                for t in tokens:
                    if min(len(t), len(kw)) >= 4 and (t.startswith(kw) or kw.startswith(t)):
                        matched = True
                        break
            if matched:
                hits += 1
                ev.append("matches '%s' (name/category/tags)" % kw)
        if keywords and hits == 0:
            continue  # keywords were given: only keyword-matching products qualify
        score += hits * 1.5
        out.append(Candidate(product=p, score=round(score, 3), evidence=ev))
    if sort == "price_asc":
        out.sort(key=lambda c: c.product.price)
    else:
        out.sort(key=lambda c: c.score, reverse=True)
    return out

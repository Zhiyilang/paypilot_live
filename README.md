# PayPilot

**An AI shopping agent with policy guardrails that executes real PayPal sandbox checkouts.**

Built for the [PayPal AI Hackathon 2026](https://paypalaihackathon.devpost.com/) ("Build What's Next with PayPal and AI").

PayPilot reads a natural-language shopping request, picks a product from a catalog *with evidence*, checks a spending policy **before** touching money, asks a human for approval when the amount is large, and then pays through the PayPal v2 Checkout API. Every decision in the trail is auditable — and the policy engine cannot be talked out of a "no" by the model.

- **Zero dependencies** — pure Python standard library. `pip install` nothing.
- **Two AI engines** — an OpenAI-compatible LLM brain (works with Xiaomi MiMo, OpenAI, etc.) *or* a deterministic offline brain. The agent degrades gracefully if the model is unavailable.
- **Guardrails first** — per-order cap, daily cap, category allowlist, merchant blocklist, and a human-approval threshold. The LLM never sees the policy override path.
- **Evidence-cited reasoning** — every candidate and every policy decision cites the fields it used.
- **172 assertions** — `python eval.py` runs the whole agent loop against an in-process PayPal mock, offline.

## Quick start

```bash
git clone https://github.com/Zhiyilang/paypilot.git
cd paypilot

# 1. run the test suite (offline, no keys needed)
python eval.py
# -> 172 passed, 0 failed

# 2. run the 4 scripted demo scenarios (mock PayPal, offline)
python -m paypilot demo

# 3. ask the agent something yourself (mock mode)
python -m paypilot shop "Birthday gift for my mom, under $50 - she loves gardening"
python -m paypilot shop "Find me the cheapest mechanical keyboard"
python -m paypilot shop "Get me nice wireless earbuds under $150" --approve

# 4. start the web demo UI (mock mode)
python -m paypilot serve --port 8780
# open http://127.0.0.1:8780
```

## How it works

```
request ──► PERCEIVE ──► SEARCH ──► SELECT ──► POLICY ──► APPROVAL ──► EXPLAIN ──► EXECUTE ──► CAPTURE ──► receipt
             (intent)     (rank +     (best       (hard       (human       (why, in     (PayPal     (payment
                           evidence)   cand.)      rules)      if ≥ thr.)   plain EN)    order)      captured)
```

**1. PERCEIVE** — the brain parses the request into a structured `Intent`: occasion, recipient, budget, keywords, category hint, "cheapest" flag. LLM brain when configured, offline heuristic otherwise.

**2. SEARCH** — the catalog is ranked with match evidence per candidate (`matches 'gardening' (name/category/tags)`, `price $34.99 within budget $50.00 (headroom $15.01)`, ...). Keyword filters are enforced: irrelevant products never reach the model.

**3. SELECT** — highest score wins (or lowest price when the user asked for the cheapest).

**4. POLICY** — the guardrail engine runs *before* any money moves:

| Rule | Effect |
|---|---|
| `BLOCKED_MERCHANT` | block: merchant on the blocklist |
| `CATEGORY_NOT_ALLOWED` | block: category outside the allowlist |
| `MAX_PER_ORDER` | block: price above the per-order cap |
| `DAILY_LIMIT` | block: spend would exceed the daily cap |
| `APPROVAL_THRESHOLD` | hold: a human must approve before payment |

**5. APPROVAL** — held orders are not paid until a human passes `--approve` (or ticks the box in the web UI).

**6. EXPLAIN** — the brain writes the justification in plain English ("I picked X because ..."), citing rating, price fit, and matched signals.

**7. EXECUTE / 8. CAPTURE** — PayPal v2 Checkout: OAuth2 client-credentials token → create order (`intent=CAPTURE`) → capture. The same client class runs against `api-m.sandbox.paypal.com` (live) or an in-process mock (offline), so tested code is shipped code.

**9. Receipt** — a plain-text receipt for the CLI and an HTML report with the full decision trail.

## Policy Audit Grid (AG Grid)

A single scenario proves one decision. The **Policy Audit Grid** proves the whole
policy surface: every catalog item is run through the same `policy.check()`
engine that gates real money, and rendered in [AG Grid Community](https://www.ag-grid.com/)
so a reviewer can sort, filter and audit hundreds of decisions at once.

Open it with `python -m paypilot serve` at **`/grid`** (or `/api/rows?spent=120`
for the raw JSON).

- **One row per product, one decision per row** — `allow` / `block` / `hold`,
  plus the exact rule id (`MAX_PER_ORDER`, `DAILY_LIMIT`, `CATEGORY_NOT_ALLOWED`,
  `APPROVAL_THRESHOLD`) and field-level evidence.
- **Click any row** → the rule and its evidence render below the grid.
- **Drag "already spent today"** → the `DAILY_LIMIT` rule flips rows to `block`
  live, which is the fastest way to see the guardrail actually bite.
- **Filter panel + CSV export** — AG Grid Community tool panel and
  `exportDataAsCsv()`; blocked-only toggle for triage.
- **Header KPIs** — rows audited, allowed / blocked / need-approval, total
  buying power, total value blocked by policy.
- **No build step, no CDN lock-in** — AG Grid comes from a CDN; if it is
  unreachable the page falls back to a plain HTML table with the same data, so
  the console still works in an air-gapped review environment.
- **Injection-safe embedding** — row data is JSON-escaped (`</` → `<\/`) so a
  hostile product name cannot break out of the `<script>` block (covered by
  `eval.py`).

The grid is *not* a decorative dashboard: it is how you verify that the
guardrails hold across the entire catalog, not just in the one scenario the
demo happens to run.

## REST API & agent context (OpenAPI 3.0.3)

The same loop is exposed as JSON, so other agents and dashboards can call the
guardrails instead of re-implementing them. There is **one** implementation of
"how PayPilot decides"; the CLI, the web UI, the grid and this API all route
through it.

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/v1/health` | liveness + PayPal mode + brain |
| `GET` | `/api/v1/catalog` | search/rank (`?category=&max_price=&q=&sort=`) |
| `GET` | `/api/v1/context` | API + policy digest, sized for an LLM prompt |
| `POST` | `/api/v1/quote` | intent + ranked candidates — **never pays** |
| `POST` | `/api/v1/policy/check` | `{product_id, daily_spent}` → allow/block/hold + rule |
| `POST` | `/api/v1/agent/run` | the full loop, optionally paying |

```bash
python -m paypilot serve                      # then:
curl -s localhost:8780/api/v1/health | jq
curl -s -X POST localhost:8780/api/v1/quote -H 'Content-Type: application/json' \
     -d '{"request":"birthday gift for my mom, under $50, she gardens"}' | jq
```

- **Spec**: `openapi.yaml`, served live at **`/openapi.yaml`**, browsable at
  **`/docs`** (Swagger UI, with an offline fallback page).
- **Postman**: `postman_collection.json` — import it, set `base_url`, run.
- **Agent context**: `GET /api/v1/context` returns a compact block (operations,
  the five policy rules, catalog shape, copy-pasteable curl examples) that fits
  in ~1.5k tokens, plus a Markdown rendering at
  `python -m paypilot context`. That is the piece you paste into a system prompt
  to let a *foreign* agent call PayPilot correctly on the first try.
- **Bring your own spec**: set `PAYPILOT_EXTRA_SPEC=/path/to/spec.yaml` and any
  other OpenAPI document (including an APIMatic-generated one for the PayPal
  REST API) is summarised into the same context block, so one prompt can hand an
  agent PayPilot *and* a third-party API.

Every response uses one envelope: `{"ok":true,"data":...,"meta":...}` or
`{"ok":false,"error":{"code":...,"message":...}}`.

## Renewal Radar

A shopping agent that only helps you *spend* is half a product. The same
transaction history that proves an agent can pay also shows what it pays for
month after month. **Renewal Radar** (`/renewals`, or
`python -m paypilot renewals`) reads it the other way round:

- **A 60-day calendar** of predicted charges, each from that merchant's own
  cadence (weekly / monthly / quarterly / yearly).
- **A verdict per subscription** — keep / review / cancel / renegotiate — each
  with the amounts and dates that produced it: `PRICE_HIKE` (NETFLIX $15.49 →
  $22.99), `DUPLICATE` (two identical $59.99 charges three days apart),
  `DORMANT` (charged monthly, silent for months).
- **A polite, ready-to-send nudge** per subscription. AI-written when a brain is
  configured, template-written offline.

Two deliberate refusals: it never flags a change that does not **persist** (so a
seasonal spike is not a price hike), and it never predicts a date it cannot
justify — an unexplained duplicate appears as a duplicate but gets **no**
renewal date. Deterministic facts; only the wording can be AI-written.

Savings are deliberately conservative: cancel counts at 100%, renegotiate 15%,
review 30%.

## Demo scenarios

`python -m paypilot demo` runs four scenarios that show the agent's full behavioural range:

| # | Request | What it demonstrates |
|---|---|---|
| 1 | "Birthday gift for my mom, under $50 - she loves gardening" | happy path: picks the Deluxe Garden Tool Set ($34.99), policy allows, pays |
| 2 | "Find me the cheapest mechanical keyboard" | "cheapest" intent → price-ascending ranking, pays |
| 3 | "Buy a luxury watch for $500 as a treat" | blocked: `CATEGORY_NOT_ALLOWED` — no payment attempted |
| 4 | "Get me nice wireless earbuds under $150" | held at `APPROVAL_THRESHOLD` ($129 ≥ $100) → pays only after human approval |

## Connecting the real PayPal sandbox

Mock mode is the default so anyone can run everything offline. To point the agent at the live sandbox:

1. Create a free PayPal Developer account at <https://developer.paypal.com/> and a sandbox REST app.
2. Export credentials:

```bash
export PAYPAL_CLIENT_ID="your-sandbox-client-id"
export PAYPAL_SECRET="your-sandbox-secret"
```

3. Run with `--live`:

```bash
python -m paypilot shop "wireless earbuds under $150" --live --approve
python -m paypilot serve --port 8780 --live
```

Live flow note: PayPal's hosted checkout requires a buyer to approve the order in a browser before capture. The agent prints the `approve_url` for the order it creates; after the sandbox buyer approves, the capture call completes the payment. (Mock mode runs the full loop in one step — that is what the demo and the eval suite use.)

## Adding an LLM brain

Any OpenAI-compatible chat endpoint works. Example with Xiaomi MiMo:

```bash
export PAYPILOT_LLM_BASE_URL="https://token-plan-cn.xiaomimimo.com/v1"
export PAYPILOT_LLM_API_KEY="your-key"
export PAYPILOT_LLM_MODEL="mimo-v2-flash"
```

The LLM does intent parsing and justification writing. If the endpoint is unreachable or returns unusable JSON, PayPilot falls back to the offline brain automatically — the agent never breaks because a model is down. The eval suite asserts this fallback behaviour.

## Configuration reference

| Env var | Default | Meaning |
|---|---|---|
| `PAYPILOT_PAYPAL_MODE` | `mock` | `mock` (offline) or `live` (sandbox) |
| `PAYPAL_CLIENT_ID` / `PAYPAL_SECRET` | — | sandbox REST app credentials (live mode) |
| `PAYPILOT_LLM_BASE_URL` | Xiaomi MiMo endpoint | OpenAI-compatible base URL |
| `PAYPILOT_LLM_API_KEY` | — | enables the LLM brain when set |
| `PAYPILOT_LLM_MODEL` | `mimo-v2-flash` | model name |

Policy defaults live in `paypilot/policy.py` (`Policy` dataclass): `max_per_order=200`, `daily_limit=500`, `approval_threshold=100`, allowed categories `garden/electronics/apparel/books`.

## Project layout

```
paypilot/
├── paypilot/
│   ├── __init__.py
│   ├── __main__.py        # python -m paypilot
│   ├── catalog.py         # CSV catalog + evidence-cited ranking
│   ├── policy.py          # spending guardrails (block/hold/allow)
│   ├── reasoning.py       # LLM brain + offline brain (one interface)
│   ├── paypal.py          # PayPal v2 REST client (token/order/capture)
│   ├── mock_paypal.py     # in-process PayPal mock (tests + demo mode)
│   ├── agent.py           # the agent loop
│   ├── receipts.py        # text + HTML receipts with decision trail
│   ├── cli.py             # shop / demo / serve commands
│   ├── web.py             # stdlib web demo UI
│   ├── grid.py            # AG Grid policy audit console (/grid, /api/rows)
│   ├── api.py             # REST API + OpenAPI serving (/api/v1/*, /docs)
│   ├── context.py         # agent-consumable API context
│   └── renewals.py        # Renewal Radar: cadence, hikes, duplicates
├── examples/
│   ├── catalog.csv        # 17 products across 5 categories
│   ├── requests.txt       # example natural-language requests
│   └── transactions.csv   # 42 transactions with planted hikes/duplicates
├── PAYPILOT_DEMO_SCRIPT.md  # <3min video storyboard + YouTube metadata
├── PAYPILOT_SUBMISSION.md   # Devpost submission text (copy-paste)
├── openapi.yaml           # OpenAPI 3.0.3 spec, served at /openapi.yaml
├── postman_collection.json
├── eval.py                # 172 assertions, runs offline
├── requirements.txt       # (empty - stdlib only)
├── .env.example
├── LICENSE                # MIT
└── README.md / README.zh.md
```

## Why an agent that can spend money needs guardrails

An LLM will happily rationalize a purchase you did not authorize. PayPilot's design assumption is the opposite: **the model proposes, the policy disposes.** Keyword filtering keeps irrelevant products away from the model; the policy engine runs after selection with hard rules; anything at or above the approval threshold waits for a human. The model writes the explanation — it never writes the permission.

## Hackathon notes

- Category targets: *Best Use of PayPal + AI*, *Best Use of Agentic Commerce*, *Most Creative*, *Best Demo Delivery*.
- Submission requirements covered: public repo with license (MIT) — yes; working prototype runnable by judges — yes, offline mock mode needs zero setup; demo video — recorded separately; documentation — this file.

## License

MIT — see [LICENSE](LICENSE).

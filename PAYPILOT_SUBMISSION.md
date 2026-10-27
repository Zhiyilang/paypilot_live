# PayPilot — Devpost submission (copy-paste ready)

**Hackathon:** Build What's Next with PayPal and AI
**Devpost:** https://paypalaihackathon.devpost.com/ （2026-10-22 实测在线，17,781 participants）
**Deadline:** Nov 12, 2026 15:00 GMT-5 = **北京 11-13 04:00**（Devpost 页逐字，与 reconscribe 口径一致）
**Repo:** https://gitee.com/ZhiYIlang/paypilot （当前版：web.py 10-22 有更新；公开 / MIT ✓，符合"public + license visible"硬规则；GitHub 镜像 https://github.com/Zhiyilang/paypilot 为 10-18 旧版）
**Prize structure (official页逐字):** 1st $12,000 / 2nd $8,000 / 3rd $5,000 / Most Creative $5k / Most Impactful $5k / Best Demo Delivery $5k / Best Use of PayPal+AI $5k / Best Use of Agentic Commerce $5k / Best Use of AG Grid 1st $5k、2nd $2k、3rd $1k×3 / Best Use of APIMatic $1k×3 / Best Use of Render（credits）
**Demo URL:** Render 已被挂起——登录 Render 恢复部署（render.yaml 一键）；兜底=提交里注明 "runs locally: python -m paypilot serve"（零依赖 stdlib，clone 即可跑，符合官方"judges must be able to actually run"要求，但 hosted URL 更稳）

---

## Step 1 — before the form (2 things only you can do)

1. **Devpost profile → GitHub username must be `Zhiyilang`** (it is currently
   `Zhiyilang074811`; judges check repo ownership).
2. **Record and upload the video** — script is in `PAYPILOT_DEMO_SCRIPT.md`
   (under 3 minutes, public on YouTube). Everything else below is copy-paste.

---

## Step 2 — the form

**Project name**
```
PayPilot — an AI shopping agent with guardrails it can't talk its way past
```

**Tagline** (one line)
```
An AI agent that can pay — and a deterministic policy engine that decides
whether it may.
```

**Description** — paste the whole block below; subheadings are literal.

### What it does

PayPilot turns a natural-language shopping request into a policy-checked,
evidence-cited purchase and a receipt.

The loop is `PERCEIVE → SEARCH → SELECT → POLICY → APPROVAL → EXPLAIN →
EXECUTE → CAPTURE → RECEIPT`. Money never moves before the policy engine runs,
and every phase returns the field-level evidence that produced it, so a reviewer
can audit *why* money did or did not move.

Three surfaces share one decision engine — there is exactly one implementation
of "how PayPilot decides":

- **Agent console** — type a request, watch the full trail. Ask for the most
  expensive telescope and the request comes back `BLOCKED` with rule
  `MAX_PER_ORDER`. Ask for something above $100 and it returns
  `awaiting_approval` and refuses to pay until a human approves.
- **Policy Audit Grid** (AG Grid) — every catalog item, one row, one decision.
  Sort, filter, click a row for its rule and evidence, export CSV. Drag
  "already spent today" and the `DAILY_LIMIT` rule flips rows to BLOCK live.
- **Renewal Radar** — reads the same transaction history the other way round:
  what renews in the next 60 days, what quietly went up (NETFLIX $15.49 →
  $22.99), what is being charged twice (two identical $59.99 charges three days
  apart), and a polite ready-to-send nudge for each one. It never invents a
  charge: an unexplained duplicate gets no predicted renewal date.

For other agents, the whole thing is a REST API described by an OpenAPI 3.0.3
document, plus a ~1,500-token context block that can be pasted into a system
prompt so a foreign agent calls it correctly on the first try.

### How we built it

**Python 3 standard library only.** No SDK, no framework, no build step —
`urllib`, `http.server`, `csv`, `re`, `dataclasses`. That was a deliberate
constraint: a payments agent a judge can read end to end is more trustworthy
than one buried in dependencies.

- **Policy engine** (`paypilot/policy.py`) — five deterministic rules, first
  violation wins: `BLOCKED_MERCHANT`, `CATEGORY_NOT_ALLOWED`, `MAX_PER_ORDER`,
  `DAILY_LIMIT`, `APPROVAL_THRESHOLD`. This module cannot be reached by the
  language model; the model proposes, the policy disposes.
- **Brains** (`paypilot/reasoning.py`) — an LLM brain (any OpenAI-compatible
  endpoint) and an offline heuristic brain behind one interface. Any network or
  parse failure degrades to offline, visibly, so the demo never dies on stage.
- **PayPal** (`paypilot/paypal.py`) — PayPal v2 Checkout: OAuth2
  client-credentials token → create order (`intent=CAPTURE`) → capture. The same
  client class runs against `api-m.sandbox.paypal.com` or an in-process mock,
  so tested code is shipped code.
- **Grid** (`paypilot/grid.py`) — AG Grid Community: column defs, custom cell
  renderers for the decision badges, filter tool panel, `exportDataAsCsv()`,
  pagination, row class rules. If the CDN is unreachable the page falls back to
  a plain HTML table with the same data.
- **API** (`paypilot/api.py`, `openapi.yaml`, `paypilot/context.py`) — six REST
  endpoints, a real OpenAPI 3.0.3 document served at `/openapi.yaml` and
  browsable at `/docs`, and a context builder that compresses the spec into
  something an LLM can actually use. `PAYPILOT_EXTRA_SPEC` merges any other
  OpenAPI document into the same context block.
- **Renewals** (`paypilot/renewals.py`) — cadence detection from raw
  transactions, price-hike and duplicate detection, a calendar, and nudges.
  Deterministic facts; only the wording can be AI-written.

### Challenges we ran into

- **The model must never be the last word on money.** Solved by making the
  policy engine a separate, non-overridable module the brain calls rather than
  advises.
- **Distinguishing a price hike from a seasonal spike.** Utilities and coffee
  go up and down naturally; flagging every change produces noise people ignore.
  Renewal Radar only flags a step that *persists* to the most recent charge.
- **Dormant vs. yearly.** A yearly renewal is not "dormant" nine months out, so
  the dormancy test is relative to each subscription's own cycle.
- **Not inventing charges.** A duplicate has no predictable cycle, so it is
  shown as a duplicate but deliberately given no predicted renewal date.
- **Embedding JSON safely.** Row data goes into a `<script>` block, so `</` is
  escaped — otherwise a crafted product name could break out of it. Covered by
  a test.

### What's next

- Real PayPal **subscription** APIs for Renegotiate/Cancel actions, so the nudge
  becomes a button instead of a copy-paste.
- Plaid / bank-statement ingestion so Renewal Radar reads every account, not one CSV.
- A merchant-side mode: the same engine as a pre-approval gate for a seller's agent.

### Accomplishments we're proud of

172 offline assertions, including a rogue-brain test where the model proposes a
tool that does not exist and then a destructive fix — both are rejected and not
one byte of data changes. Zero dependencies. Runs with no credentials at all.

---

**Technologies used** (Devpost tag field)
```
Python 3, Standard Library only, PayPal REST API v2 (Checkout / Orders /
Capture / OAuth2), AG Grid Community, OpenAPI 3.0.3, Swagger UI, Postman,
Render, OpenAI-compatible LLM API
```

**Sponsor categories to tick**
- [x] **Best Use of AG Grid / AG Studio** — Policy Audit Grid (`/grid`)
- [x] **Best Use of APIMatic** — `openapi.yaml` + the agent context layer
      (`/api/v1/context`, `PAYPILOT_EXTRA_SPEC`)
- [x] **Best Use of Postman** — `postman_collection.json`
- [x] **Best Use of Render** — `render.yaml`, one-click deploy
- [ ] Best Use of Channel3 — needs an API key we don't have

**Links**
- Repo: https://gitee.com/ZhiYIlang/paypilot（当前版；GitHub 镜像 https://github.com/Zhiyilang/paypilot 为 10-18 旧版）
- Video: `<your YouTube URL>`
- Demo: `<your Render URL>` (or note "runs locally: `python -m paypilot serve`")

**Built by:** Zhiyilang — solo.

---

## Judging criteria → where we score

| Criterion | What to point them at |
|---|---|
| Technological implementation | one decision engine across CLI / web / grid / REST API; 172 assertions; zero dependencies |
| Design | the decision trail — every phase cites its evidence; blocked/held are first-class outcomes, not errors |
| Potential impact | Renewal Radar turns the same payment data into money the user gets back |
| Innovation | guardrails the model cannot reach; API context sized for an LLM prompt |
| Presentation | the video's live DAILY_LIMIT flip and the 172/172 run |

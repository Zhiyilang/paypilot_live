# 📺 YouTube 上传包 · PayPilot（复制即用）

> 视频文件：`paypilot/paypilot_demo.mp4`（21.534s / 1280×720 / 283KB，实测 < 3min 合规）
> 用途：PayPal AI Hackathon（Build What's Next with PayPal and AI）Devpost 提交必填的公开演示视频链接。
> 硬截止：**2026-11-12 12:00 PT = 北京 11-13 04:00**。

## Title（标题，复制）

```
PayPilot — an AI shopping agent with guardrails it can't talk its way past (PayPal AI Hackathon)
```

## Description（描述，复制）

```
PayPilot turns a natural-language shopping request into a policy-checked,
evidence-cited purchase and a receipt — built on PayPal v2 Checkout
(OAuth2 → create order → capture) for the PayPal AI Hackathon.

The loop is PERCEIVE → SEARCH → SELECT → POLICY → APPROVAL → EXPLAIN →
EXECUTE → CAPTURE → RECEIPT. Money never moves before the deterministic
policy engine runs, and every phase returns the field-level evidence that
produced it, so a reviewer can audit why money did or did not move.

Three surfaces share one decision engine:
  • Agent console — ask for the most expensive telescope, get BLOCKED with
    rule MAX_PER_ORDER; ask above $100, get awaiting_approval until a human
    approves. The model proposes; the policy disposes.
  • Policy Audit Grid (AG Grid) — every catalog item, one row, one decision;
    drag "already spent today" and DAILY_LIMIT flips rows to BLOCK live.
  • Renewal Radar — price hikes (NETFLIX $15.49 → $22.99), duplicate charges
    ($59.99 twice, three days apart), and nudges. It never invents a charge.

Python 3 standard library only — zero dependencies, 172 offline assertions,
including a rogue-brain test where the model proposes a destructive fix:
rejected, not one byte of data changes. Runs with no credentials at all.

Repo (public, MIT): https://github.com/Zhiyilang/paypilot
Built for: Build What's Next with PayPal and AI — PayPal AI Hackathon 2026

#PayPal #AIagent #payments #guardrails #AGGrid #OpenAPI #Python #opensource #hackathon
```

## Tags（标签，复制）

```
PayPal, PayPal AI Hackathon, AI agent, payments, guardrails, policy engine,
AG Grid, OpenAPI, Python, standard library, zero-dependency, open source,
hackathon, Devpost
```

## 上传注意

- 可见性先设 **Unlisted**（评审能打开、又不公开），提交后是否转 Public 看你。
- 上传后把视频 URL 填进 `PAYPILOT_SUBMISSION.md` 的「Video」字段（第 144 行 `<your YouTube URL>` 占位），再整份粘进 Devpost。
- 提交前两件只有你能做的事（见 PAYPILOT_SUBMISSION.md Step 1）：① Devpost 个人资料 GitHub 用户名改成 **Zhiyilang**（现在是 Zhiyilang074811，评委核对仓归属）；② Demo URL——Render 服务已被挂起，登录 Render 控制台恢复部署（render.yaml 一键），或退而求其次在提交里注明 "runs locally: python -m paypilot serve"。

# PayPilot（中文说明）

**一个带花钱护栏的 AI 购物代理——直接执行真实的 PayPal 沙盒结账。**

为 [PayPal AI Hackathon 2026](https://paypalaihackathon.devpost.com/)（"Build What's Next with PayPal and AI"）而做。

PayPilot 读取一句大白话购物需求，从商品目录里**带证据地**挑选商品，在动钱之前先过一遍消费策略，金额超标时必须经人工批准，然后通过 PayPal v2 Checkout API 完成付款。整条决策链可审计——而且策略引擎不会被模型"说服"。

- **零第三方依赖**——纯 Python 标准库，不用 `pip install` 任何东西。
- **双 AI 引擎**——OpenAI 兼容的 LLM 大脑（可用小米 MiMo、OpenAI 等）**或**确定性离线大脑；模型不可用时自动降级，代理不会崩。
- **护栏优先**——单笔上限、每日上限、类目白名单、商家黑名单、人工审批阈值。LLM 永远拿不到"跳过策略"的通道。
- **证据引用式推理**——每个候选商品、每条策略判定都注明依据字段。
- **53 条断言**——`python eval.py` 用进程内 PayPal mock 离线跑完整代理闭环。

## 快速开始

```bash
git clone https://github.com/Zhiyilang/paypilot.git
cd paypilot

# 1. 跑测试套件（离线，无需任何密钥）
python eval.py
# -> 172 passed, 0 failed

# 2. 跑 4 个演示场景（mock PayPal，离线）
python -m paypilot demo

# 3. 自己问代理（mock 模式）
python -m paypilot shop "给我妈买个 50 美元以内的生日礼物，她喜欢园艺"
python -m paypilot shop "找个最便宜的机械键盘"
python -m paypilot shop "150 美元以内的无线耳机" --approve

# 4. 启动 Web 演示界面（mock 模式）
python -m paypilot serve --port 8780
# 打开 http://127.0.0.1:8780
```

## 工作原理

```
需求 ──► PERCEIVE ──► SEARCH ──► SELECT ──► POLICY ──► APPROVAL ──► EXPLAIN ──► EXECUTE ──► CAPTURE ──► 收据
          (意图解析)   (带证据排序)  (最优候选)   (硬规则)      (超标人工批)   (大白话解释)   (PayPal下单)   (完成支付)
```

1. **PERCEIVE**——大脑把需求解析成结构化 `Intent`：场合、收礼人、预算、关键词、类目提示、"最便宜"标记。有 LLM 用 LLM，没有就用离线启发式。
2. **SEARCH**——目录排序，每个候选都带匹配证据（如 `matches 'gardening' (name/category/tags)`、`price $34.99 within budget $50.00 (headroom $15.01)`）。关键词不相关的商品根本到不了模型面前。
3. **SELECT**——得分最高者胜出（用户说"最便宜"时按价格升序）。
4. **POLICY**——护栏引擎在动钱**之前**运行：

| 规则 | 效果 |
|---|---|
| `BLOCKED_MERCHANT` | 拦截：商家在黑名单 |
| `CATEGORY_NOT_ALLOWED` | 拦截：类目不在白名单 |
| `MAX_PER_ORDER` | 拦截：超过单笔上限 |
| `DAILY_LIMIT` | 拦截：超过每日累计上限 |
| `APPROVAL_THRESHOLD` | 挂起：必须人工批准才能付款 |

5. **APPROVAL**——被挂起的订单在人工 `--approve`（或 Web 界面勾选）之前不会付款。
6. **EXPLAIN**——大脑用大白话写理由（"我选 X 因为……"），引用评分、价格契合度和命中信号。
7. **EXECUTE / 8. CAPTURE**——PayPal v2 Checkout：OAuth2 客户端凭证换 token → 创建订单（`intent=CAPTURE`）→ 捕获付款。同一个客户端类既能打 `api-m.sandbox.paypal.com`（真实沙盒），也能进程内 mock（离线）——测过的代码就是上线的代码。
9. **收据**——CLI 输出纯文本收据；Web 输出带完整决策链的 HTML 报告。

## 四个演示场景

`python -m paypilot demo`：

| # | 需求 | 演示点 |
|---|---|---|
| 1 | "给我妈买 50 美元以内的生日礼物，她喜欢园艺" | 顺利路径：选中园艺工具套装（$34.99），策略放行，付款 |
| 2 | "找个最便宜的机械键盘" | "最便宜"意图 → 按价格升序，付款 |
| 3 | "买块 500 美元的奢侈品手表" | 被拦截：`CATEGORY_NOT_ALLOWED`，不动钱 |
| 4 | "150 美元以内的无线耳机" | 触发 `APPROVAL_THRESHOLD`（$129 ≥ $100）挂起 → 人工批准后才付款 |

## 接真实 PayPal 沙盒

默认 mock 模式，任何人零配置离线跑通。接真实沙盒：

1. 在 <https://developer.paypal.com/> 免费注册开发者账号，建一个 sandbox REST 应用。
2. 导出凭证：

```bash
export PAYPAL_CLIENT_ID="你的-sandbox-client-id"
export PAYPAL_SECRET="你的-sandbox-secret"
```

3. 加 `--live` 运行：

```bash
python -m paypilot shop "150 美元以内的无线耳机" --live --approve
python -m paypilot serve --port 8780 --live
```

真实流程说明：PayPal 托管结账需要买家在浏览器里批准订单，之后才能 capture。代理会打印它创建订单的 `approve_url`；沙盒买家批准后，capture 调用完成扣款。（mock 模式一步跑完整个闭环——演示和测试用的就是这个。）

## 接 LLM 大脑

任何 OpenAI 兼容的 chat 端点都行。以小米 MiMo 为例：

```bash
export PAYPILOT_LLM_BASE_URL="https://token-plan-cn.xiaomimimo.com/v1"
export PAYPILOT_LLM_API_KEY="你的密钥"
export PAYPILOT_LLM_MODEL="mimo-v2-flash"
```

LLM 负责意图解析和理由撰写。端点连不上或返回不可用 JSON 时，PayPilot 自动回退到离线大脑——模型挂了代理也不会挂。测试套件里有断言专门验证这个回退行为。

## 环境变量

| 变量 | 默认 | 含义 |
|---|---|---|
| `PAYPILOT_PAYPAL_MODE` | `mock` | `mock`（离线）或 `live`（沙盒） |
| `PAYPAL_CLIENT_ID` / `PAYPAL_SECRET` | — | 沙盒 REST 应用凭证（live 模式） |
| `PAYPILOT_LLM_BASE_URL` | 小米 MiMo 端点 | OpenAI 兼容 base URL |
| `PAYPILOT_LLM_API_KEY` | — | 设置后启用 LLM 大脑 |
| `PAYPILOT_LLM_MODEL` | `mimo-v2-flash` | 模型名 |

策略默认值在 `paypilot/policy.py` 的 `Policy` 数据类里：`max_per_order=200`、`daily_limit=500`、`approval_threshold=100`，允许类目 `garden/electronics/apparel/books`。

## 项目结构

```
paypilot/
├── paypilot/
│   ├── __init__.py
│   ├── __main__.py        # python -m paypilot
│   ├── catalog.py         # CSV 目录 + 带证据的排序
│   ├── policy.py          # 消费护栏（拦截/挂起/放行）
│   ├── reasoning.py       # LLM 大脑 + 离线大脑（同一接口）
│   ├── paypal.py          # PayPal v2 REST 客户端（token/下单/捕获）
│   ├── mock_paypal.py     # 进程内 PayPal mock（测试 + 演示模式）
│   ├── agent.py           # 代理主循环
│   ├── receipts.py        # 文本 + HTML 收据（含决策链）
│   ├── cli.py             # shop / demo / serve 命令
│   └── web.py             # 标准库 Web 演示界面
├── examples/
│   ├── catalog.csv        # 5 个类目 17 件商品
│   └── requests.txt       # 自然语言需求示例
├── eval.py                # 53 条断言，离线运行
├── requirements.txt       # （空——只用标准库）
├── .env.example
├── LICENSE                # MIT
└── README.md / README.zh.md
```

## 为什么能花钱的代理必须有护栏

LLM 会很乐意替你合理化一笔你没授权的购买。PayPilot 的设计假设正好相反：**模型提案，策略裁决。** 关键词过滤把不相关商品挡在模型之前；策略引擎在选中之后用硬规则裁决；达到审批阈值的一切等待人类。模型只负责写解释——从不负责开权限。

## 参赛说明

- 目标奖项：*Best Use of PayPal + AI*、*Best Use of Agentic Commerce*、*Most Creative*、*Best Demo Delivery*。
- 提交要求覆盖：公开仓库 + 许可证（MIT）——有；评委可运行的可用原型——有，离线 mock 模式零配置；演示视频——另行录制；文档——本文件。

## 许可证

MIT——见 [LICENSE](LICENSE)。

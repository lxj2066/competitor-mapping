---
name: competitor-mapping-skill
display_name: "竞对映射引擎"
display_name_en: "Competitor Mapping Engine"
description: "把目标公司或技术方向做成可审计的竞对映射档案：五路召回、确定性质量门、校准融合打分、强制反证清单与增量监控。触发词：竞对分析、找对标、对标公司、竞争对手映射、competitor mapping、竞争格局、技术路线对标、谁在做这个。不触发：只查工商登记信息、写行业综述、估值建模、招聘岗位画像。"
description_zh: "给一个目标公司或技术方向，产出带证据链与反证清单的竞对映射档案；口径先冻结、质量门用规则卡、权重用标注校准、漂移触发增量重评。"
description_en: "Turn a target company or technology direction into an auditable competitor mapping dossier: five-channel recall, deterministic quality gate, calibrated fusion, mandatory refutation list, and drift-triggered incremental re-mapping."
version: 1.0.0
author: "李献杰 Lyon"
category: research
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# 竞对映射引擎

输入一家公司或一个技术方向，输出一份**每条判断都能回溯到证据行**的竞对映射档案。不产出「感觉像对标」的名单。

三条铁律，不解释：

1. **口径先冻结，再召回。** 没定义"什么算竞争对手"就召回，等于无法评估召回质量。
2. **规则能判的，不交模型判。** 活跃状态、来源独立性、时效、主体同一性、规模区间，全部走脚本，模型只看输出。
3. **必须找反证，找不到要写在明处。** 「未找到反证」不等于「无反证」；两者在档案里必须分开表述。

> 本包是分析流程与判定工具，**不构成投资建议**。所有数字须现场取数并标注取数日期与出处；取不到就写「检索失败」，不许用记忆填数。

## 触发条件

**触发**（任一命中即用）：

| 用户说法 | 说明 |
|---|---|
| 帮我找一下 X 的竞争对手 / 谁在做这个 | 主场景，目标公司或技术方向明确 |
| 做一个对标映射 / 竞争格局 / 赛道 players | 需要分层候选，不只要一个名字 |
| 我们和 A、B 比差在哪 | 已有候选，需要结构化对比与反证 |
| 这个技术路线还有谁在押 | 技术信号驱动的召回 |
| 上一版映射要更新，X 最近有变化 | 增量重评入口 |

**不触发**（负向边界，命中即改走别的做法）：

| 说法 | 应当 | 原因 |
|---|---|---|
| 查一下这家公司的工商信息 / 股权结构 | 企业信息查询工具 | 这是主体核验，不是竞争关系判定 |
| 写一份行业综述 / 市场规模测算 | 研究报告写作 | 缺目标主体，无法做候选级判定 |
| 帮我给这家公司估值 / 建模 | 估值建模流程 | 本包不给估值口径，只给竞争定位 |
| 帮我写个竞品分析 JD / 招人 | 招聘文案 | 对象是人岗，不是公司 |

一条消息里既有映射需求又有估值需求时，**只做映射部分**，估值口径明确交回用户。

## 0 口径闸门（不做完不许出任何分数）

先冻结四件事，写进 `mapping-scope.json`，全流程只读不改：

1. **目标定义**：目标公司（主体唯一键）或目标技术方向（一句话描述 + 3 至 8 个技术术语）。
2. **竞争口径**：候选必须归入四类之一，见 `references/mapping-methodology.md`：
   - `direct` 直接：同一客户群、同一需求、同类解法。
   - `indirect` 间接：同一需求、不同解法。
   - `potential` 潜在：具备进入能力（上下游一体、相邻技术迁移）。
   - `comparable` 可比：财务或模式可比，用于定位，**不计入竞争强度**。
3. **时间窗**：证据新鲜度基准日（默认取当天），以及各通道 TTL。
4. **地域与阶段**：市场范围、公司阶段区间（否则巨头的边缘业务会被误当对标）。

口径不冻结就召回，后面所有分数都不可比。用户没说清时，按默认口径出「前置假设」一行写在档案首屏，不做开放式追问超过一次。

## 工作流（S0 至 S7，逐步交付，不许合并）

| 步 | 动作 | 产物 | 判据（不过就停） |
|---|---|---|---|
| S0 | 冻结口径、定 TTL、定分层阈值 | `mapping-scope.json` | 四件事齐全，四类口径都有定义 |
| S1 | 解析目标：技术、产品、产业链位置、市场四个维度各给关键词与锚点 | `target-profile.json` | 每维至少 1 条可验证锚点，不许只有形容词 |
| S2 | 五路召回，每路独立产出候选与证据 | `candidates.json` | 每个候选至少 1 条证据；同一候选跨路保留全部证据 |
| S3 | 确定性质量门（脚本 G1 至 G7） | `gated.json` | 只有 `pass` 进打分；`hold` 必须写复核理由 |
| S4 | 校准融合打分（脚本，含区间与通道贡献） | `fused.json` | 每分带置信区间与贡献分解 |
| S5 | 强制反证（脚本 + 人工补证） | `critique.json` | Top 候选全覆盖；缺字段标 `unverified` |
| S6 | 分层档案输出（`scripts/dossier.py`） | `dossier.md` / `dossier.html` | T1 必含五大量化与三条结构性判断 |
| S7 | 监控注册与漂移规则（`scripts/drift.py`） | `monitor.json` | 每个对标至少 2 条触发规则 |

### S2 五路召回（并行，互不覆盖）

| 通道 | 召回什么 | 最少证据 | 常见失效 |
|---|---|---|---|
| `value_chain` 产业链 | 一阶上下游与合作披露 | 招股书、年报、供应商名录、公告片段 | 只覆盖上市大客户，长尾全漏 |
| `patent_graph` 专利异构图 | 技术邻近公司 | 专利号、IPC 主分类、申请人 | IPC 粗粒度，同码不同技术 |
| `patent_semantic` 专利语义分布 | 技术主题重叠公司 | 专利摘要、主题簇分布 | 摘要措辞差异导致漏召 |
| `product_market` 产品市场 | 功能重叠、定价、渠道 | 产品页、定价页、应用商店、流量口径 | 流量相近但客群不同 |
| `graph_diffusion` 图扩散 | 已知对标的邻居 | 种子清单、边类型与权重 | 种子选偏会整片带偏 |

每路都写 `channel` 与证据等级，等级定义见 `references/mapping-methodology.md`。**同一原始出处只能算一条独立来源**，转载、聚合、同源重复不计。

### S3 至 S5 用脚本，不靠人眼

```
python scripts/gate.py     --candidates candidates.json --config mapping-scope.json --out gated.json
python scripts/fuse.py     --candidates gated.json --weights weights.json --out fused.json
python scripts/critique.py --target target-profile.json --candidates fused.json --out critique.json
```

权重不许拍。冷启动用 `fuse.py --calibrate labels.json`，由标注对拟合通道判别力，未达最小标注量时自动退回先验权重并在档案标注 `prior_weights`。规则明细见 `references/gate-rules.md`。

### S5 反证的硬要求

每个进入 T1 的候选，必须至少试过五类反证：技术路线互斥、客群不重叠、商业模式不同、地域隔离、规模阶段错配。每条写三样：反证命题、所需证据、当前状态（`open` / `refuted` / `unverified`）。缺字段不许写「无反证」，只能写 `unverified` 并列出缺哪几个字段。

模板见 `references/critique-playbook.md`。

### S6 分层条件（不满足不许升级）

| 层 | 数量上限 | 进入条件 | 内容 |
|---|---|---|---|
| T1 深度 | 3 至 5 | 质量门 pass 且综合分达 T1 阈值且无高危未决反证 | 五大量化 + 三条结构性判断 + 反证清单 |
| T2 定位卡 | 10 至 20 | 质量门 pass，分数达 T2 阈值 | 定位一句话、重叠维度、单条关键证据 |
| T3 观察 | 不限 | pass 但规模或阶段错配，或 `hold` 后复核通过 | 触发监控，不进结论 |

模板与字段清单见 `references/dossier-template.md`。

### S7 增量重评，不做定时全量

只按变更信号触发：专利主题漂移、招聘技术栈漂移、定价或产品页结构变更、融资并购事件、专利增速异常。每条触发规则写明阈值、重评范围（局部或全量）、重评后失效的旧结论。规则见 `references/monitoring-rules.md`。

## 输出契约

一次完整交付给五件，缺一件视为未完成：

1. `mapping-scope.json` 口径快照（含前置假设与基准日）。
2. `fused.json` 候选表：`id`、`name`、`classification`、`score`、`ci`、`contributions`、`gate.status`。
3. `critique.json` 反证清单：每条含 `code`、`claim`、`severity`、`state`、`missing_fields`。
4. `dossier.md` 与自包含 `dossier.html`，按 T1 / T2 / T3 分层。
5. `monitor.json` 监控注册表与漂移触发规则。

档案首屏固定三段：**结论（T1 名单与一句定位）· 证据强度（通道覆盖数与独立来源数）· 未决项（unverified 字段与待补证据）**。没有第三段就是没做完。

评估口径（P@K、R@K、NDCG@K、留一法回测）见 `references/eval-protocol.md`，脚本为 `scripts/eval.py`；数据源可得性与替代口径见 `references/source-adapters.md`。

## 红线

- 不许凭记忆给公司名、营收、专利数、客户名。全部来自本次取数并标日期与出处。
- 不许把「检索失败」写成「不存在」，也不许把「没查到反证」写成「已排除风险」。
- 不许为了凑够 T1 数量放宽质量门或阈值；宁可少给，不许降标准。
- 不许只给分数不给贡献分解；分数不可解释等于没算。
- 不许把 `comparable` 混进竞争强度；可比公司只用于定位。
- 不许在监控规则里放高频全量重跑，成本失控且掩盖信号。
- 涉及他人个人信息、未公开商业数据时改占位符，不落原文。

## 降级

| 情况 | 处置 |
|---|---|
| 脚本跑不起来或 Python 不可用 | 手算不可代替：写「本轮未实测」，仍出档案但标注分数未经脚本核算 |
| 某路数据源无权限或无凭证 | 该通道标 `unavailable` 并写明缺哪一项，不许用别通道分数顶替 |
| 用户只给公司名，无技术描述 | 用官网与年报做 S1，技术维度标 `thin`，召回降到三路并在档案声明覆盖不足 |
| 主体身份不确定（同名、子公司） | 停在 S3，输出主体核验清单，不进入打分 |
| 标注量不足以校准 | 退回先验权重，档案标注 `prior_weights` 与所需最小标注数 |
| 只要求一个名单 | 只给 T2 定位卡，明确声明未做深度档案与反证 |
| 输入与本次域完全不重叠 | 拒绝套用旧结论，重新走 S0 |

## 自检（退出门禁，全部为 Yes 才交付）

- [ ] 口径四件事写进 `mapping-scope.json` 了吗，候选是否每个都有分类？
- [ ] 每个候选的独立来源数是否 ≥2，来源是否真的是不同原始出处？
- [ ] 分数是否有区间与通道贡献，冲突标记是否落实？
- [ ] T1 候选是否五类反证都试过，`unverified` 是否写清了缺哪几个字段？
- [ ] 所有数字是否带取数日期与出处，检索失败是否如实标注？
- [ ] 监控注册表是否每个对标都有至少 2 条可执行触发规则？

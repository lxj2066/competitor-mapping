# 质量门规则（G1 至 G7）

> 这一层**不用模型判断**，全部规则化。目的只有一个：把「只有一路信号、来源还是转载」的候选挡在打分之前。
> 实现脚本：`scripts/gate.py`。改阈值改 `mapping-scope.json`，不改代码。

## 0 输入契约

批次文件（候选数组），每条至少含：

```json
{
  "id": "c001",
  "name": "示例公司",
  "attributes": {
    "status": "active",
    "entity_key": "CN-91330100XXXXXXXX",
    "classification": "direct",
    "classification_basis": "年报披露同款产品供货同一客户",
    "revenue_cny": 520000000,
    "last_verified": "2026-09-30"
  },
  "signals": {
    "value_chain": {"score": 0.82, "evidence": [
      {"source_type": "E1", "independent_key": "cninfo-20250415-113",
       "claim": "年报披露向同一整机厂供货", "ref": "公告编号 20250415-113", "date": "2025-04-15"}
    ]}
  }
}
```

字段缺失不等于不合格，但**决定该条命中哪一道门**。缺字段一律往 `hold` 走，不许默认通过。

## 1 七道门

| 码 | 门 | 判据 | 不过怎么处理 | 命中率预期 |
|---|---|---|---|---|
| `G1` | 经营状态 | `attributes.status` 为 `active` | 注销、吊销、停业直接 `drop`；缺失或 `unknown` 走 `hold` | 中 |
| `G2` | 来源独立性 | 证据中 `source_type` 为 `E1`/`E2` 的**不同 `independent_key`** 数量 ≥ 2 | 0 条 `drop`；1 条 `hold` | 高，主要拦截项 |
| `G3` | 证据时效 | 有分数的每条通道，其最新证据日期距基准日不超过该通道 TTL | 全部通道超期 `hold`；部分超期按通道剔除后重算 | 中 |
| `G4` | 主体同一性 | `entity_key` 存在，且本批次内不重复 | 重复时保留证据多的一条，其余 `hold` 并标 `dup_of`；缺失 `hold` | 中 |
| `G5` | 口径对齐 | `classification` 属于允许集合，且有 `classification_basis` | 无分类或 `unclassified` 直接 `drop`；有分类无依据 `hold` | 高 |
| `G6` | 规模合理性 | 营收比落在 `scale_ratio_band`（默认 0.02 至 50 倍）内 | 超出只降层：`tier_cap` 收紧为 `T3`；营收缺失标 `scale_unknown`，`tier_cap` 为 `T2` | 低但致命 |
| `G7` | 核验时效 | `last_verified` 距基准日不超过 `verify_max_age_days`（默认 120 天） | 超期或缺失 `hold`，需重新核验主体 | 中 |

状态语义：

- `pass`：可进打分，受 `tier_cap` 约束。
- `hold`：不进打分，进复核队列，必须人手补证据或确认后放行。
- `drop`：本批次出局，写入剔除理由，便于复查。

## 2 默认阈值

| 参数 | 默认 | 什么时候该改 |
|---|---|---|
| `min_independent_sources` | 2 | 冷门细分赛道来源稀缺可降为 1，但档案必须声明降级 |
| `ttl_days.value_chain` | 540 | 供应链关系变化慢，可放长 |
| `ttl_days.patent_graph` | 1825 | 专利有效期长，事件性为主 |
| `ttl_days.patent_semantic` | 1825 | 同上 |
| `ttl_days.product_market` | 240 | 产品与定价变化快，须短 |
| `ttl_days.graph_diffusion` | 540 | 图上的边随时间失效 |
| `scale_ratio_band` | 0.02 至 50 | 平台型或生态型公司应放宽上界 |
| `verify_max_age_days` | 120 | 高变动行业可压到 60 |

## 3 三个易错点

1. **独立来源数不等于证据条数。** 同一公告的三种转载是 1 条。脚本按 `independent_key` 去重，`independent_key` 由人工填成原始出处标识。
2. **`E3` 与 `E4` 不计入 G2。** 媒体与推断可以补充置信，不能独立成立。允许它们参与融合，不允许它们过门。
3. **`hold` 不是淘汰。** 复核后放行的候选必须写复核人、复核日期与新增证据，否则下一轮会再次被拦，且无法解释。

## 4 与分层的接口

质量门只输出 `status` 与 `tier_cap`，不输出分数。分层由综合分与反证状态决定，见 `dossier-template.md`。两处阈值互不覆盖：

- 质量门控的是**能不能算**。
- 分层控的是**算完能说到多深**。

混在一处会出现"分数高但证据不合格"的档案，这是最常见的翻车点。

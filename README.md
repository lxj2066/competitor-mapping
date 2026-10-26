# 竞对映射引擎 · Competitor Mapping

> 把「谁是我的竞争对手」从一句感觉，做成一份**每条判断都能回溯到证据行**的档案。

本仓库同域双包：

| 包 | 形态 | 装到哪 | 说明 |
|---|---|---|---|
| [`competitor-mapping-skill`](./competitor-mapping-skill) | 技能包 | SkillHub | 完整方法论、六个确定性脚本、十个回归用例 |
| [`competitor-mapping-agent`](./competitor-mapping-agent) | 专家包 | WorkBuddy 专家入口 | 人设与退出自检，内嵌同款技能，可直接对话 |

两包领域段相同、版本各自独立。只想要流程与脚本就装技能包；想直接对话就装专家包。

## 为什么不是又一个「四路召回 + 加权平均」

线性流水线的通病是**没有失败处理**：召回到什么就输出什么，错了也没人拦。生产级的对标映射必须回答三个问题，本包对每个问题都有可执行的判据：

| 问题 | 对应机制 | 判据落在哪 |
|---|---|---|
| 怎么知道找得对 | 确定性质量门 + 强制反证 | `references/gate-rules.md`、`references/critique-playbook.md` |
| 分数凭什么这么高 | 对数几率池化 + 通道贡献分解 + 置信区间 | `references/mapping-methodology.md` |
| 什么时候该重新找 | 五类漂移信号，只按信号触发增量重评 | `references/monitoring-rules.md` |

## 六个确定性脚本（纯标准库，同输入同输出）

| 脚本 | 作用 | 关键设计 |
|---|---|---|
| `scripts/gate.py` | 质量门 G1 至 G7 | 独立来源按 `independent_key` 去重，转载不算两条 |
| `scripts/fuse.py` | 校准融合打分 | 对数几率池化 + 一致性加成 + 冲突惩罚 + bootstrap 区间；权重可标注拟合 |
| `scripts/critique.py` | 五类反证清单 | 三态结论，缺字段记 `unverified`，绝不写「无反证」 |
| `scripts/eval.py` | P@K / R@K / NDCG@K 与留一法回测 | 留一法测「没有先验时能否自己找到」 |
| `scripts/drift.py` | 漂移检测 D1 至 D5 | 仅并购事件触发全量重审，其余局部重算 |
| `scripts/dossier.py` | 分层档案渲染 | T1 深度 / T2 定位卡 / T3 观察，缺字段渲染为 unverified |

## 快速开始

```bash
# 1 冻结口径，准备候选（结构见 references/gate-rules.md 第 0 节）
# 2 跑质量门
python competitor-mapping-skill/scripts/gate.py \
  --candidates candidates.json --config mapping-scope.json --out gated.json
# 3 打分
python competitor-mapping-skill/scripts/fuse.py --candidates gated.json --out fused.json
# 4 反证
python competitor-mapping-skill/scripts/critique.py \
  --target target-profile.json --candidates fused.json --out critique.json
# 5 出档案
python competitor-mapping-skill/scripts/dossier.py \
  --fused fused.json --critique critique.json --out dossier.md --html dossier.html
```

每个脚本都有可运行的样例输入，位于 `competitor-mapping-skill/tests/`，可直接换成自己的数据。

## 验证结果（本仓库当前状态，可复跑）

| 项目 | 结果 |
|---|---|
| 技能包体检 `forge.py check --strict` | A / 100，BLOCKER 0 |
| 专家包体检 `forge.py check --strict` | A / 100，BLOCKER 0，内嵌技能 A/100 |
| 结构回归 `forge.py test` | 5 / 5 通过（含反例与空输入） |
| 脚本回归 `python tests/run_tests.py` | 10 / 10 通过（含确定性与失败路径） |
| 打包五层探针 `forge.py pack` | 技能包 PASS 15 项，专家包 PASS 17 项 |

复跑：

```bash
cd competitor-mapping-skill && python tests/run_tests.py -v
```

## 边界（请先读）

- **不构成投资建议**。本包给竞争定位，不给估值口径，不给买卖结论。
- 所有数字必须现场取数并标注日期与出处；检索失败写「检索失败」，不许推断填补。
- 数据源凭证不进包，一律用 `${VAR}` 占位由运行环境注入。
- 「未找到反证」与「无反证」是两件事，档案里必须分开写。

## 目录

```
competitor-mapping/
├─ README.md
├─ LICENSE
├─ CHANGELOG.md
├─ competitor-mapping-skill/
│  ├─ SKILL.md                     八步工作流与退出门禁
│  ├─ references/                  七个知识层文件（口径、门控、反证、评估、监控、数据源、档案模板）
│  ├─ scripts/                     六个确定性脚本
│  └─ tests/                       用例、样例数据与回归脚本
└─ competitor-mapping-agent/
   ├─ .codebuddy-plugin/plugin.json
   ├─ agents/competitor-mapping.md
   ├─ avatars/competitor-mapping.png
   ├─ skills/competitor-mapping-skill/   内嵌技能
   ├─ README.md
   └─ CHANGELOG.md
```

## 作者

李献杰 Lyon ｜ 浙江杭州
付费咨询 ｜ 猎头寻访 ｜ 招聘服务 ｜ AI 自动化改进优化

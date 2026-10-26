#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dossier.py — 分层档案渲染（T1 深度 / T2 定位卡 / T3 观察）

用法:
    python dossier.py --fused fused.json --out dossier.md [--html dossier.html]
                      [--critique critique.json] [--scope mapping-scope.json]

分层条件见 references/dossier-template.md：
    T1 综合分 >= 75 且 tier_cap 非 T3 且区间宽度 <= 30 且无高危未决反证
    T2 综合分 >= 55
    T3 其余通过质量门者；未通过质量门的不进档案，只在剔除统计里出现

缺字段一律渲染为 unverified 并写明缺什么，绝不补数。

退出码:
    0 正常；2 输入或用法错误
"""

import argparse
import html
import json
import os
import sys

CHANNELS = ("value_chain", "patent_graph", "patent_semantic",
            "product_market", "graph_diffusion")

CATEGORY_ZH = {"direct": "直接", "indirect": "间接",
               "potential": "潜在", "comparable": "可比"}


def die(msg):
    sys.stderr.write("dossier.py: %s\n" % msg)
    raise SystemExit(2)


def load_json(path):
    if not os.path.isfile(path):
        die("文件不存在: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as exc:
        die("%s 不是合法 JSON: %s" % (path, exc))


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def independent_sources(cand):
    keys = set()
    for ch, node in (cand.get("signals") or {}).items():
        if ch not in CHANNELS or not isinstance(node, dict):
            continue
        for ev in (node.get("evidence") or []):
            if not isinstance(ev, dict):
                continue
            if str(ev.get("source_type", "")).upper() not in ("E1", "E2"):
                continue
            key = str(ev.get("independent_key", "")).strip()
            if key:
                keys.add(key)
    return len(keys)


def median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def tier_of(cand, critique_map):
    """tier_cap 是上限：cap 为 T3 时无论分数多高都只能进 T3。"""
    fused = cand.get("fused") or {}
    gate = cand.get("gate") or {}
    score = num(fused.get("score"))
    if gate.get("status") != "pass" or fused.get("status") != "ok" or score is None:
        return "X"
    cap = str(gate.get("tier_cap", "T1"))
    width = num(fused.get("ci_width"))
    risk = (critique_map.get(str(cand.get("id"))) or {}).get("risk", "unknown")
    if score >= 75 and (width is None or width <= 30.0) and risk != "high":
        desired = "T1"
    elif score >= 55:
        desired = "T2"
    else:
        desired = "T3"
    rank = {"T1": 1, "T2": 2, "T3": 3}
    tier = max(desired, cap, key=lambda t: rank.get(t, 3))
    return tier if tier in rank else "T3"


def five_metrics(cand):
    attrs = cand.get("attributes") or {}
    keys = [("有效专利数", "patent_valid"), ("近三年新增专利", "patent_recent_3y"),
            ("被引中位数", "patent_cited_median"), ("产业链上下游边数", "chain_edges"),
            ("功能矩阵覆盖度", "feature_coverage")]
    rows = []
    for label, key in keys:
        val = attrs.get(key)
        date = attrs.get(key + "_date", "")
        src = attrs.get(key + "_ref", "")
        if val is None or val == "":
            rows.append((label, "unverified", "缺采样", "补该项取数与出处"))
        else:
            rows.append((label, str(val), date or "未标日期", src or "未标出处"))
    return rows


def three_judgements(cand):
    attrs = cand.get("attributes") or {}
    spec = (("能力来源", "capability_source"),
            ("位置成因", "position_cause"),
            ("脆弱点", "fragility"))
    rows = []
    for label, key in spec:
        item = attrs.get(key)
        if isinstance(item, dict) and item.get("claim"):
            rows.append((label, str(item.get("claim")), str(item.get("ref", "缺证据编号"))))
        else:
            rows.append((label, "假设未定", "补证据后转判断"))
    return rows


def build(payload, critique, scope):
    cands = payload.get("candidates") or []
    critique_map = {}
    if isinstance(critique, dict):
        for item in critique.get("candidates") or []:
            critique_map[str(item.get("id"))] = item.get("critique") or {}

    buckets = {"T1": [], "T2": [], "T3": [], "X": []}
    for cand in cands:
        buckets[tier_of(cand, critique_map)].append(cand)
    buckets["T1"].sort(key=lambda c: -num((c.get("fused") or {}).get("score") or 0.0))
    buckets["T2"].sort(key=lambda c: -num((c.get("fused") or {}).get("score") or 0.0))
    buckets["T3"].sort(key=lambda c: -num((c.get("fused") or {}).get("score") or 0.0))

    scored = buckets["T1"] + buckets["T2"]
    cover = [len((c.get("fused") or {}).get("channels_used") or []) for c in scored]
    srcs = [independent_sources(c) for c in scored]
    widths = [num((c.get("fused") or {}).get("ci_width")) for c in scored]
    widths = [w for w in widths if w is not None]

    pending = []
    for c in buckets["T1"]:
        crit = critique_map.get(str(c.get("id"))) or {}
        for field in crit.get("unknown_fields") or []:
            pending.append("%s 缺 %s（反证未验证）" % (c.get("name", c.get("id")), field))
        for row in five_metrics(c):
            if row[1] == "unverified":
                pending.append("%s 缺 %s（T1 五大量化未齐）" % (c.get("name", c.get("id")), row[0]))
    for c in buckets["T2"]:
        if num((c.get("fused") or {}).get("ci_width")) is not None and \
                num((c.get("fused") or {}).get("ci_width")) > 30:
            pending.append("%s 置信区间过宽，未进 T1" % c.get("name", c.get("id")))

    return {
        "basis_date": payload.get("basis_date", scope.get("basis_date", "") if scope else ""),
        "target": (scope or {}).get("target_name", "未命名目标"),
        "assumptions": (scope or {}).get("assumptions") or [],
        "buckets": buckets,
        "critique_map": critique_map,
        "evidence": {
            "scored": len(scored),
            "channel_coverage_median": median(cover),
            "independent_sources_median": median(srcs),
            "ci_width_median": median(widths),
        },
        "pending": pending,
        "weights": payload.get("weights") or {},
        "weight_source": payload.get("weight_source", "unknown"),
    }


def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(x) for x in row) + " |")
    return out


def render_md(ctx):
    b = ctx["buckets"]
    lines = ["# 竞对映射档案：%s" % ctx["target"], "",
             "基准日：%s ｜ 权重来源：%s" % (ctx["basis_date"] or "未标", ctx["weight_source"]), ""]
    if ctx["assumptions"]:
        lines.append("前置假设：" + "；".join(str(a) for a in ctx["assumptions"]))
        lines.append("")

    t1_names = [c.get("name", c.get("id")) for c in b["T1"]]
    lines.append("## 结论")
    if t1_names:
        lines.append("T1 共 %d 家：%s。定位与差异见下方深度档案。" % (
            len(t1_names), "、".join(t1_names)))
    else:
        lines.append("本轮无候选达到 T1 条件。原因见未决项，不许用 T2 顶替结论。")
    lines.append("")
    ev = ctx["evidence"]
    lines.append("## 证据强度")
    lines.append("参与打分 %s 家 ｜ 通道覆盖中位数 %s / 5 ｜ 独立来源中位数 %s ｜ 区间宽度中位数 %s" % (
        ev["scored"],
        "无" if ev["channel_coverage_median"] is None else int(ev["channel_coverage_median"]),
        "无" if ev["independent_sources_median"] is None else int(ev["independent_sources_median"]),
        "无" if ev["ci_width_median"] is None else ev["ci_width_median"]))
    lines.append("")
    lines.append("## 未决项")
    if ctx["pending"]:
        for item in ctx["pending"][:20]:
            lines.append("- " + item)
        if len(ctx["pending"]) > 20:
            lines.append("- 另有 %d 条同类缺项，见 fused.json 与 critique.json" % (len(ctx["pending"]) - 20))
    else:
        lines.append("- 无。本轮所有 T1 字段齐备，反证状态已记录。")
    lines.append("")

    if b["T1"]:
        lines.append("## T1 深度档案")
        for c in b["T1"]:
            fused = c.get("fused") or {}
            attrs = c.get("attributes") or {}
            cls = CATEGORY_ZH.get(attrs.get("classification", ""), "未分类")
            lines.append("")
            lines.append("### %s（%s）综合分 %s ｜ 区间 %s" % (
                c.get("name", c.get("id")), cls, fused.get("score"), fused.get("ci")))
            lines.append("")
            lines.append("通道贡献：")
            rows = [("%s" % ch,
                     "%s" % v.get("score"),
                     "%s" % v.get("weight"),
                     "%s%%" % v.get("share_pct"),
                     "冲突" if ch in (fused.get("flags") or []) else "正常")
                    for ch, v in sorted((fused.get("contributions") or {}).items(),
                                        key=lambda kv: -abs(kv[1].get("share_pct") or 0))]
            lines.extend(md_table(["通道", "分数", "权重", "贡献占比", "状态"], rows))
            lines.append("")
            lines.append("五大量化：")
            lines.extend(md_table(["指标", "数值", "取数日期", "出处"], five_metrics(c)))
            lines.append("")
            lines.append("三条结构性判断：")
            lines.extend(md_table(["类型", "判断", "证据编号"], three_judgements(c)))
            crit = ctx["critique_map"].get(str(c.get("id"))) or {}
            lines.append("")
            lines.append("反证：风险 %s ｜ %s" % (crit.get("risk", "unknown"),
                                            crit.get("verdict", "未做反证")))
            for con in crit.get("contradictions") or []:
                lines.append("- %s %s（%s，%s）" % (con.get("code"), con.get("claim"),
                                                   con.get("severity"), con.get("state")))
            if crit.get("unknown_fields"):
                lines.append("- 未验证字段：%s" % "、".join(crit["unknown_fields"]))
            lines.append("")

    if b["T2"]:
        lines.append("## T2 定位卡")
        rows = []
        for c in b["T2"]:
            fused = c.get("fused") or {}
            crit = ctx["critique_map"].get(str(c.get("id"))) or {}
            attrs = c.get("attributes") or {}
            rows.append((c.get("name", c.get("id")),
                         CATEGORY_ZH.get(attrs.get("classification", ""), "未分类"),
                         fused.get("score"), fused.get("ci"),
                         crit.get("risk", "unknown"),
                         (fused.get("flags") and "/".join(fused["flags"])) or "无"))
        lines.append("")
        lines.extend(md_table(["公司", "分类", "分数", "区间", "反证风险", "标记"], rows))
        lines.append("")

    if b["T3"]:
        lines.append("## T3 观察名单")
        lines.append("")
        rows = []
        for c in b["T3"]:
            attrs = c.get("attributes") or {}
            fused = c.get("fused") or {}
            why = "规模或阶段错配" if "scale_mismatch" in (c.get("gate") or {}).get("flags", []) \
                else "分数不足"
            rows.append((c.get("name", c.get("id")),
                         CATEGORY_ZH.get(attrs.get("classification", ""), "未分类"),
                         fused.get("score"), why))
        lines.extend(md_table(["公司", "分类", "分数", "观察原因"], rows))
        lines.append("")

    if b["X"]:
        lines.append("## 剔除与复核")
        lines.append("")
        rows = []
        for c in b["X"]:
            gate = c.get("gate") or {}
            rows.append((c.get("name", c.get("id")), gate.get("status"),
                         ",".join(gate.get("codes") or []) or "无",
                         "；".join(gate.get("reasons") or [])[:60]))
        lines.extend(md_table(["公司", "状态", "命中门", "原因"], rows))
        lines.append("")

    lines.append("---")
    lines.append("本档案为分析流程产物，不构成投资建议。所有数字须以取数日期与出处为准，检索失败不等于不存在。")
    return "\n".join(lines) + "\n"


def render_html(md_text, title):
    css = """
:root{--bg:#0b1220;--fg:#e8eef7;--dim:#93a2b8;--line:#22304a;--accent:#4da3ff;--warn:#ffb454}
*{box-sizing:border-box}
body{margin:0;padding:48px 24px;background:var(--bg);color:var(--fg);
font:15px/1.75 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
main{max-width:960px;margin:0 auto}
h1{font-size:28px;margin:0 0 8px;letter-spacing:.5px}
h2{margin:40px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line);font-size:20px}
h3{margin:28px 0 8px;font-size:16px;color:var(--accent)}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:13px}
th,td{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
th{background:#131c2e;color:var(--dim);font-weight:600}
tr:nth-child(even) td{background:#0e1726}
code{background:#131c2e;padding:1px 5px;border-radius:3px;font-size:12px}
hr{border:0;border-top:1px solid var(--line);margin:32px 0}
li{margin:4px 0}
footer{color:var(--dim);font-size:12px;margin-top:32px}
"""
    body = []
    in_table, in_list = False, False
    for raw in md_text.splitlines():
        line = raw.rstrip()
        if not line:
            if in_table:
                body.append("</table>")
                in_table = False
            if in_list:
                body.append("</ul>")
                in_list = False
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            if not in_table:
                body.append("<table>")
                in_table = True
                body.append("<tr>" + "".join("<th>%s</th>" % html.escape(c) for c in cells) + "</tr>")
            else:
                body.append("<tr>" + "".join("<td>%s</td>" % html.escape(c) for c in cells) + "</tr>")
            continue
        if in_table:
            body.append("</table>")
            in_table = False
        if line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append("<li>%s</li>" % html.escape(line[2:]))
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        if line.startswith("### "):
            body.append("<h3>%s</h3>" % html.escape(line[4:]))
        elif line.startswith("## "):
            body.append("<h2>%s</h2>" % html.escape(line[3:]))
        elif line.startswith("# "):
            body.append("<h1>%s</h1>" % html.escape(line[2:]))
        elif line.startswith("---"):
            body.append("<hr>")
        else:
            body.append("<p>%s</p>" % html.escape(line))
    if in_table:
        body.append("</table>")
    if in_list:
        body.append("</ul>")
    return ("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>%s</title><style>%s</style></head><body><main>%s"
            "<footer>competitor-mapping-skill 生成，不构成投资建议。</footer>"
            "</main></body></html>\n" % (html.escape(title), css, "\n".join(body)))


def main(argv=None):
    ap = argparse.ArgumentParser(description="分层档案渲染")
    ap.add_argument("--fused", required=True)
    ap.add_argument("--out", required=True, help="输出 dossier.md")
    ap.add_argument("--html", help="另存自包含 HTML")
    ap.add_argument("--critique")
    ap.add_argument("--scope")
    args = ap.parse_args(argv)

    payload = load_json(args.fused)
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        die("fused 文件需含 candidates 数组")
    critique = load_json(args.critique) if args.critique else None
    scope = load_json(args.scope) if args.scope else None
    if scope is not None and not isinstance(scope, dict):
        die("scope 需为对象")

    ctx = build(payload, critique, scope)
    md_text = render_md(ctx)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(md_text)
    if args.html:
        with open(args.html, "w", encoding="utf-8") as fh:
            fh.write(render_html(md_text, "竞对映射档案：%s" % ctx["target"]))

    b = ctx["buckets"]
    sys.stdout.write("dossier: T1 %d / T2 %d / T3 %d / 剔除 %d -> %s\n" % (
        len(b["T1"]), len(b["T2"]), len(b["T3"]), len(b["X"]), args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

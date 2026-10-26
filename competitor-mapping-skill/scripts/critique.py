#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""critique.py — 结构化反证清单（五类模板，三态结论）

用法:
    python critique.py --target target-profile.json --candidates fused.json --out critique.json
    python critique.py --target target-profile.json --candidates fused.json --out critique.json --md critique.md

判定口径:
    只比对结构化字段（tech_route / customer / business_model / geo / revenue_cny）。
    字段缺失记为 unverified，绝不写成「无反证」。
    脚本判不了领域原理是否互斥，该部分由人工补证后按同一结构回写。

退出码:
    0 正常；2 输入或用法错误
"""

import argparse
import json
import os
import sys

DIM_SPEC = {
    "tech_route": ("TR", "技术路线互斥", "双方原理层描述、关键权利要求或路线图"),
    "customer": ("CU", "客群不重叠", "客户名单或行业分布、渠道结构、中标记录"),
    "business_model": ("BM", "商业模式不同", "收入结构、定价方式、交付形态"),
    "geo": ("GE", "地域市场隔离", "区域收入占比、销售实体、认证准入"),
}


def die(msg):
    sys.stderr.write("critique.py: %s\n" % msg)
    raise SystemExit(2)


def load_json(path):
    if not os.path.isfile(path):
        die("文件不存在: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as exc:
        die("%s 不是合法 JSON: %s" % (path, exc))


def as_list(value):
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in value.replace("、", ",").split(",") if v.strip()]
    return []


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def overlap(a, b):
    sa, sb = set(x.lower() for x in a), set(x.lower() for x in b)
    return sa & sb


def judge(target, cand, score, band):
    attrs = cand.get("attributes") or {}
    contradictions, unknown = [], []
    checked = 0

    for dim, (code, name, need) in DIM_SPEC.items():
        tv, cv = as_list(target.get(dim)), as_list(attrs.get(dim))
        if not tv or not cv:
            unknown.append(dim)
            continue
        checked += 1
        if overlap(tv, cv):
            continue
        severity = "high" if (score >= 70 and code in ("TR", "CU")) else "medium"
        if code == "GE":
            severity = "medium"
        if code == "BM":
            severity = "medium"
        contradictions.append({
            "code": code,
            "dim": dim,
            "claim": "%s：目标侧 %s 与候选侧 %s 无交集" % (name, "/".join(tv), "/".join(cv)),
            "severity": severity,
            "state": "open",
            "evidence_needed": need,
            "missing_fields": [],
        })

    # ---- SC 规模与阶段错配
    trev, crev = num(target.get("revenue_cny")), num(attrs.get("revenue_cny"))
    if trev and crev and trev > 0:
        checked += 1
        ratio = crev / trev
        if ratio < float(band[0]) or ratio > float(band[1]):
            contradictions.append({
                "code": "SC",
                "dim": "scale",
                "claim": "规模错配：营收比 %.4f 超出 [%s, %s]" % (ratio, band[0], band[1]),
                "severity": "low",
                "state": "open",
                "evidence_needed": "营收、员工数、融资轮次，或可比的业务单元口径",
                "missing_fields": [],
            })
    else:
        unknown.append("revenue_cny")

    sev_set = set(c["severity"] for c in contradictions)
    if "high" in sev_set:
        risk, verdict = "high", "存疑"
    elif "medium" in sev_set:
        risk, verdict = "medium", "对标成立（标注不可比维度）"
    elif contradictions:
        risk, verdict = "low", "对标成立（降层观察）"
    else:
        risk, verdict = "low", "对标成立"

    if unknown and len(unknown) >= 3:
        verdict = "存疑（字段缺口过大）"

    note = "已检查 %d 个维度；反证状态均为 open，表示「暂未找到成立的反证」，不等于已排除风险。" % checked
    return {
        "risk": risk,
        "verdict": verdict,
        "contradictions": contradictions,
        "unknown_fields": unknown,
        "dimensions_checked": checked,
        "note": note,
    }


def render_md(payload):
    lines = ["# 反证清单", "",
             "目标：%s ｜ 生成日期：%s" % (payload["target_name"], payload["generated_at"]), ""]
    for item in payload["candidates"]:
        lines.append("## %s（%s）综合分 %s" % (
            item.get("name", item.get("id")), item.get("id"), item.get("score")))
        lines.append("")
        lines.append("风险：%s ｜ 结论：%s" % (item["critique"]["risk"], item["critique"]["verdict"]))
        lines.append("")
        rows = ["| 码 | 命题 | 严重度 | 状态 | 所需证据 |",
                "|---|---|---|---|---|"]
        for c in item["critique"]["contradictions"]:
            rows.append("| %s | %s | %s | %s | %s |" % (
                c["code"], c["claim"], c["severity"], c["state"], c["evidence_needed"]))
        if len(rows) == 2:
            rows.append("| 无 | 已试五类模板，未形成成立的反证 | 无 | open | 无 |")
        lines.extend(rows)
        lines.append("")
        if item["critique"]["unknown_fields"]:
            lines.append("未验证字段：%s（缺字段即 unverified，不写「无反证」）" % "、".join(
                item["critique"]["unknown_fields"]))
            lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="结构化反证清单")
    ap.add_argument("--target", required=True, help="目标画像 JSON")
    ap.add_argument("--candidates", required=True, help="fused.json")
    ap.add_argument("--out", required=True, help="输出 critique.json")
    ap.add_argument("--md", help="另存一份可读版 markdown")
    ap.add_argument("--min-score", type=float, default=55.0, help="低于该分不做反证")
    ap.add_argument("--band", default="0.02,50", help="规模带，默认 0.02,50")
    args = ap.parse_args(argv)

    target = load_json(args.target)
    if not isinstance(target, dict):
        die("目标画像需为对象")
    attrs = target.get("attributes") if isinstance(target.get("attributes"), dict) else target

    raw = load_json(args.candidates)
    cands = raw.get("candidates") if isinstance(raw, dict) else raw
    if not isinstance(cands, list):
        die("候选文件需为数组或 {\"candidates\": [...]}")

    try:
        band = [float(x) for x in str(args.band).split(",")]
        if len(band) != 2 or band[0] >= band[1]:
            raise ValueError
    except ValueError:
        die("--band 需形如 0.02,50")

    out = []
    for cand in cands:
        fused = cand.get("fused") or {}
        score = num(fused.get("score"))
        block = {"id": cand.get("id"), "name": cand.get("name"), "score": score}
        if fused.get("status") != "ok" or score is None:
            block["critique"] = {
                "risk": "unknown",
                "verdict": "跳过（未通过质量门或无分数）",
                "contradictions": [],
                "unknown_fields": [],
                "dimensions_checked": 0,
                "note": "质量门未通过，不做反证，也不得出现在结论段。",
            }
        elif score < args.min_score:
            block["critique"] = {
                "risk": "low",
                "verdict": "跳过（分数低于反证阈值 %.0f）" % args.min_score,
                "contradictions": [],
                "unknown_fields": [],
                "dimensions_checked": 0,
                "note": "低分候选不做深度反证，进 T3 观察。",
            }
        else:
            block["critique"] = judge(attrs, cand, score, band)
        out.append(block)

    basis = raw.get("basis_date") if isinstance(raw, dict) else None
    payload = {
        "target_name": str(target.get("name", "未命名目标")),
        "generated_at": basis or "",
        "min_score": args.min_score,
        "summary": {
            "total": len(out),
            "high": len([1 for b in out if b["critique"]["risk"] == "high"]),
            "medium": len([1 for b in out if b["critique"]["risk"] == "medium"]),
            "low": len([1 for b in out if b["critique"]["risk"] == "low"]),
            "unknown": len([1 for b in out if b["critique"]["risk"] == "unknown"]),
        },
        "candidates": out,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    if args.md:
        with open(args.md, "w", encoding="utf-8") as fh:
            fh.write(render_md(payload))

    sys.stdout.write("critique: %d 条，high %d / medium %d -> %s\n" % (
        len(out), payload["summary"]["high"], payload["summary"]["medium"], args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

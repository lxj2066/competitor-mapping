#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""drift.py — 增量重评的漂移检测（D1 至 D5）

用法:
    python drift.py --baseline baseline.json --current current.json --out drift.json
    python drift.py --baseline baseline.json --current current.json --out drift.json --md drift.md

退出码:
    0 正常；2 输入或用法错误

口径:
    D1 专利主题漂移     主题分布 JS 散度（base 2）超阈
    D2 招聘技术栈漂移   top20 关键词 Jaccard 低于阈值
    D3 产品与定价变更   结构指纹变化或定价带中位变动超阈
    D4 融资并购事件     期内出现新事件（唯一触发全量重审）
    D5 专利增速异常     当期与基线年均之比超出区间
"""

import argparse
import json
import math
import os
import sys

SCOPE = {
    "D1": "技术定位重评（局部）",
    "D2": "观察名单升级评估（局部）",
    "D3": "产品重叠重算（局部）",
    "D4": "全量重审：分类口径、产业链位、分层",
    "D5": "技术定位重评（局部）",
}

DEFAULT_RULES = {
    "D1": {"js_max": 0.15},
    "D2": {"jaccard_min": 0.60, "top_n": 20},
    "D3": {"pricing_pct": 0.20},
    "D4": {"enabled": True},
    "D5": {"ratio_max": 2.0, "ratio_min": 0.3},
}


def die(msg):
    sys.stderr.write("drift.py: %s\n" % msg)
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


def norm_dist(dist):
    if not isinstance(dist, dict):
        return {}
    items = {str(k): max(num(v) or 0.0, 0.0) for k, v in dist.items()}
    total = sum(items.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in items.items()}


def kl_base2(p, q):
    out = 0.0
    for key, pv in p.items():
        if pv <= 0:
            continue
        qv = q.get(key, 0.0)
        if qv <= 0:
            qv = 1e-12
        out += pv * math.log(pv / qv, 2)
    return out


def js_divergence(a, b):
    pa, pb = norm_dist(a), norm_dist(b)
    if not pa or not pb:
        return None
    keys = set(pa) | set(pb)
    m = {k: 0.5 * (pa.get(k, 0.0) + pb.get(k, 0.0)) for k in keys}
    return 0.5 * kl_base2(pa, m) + 0.5 * kl_base2(pb, m)


def jaccard(a, b, top_n):
    sa = set(str(x).lower() for x in (a or [])[:top_n])
    sb = set(str(x).lower() for x in (b or [])[:top_n])
    if not sa and not sb:
        return None
    return len(sa & sb) / float(len(sa | sb))


def band_shift(old, new):
    if not isinstance(old, list) or not isinstance(new, list):
        return None
    if len(old) != 2 or len(new) != 2:
        return None
    o = (num(old[0]) + num(old[1])) / 2.0
    n = (num(new[0]) + num(new[1])) / 2.0
    if o is None or n is None or o <= 0:
        return None
    return abs(n - o) / o


def detect(base, cur, rules):
    triggers = []
    for code, spec in rules.items():
        if code == "D1":
            js = js_divergence(base.get("patent_topics"), cur.get("patent_topics"))
            if js is not None and js > float(spec.get("js_max", 0.15)):
                triggers.append({"code": "D1", "metric": "js_divergence",
                                 "value": round(js, 4), "threshold": spec.get("js_max", 0.15),
                                 "action": SCOPE["D1"]})
        elif code == "D2":
            jac = jaccard(base.get("hiring_keywords"), cur.get("hiring_keywords"),
                          int(spec.get("top_n", 20)))
            if jac is not None and jac < float(spec.get("jaccard_min", 0.60)):
                triggers.append({"code": "D2", "metric": "jaccard",
                                 "value": round(jac, 4),
                                 "threshold": spec.get("jaccard_min", 0.60),
                                 "action": SCOPE["D2"]})
        elif code == "D3":
            hb, hc = str(base.get("pricing_page_hash", "")), str(cur.get("pricing_page_hash", ""))
            shift = band_shift(base.get("pricing_band_cny"), cur.get("pricing_band_cny"))
            limit = float(spec.get("pricing_pct", 0.20))
            if hb and hc and hb != hc:
                triggers.append({"code": "D3", "metric": "page_hash",
                                 "value": "changed", "threshold": "unchanged",
                                 "action": SCOPE["D3"]})
            elif shift is not None and shift > limit:
                triggers.append({"code": "D3", "metric": "pricing_shift",
                                 "value": round(shift, 4), "threshold": limit,
                                 "action": SCOPE["D3"]})
        elif code == "D4":
            if not spec.get("enabled", True):
                continue
            eb = str(base.get("last_funding_event", "") or "")
            ec = str(cur.get("last_funding_event", "") or "")
            if ec and ec != eb:
                triggers.append({"code": "D4", "metric": "funding_event",
                                 "value": ec, "threshold": "期内无新事件",
                                 "action": SCOPE["D4"]})
        elif code == "D5":
            pb, pc = num(base.get("patent_count")), num(cur.get("patent_count"))
            if pb and pc and pb > 0:
                ratio = pc / pb
                if ratio > float(spec.get("ratio_max", 2.0)) or \
                        ratio < float(spec.get("ratio_min", 0.3)):
                    triggers.append({"code": "D5", "metric": "patent_ratio",
                                     "value": round(ratio, 4),
                                     "threshold": [spec.get("ratio_min", 0.3),
                                                   spec.get("ratio_max", 2.0)],
                                     "action": SCOPE["D5"]})
    return triggers


def render_md(payload):
    lines = ["# 漂移检测与重评建议", "",
             "生成日期：%s" % payload["generated_at"], ""]
    if not payload["entities"]:
        lines.append("无触发项。")
        return "\n".join(lines) + "\n"
    rows = ["| 主体 | 码 | 指标 | 值 | 阈值 | 重评范围 |", "|---|---|---|---|---|---|"]
    for ent in payload["entities"]:
        for t in ent["triggers"]:
            rows.append("| %s | %s | %s | %s | %s | %s |" % (
                ent["name"], t["code"], t["metric"], t["value"], t["threshold"], t["action"]))
    lines.extend(rows)
    lines.append("")
    lines.append("全量重审触发：%s" % ("是" if payload["full_rerun"] else "否"))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="漂移检测 D1 至 D5")
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--current", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rules")
    ap.add_argument("--md")
    ap.add_argument("--generated-at", default="")
    args = ap.parse_args(argv)

    base = load_json(args.baseline)
    cur = load_json(args.current)
    rules = dict(DEFAULT_RULES)
    if args.rules:
        override = load_json(args.rules)
        if not isinstance(override, dict):
            die("rules 需为对象")
        for code, spec in override.items():
            if isinstance(spec, dict):
                rules.setdefault(code, {}).update(spec)

    base_map = {str(e.get("id")): e for e in (base.get("entities") or [])}
    now_map = {str(e.get("id")): e for e in (cur.get("entities") or [])}

    entities, warnings = [], []
    full_rerun = False
    for eid in sorted(now_map):
        if eid not in base_map:
            warnings.append("%s 无基线，需先建基线再谈漂移" % eid)
            continue
        applied = now_map[eid].get("rules")
        use_rules = rules
        if isinstance(applied, list) and applied:
            use_rules = {c: rules[c] for c in applied if c in rules}
        if len(use_rules) < 2:
            warnings.append("%s 监控规则不足 2 条，单一信号易假阳性" % eid)
        triggers = detect(base_map[eid], now_map[eid], use_rules)
        if any(t["code"] == "D4" for t in triggers):
            full_rerun = True
        if triggers:
            entities.append({
                "id": eid,
                "name": now_map[eid].get("name", eid),
                "rules": sorted(use_rules),
                "triggers": triggers,
            })

    payload = {
        "generated_at": args.generated_at,
        "rules": rules,
        "full_rerun": full_rerun,
        "summary": {
            "entities_checked": len([1 for e in now_map if e in base_map]),
            "entities_triggered": len(entities),
            "triggers": sum(len(e["triggers"]) for e in entities),
        },
        "warnings": warnings,
        "entities": entities,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    if args.md:
        with open(args.md, "w", encoding="utf-8") as fh:
            fh.write(render_md(payload))

    sys.stdout.write("drift: 检查 %d 家，触发 %d 家 / %d 条，全量重审=%s -> %s\n" % (
        payload["summary"]["entities_checked"], payload["summary"]["entities_triggered"],
        payload["summary"]["triggers"], "是" if full_rerun else "否", args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate.py — 确定性质量门 G1 至 G7

用法:
    python gate.py --candidates candidates.json --config mapping-scope.json --out gated.json
    python gate.py --candidates candidates.json --out gated.json --basis-date 2026-10-26

输入:
    candidates.json  {"candidates": [ <候选>, ... ]}  或  [ <候选>, ... ]
    config（可选）   见 references/gate-rules.md 第 2 节

输出:
    gated.json       每个候选附 gate 块（status / codes / reasons / flags / tier_cap / channels_usable）
                     与 summary 计数

退出码:
    0 正常；2 输入或用法错误

约束:
    纯标准库；同输入必得同输出；不做任何网络访问。
"""

import argparse
import json
import os
import sys
from datetime import date, timedelta

CHANNELS = ("value_chain", "patent_graph", "patent_semantic",
            "product_market", "graph_diffusion")

DEFAULT_TTL = {
    "value_chain": 540,
    "patent_graph": 1825,
    "patent_semantic": 1825,
    "product_market": 240,
    "graph_diffusion": 540,
}

DROP_STATUS = ("deregistered", "revoked", "closed", "terminated", "cancelled")
ALLOWED_CLASS = ("direct", "indirect", "potential", "comparable")


def die(msg):
    sys.stderr.write("gate.py: %s\n" % msg)
    raise SystemExit(2)


def load_json(path):
    if not os.path.isfile(path):
        die("文件不存在: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as exc:
        die("%s 不是合法 JSON: %s" % (path, exc))


def to_date(value):
    if not value:
        return None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def evidence_of(cand):
    out = []
    signals = cand.get("signals") or {}
    for ch in CHANNELS:
        node = signals.get(ch) or {}
        for ev in (node.get("evidence") or []):
            if isinstance(ev, dict):
                out.append((ch, ev))
    return out


def channel_dates(cand):
    latest = {}
    for ch, ev in evidence_of(cand):
        d = to_date(ev.get("date"))
        if d is None:
            continue
        if ch not in latest or d > latest[ch]:
            latest[ch] = d
    return latest


def scaled(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate(cand, cfg, basis, entity_seen):
    """返回 (status, codes, reasons, flags, tier_cap, channels_usable)。"""
    codes, reasons, flags = [], [], []
    attrs = cand.get("attributes") or {}
    status = "pass"
    tier_cap = "T1"

    def worse(new):
        order = {"pass": 0, "hold": 1, "drop": 2}
        return new if order[new] > order[status] else status

    # ---- G1 经营状态
    st = str(attrs.get("status", "unknown")).lower()
    if st == "active":
        pass
    elif st in DROP_STATUS:
        codes.append("G1")
        reasons.append("G1 经营状态为 %s" % st)
        status = worse("drop")
    else:
        codes.append("G1")
        reasons.append("G1 经营状态未知（%s）" % (st or "缺失"))
        flags.append("status_unknown")
        status = worse("hold")

    # ---- G2 来源独立性（只认 E1 / E2）
    keys = []
    for _ch, ev in evidence_of(cand):
        if str(ev.get("source_type", "")).upper() not in ("E1", "E2"):
            continue
        key = str(ev.get("independent_key", "")).strip()
        if key and key not in keys:
            keys.append(key)
    need = int(cfg.get("min_independent_sources", 2))
    if len(keys) >= need:
        pass
    elif len(keys) == 0:
        codes.append("G2")
        reasons.append("G2 无 E1/E2 级独立来源")
        status = worse("drop")
    else:
        codes.append("G2")
        reasons.append("G2 独立来源 %d 条，低于要求的 %d 条" % (len(keys), need))
        status = worse("hold")

    # ---- G3 证据时效
    ttl = dict(DEFAULT_TTL)
    ttl.update(cfg.get("ttl_days") or {})
    dates = channel_dates(cand)
    usable, stale = [], []
    for ch in CHANNELS:
        node = (cand.get("signals") or {}).get(ch) or {}
        if scaled(node.get("score")) is None:
            continue
        d = dates.get(ch)
        limit = int(ttl.get(ch, 540))
        if d is None or (basis - d) > timedelta(days=limit):
            stale.append(ch)
        else:
            usable.append(ch)
    if stale and not usable:
        codes.append("G3")
        reasons.append("G3 全部通道证据超期或缺失日期: %s" % ",".join(stale))
        flags.append("stale_all")
        status = worse("hold")
    elif stale:
        flags.append("stale_partial")
        reasons.append("G3 部分通道超期已剔除: %s" % ",".join(stale))

    # ---- G4 主体同一性
    ek = str(attrs.get("entity_key", "")).strip()
    if not ek:
        codes.append("G4")
        reasons.append("G4 缺主体唯一键")
        flags.append("entity_unknown")
        status = worse("hold")
    elif ek in entity_seen:
        codes.append("G4")
        reasons.append("G4 主体重复，重复于 %s" % entity_seen[ek])
        flags.append("dup_of:%s" % entity_seen[ek])
        status = worse("hold")
    else:
        entity_seen[ek] = str(cand.get("id", "?"))

    # ---- G5 口径对齐
    cls = str(attrs.get("classification", "")).strip().lower()
    allowed = cfg.get("classification_allowed") or list(ALLOWED_CLASS)
    if cls not in allowed:
        codes.append("G5")
        reasons.append("G5 未归入竞争口径（classification=%s）" % (cls or "缺失"))
        status = worse("drop")
    elif not str(attrs.get("classification_basis", "")).strip():
        codes.append("G5")
        reasons.append("G5 有分类但无分类依据")
        status = worse("hold")

    # ---- G6 规模合理性
    band = cfg.get("scale_ratio_band") or [0.02, 50.0]
    target_rev = scaled(cfg.get("target_revenue_cny"))
    rev = scaled(attrs.get("revenue_cny"))
    if rev is None or target_rev in (None, 0):
        flags.append("scale_unknown")
        tier_cap = "T2"
    else:
        ratio = rev / target_rev
        if ratio < float(band[0]) or ratio > float(band[1]):
            codes.append("G6")
            reasons.append("G6 营收比 %.3f 超出 [%s, %s]" % (ratio, band[0], band[1]))
            flags.append("scale_mismatch")
            tier_cap = "T3"

    # ---- G7 核验时效
    lv = to_date(attrs.get("last_verified"))
    max_age = int(cfg.get("verify_max_age_days", 120))
    if lv is None:
        codes.append("G7")
        reasons.append("G7 缺核验日期")
        flags.append("verify_unknown")
        status = worse("hold")
    elif (basis - lv) > timedelta(days=max_age):
        codes.append("G7")
        reasons.append("G7 核验日期距今 %d 天，超过 %d 天" % ((basis - lv).days, max_age))
        status = worse("hold")

    if status != "pass":
        tier_cap = "T3" if status == "drop" else tier_cap

    return status, codes, reasons, flags, tier_cap, usable


def main(argv=None):
    ap = argparse.ArgumentParser(description="确定性质量门 G1 至 G7")
    ap.add_argument("--candidates", required=True, help="候选批次 JSON")
    ap.add_argument("--config", help="口径快照 JSON（可选）")
    ap.add_argument("--out", required=True, help="输出 gated.json")
    ap.add_argument("--basis-date", help="基准日 YYYY-MM-DD，默认取当天")
    args = ap.parse_args(argv)

    raw = load_json(args.candidates)
    cands = raw.get("candidates") if isinstance(raw, dict) else raw
    if not isinstance(cands, list):
        die("候选文件需为数组或 {\"candidates\": [...]}")

    cfg = load_json(args.config) if args.config else {}
    if not isinstance(cfg, dict):
        die("config 顶层需为对象")

    basis = to_date(args.basis_date) or to_date(cfg.get("basis_date")) or date.today()

    entity_seen = {}
    out, counts = [], {"pass": 0, "hold": 0, "drop": 0}
    for cand in cands:
        if not isinstance(cand, dict):
            die("候选数组含非对象元素")
        status, codes, reasons, flags, tier_cap, usable = evaluate(
            cand, cfg, basis, entity_seen)
        counts[status] += 1
        item = dict(cand)
        item["gate"] = {
            "status": status,
            "codes": codes,
            "reasons": reasons,
            "flags": flags,
            "tier_cap": tier_cap,
            "channels_usable": usable,
        }
        out.append(item)

    payload = {
        "basis_date": basis.isoformat(),
        "thresholds": {
            "min_independent_sources": int(cfg.get("min_independent_sources", 2)),
            "ttl_days": dict(DEFAULT_TTL, **(cfg.get("ttl_days") or {})),
            "scale_ratio_band": cfg.get("scale_ratio_band") or [0.02, 50.0],
            "verify_max_age_days": int(cfg.get("verify_max_age_days", 120)),
        },
        "summary": dict(counts, total=len(out)),
        "candidates": out,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    sys.stdout.write("gate: %d 条 -> pass %d / hold %d / drop %d -> %s\n" % (
        len(out), counts["pass"], counts["hold"], counts["drop"], args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

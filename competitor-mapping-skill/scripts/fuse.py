#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fuse.py — 校准融合打分（对数几率池化 + 冲突惩罚 + 通道级 bootstrap）

两种用法:

  1) 打分
     python fuse.py --candidates gated.json --out fused.json [--weights weights.json]

  2) 用标注对校准通道权重
     python fuse.py --calibrate labels.json --candidates candidates.json --out weights.json

输入说明:
    候选需含 signals.<channel>.score（0 至 1）。若含 gate.channels_usable 则只用可用通道。
    标注对形如 {"pairs": [{"a": "c001", "b": "c002", "preferred": "c001"}]}

退出码:
    0 正常；2 输入或用法错误

约束:
    纯标准库；随机种子固定；同输入必得同输出。
"""

import argparse
import json
import math
import os
import random
import sys

CHANNELS = ("value_chain", "patent_graph", "patent_semantic",
            "product_market", "graph_diffusion")

PRIOR_WEIGHTS = {
    "value_chain": 0.28,
    "patent_graph": 0.18,
    "patent_semantic": 0.22,
    "product_market": 0.22,
    "graph_diffusion": 0.10,
}

DEFAULT_PARAMS = {
    "eps": 0.02,
    "agree_threshold": 0.60,
    "agree_bonus": 0.25,
    "conflict_tau": 0.45,
    "conflict_lambda": 1.5,
    "bootstrap": 400,
    "seed": 20260101,
    "low_confidence_width": 30.0,
    "ridge": 0.001,
    "min_pairs": 12,
}


def die(msg):
    sys.stderr.write("fuse.py: %s\n" % msg)
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
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out):
        return None
    return out


def sigmoid(x):
    if x >= 700:
        return 1.0
    if x <= -700:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def logit(p, eps):
    p = min(max(p, eps), 1.0 - eps)
    return math.log(p / (1.0 - p))


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    idx = int(round(q * (len(ordered) - 1)))
    idx = max(0, min(len(ordered) - 1, idx))
    return ordered[idx]


def channel_scores(cand):
    """返回 {channel: score}，只含可用且有分数的通道。"""
    usable = None
    gate = cand.get("gate") or {}
    if isinstance(gate.get("channels_usable"), list):
        usable = [c for c in gate["channels_usable"] if c in CHANNELS]
    out = {}
    signals = cand.get("signals") or {}
    for ch in CHANNELS:
        if usable is not None and ch not in usable:
            continue
        node = signals.get(ch) or {}
        score = num(node.get("score"))
        if score is None:
            continue
        out[ch] = min(max(score, 0.0), 1.0)
    return out


def normalize_weights(weights, channels):
    picked = {c: max(float(weights.get(c, 0.0) or 0.0), 0.0) for c in channels}
    total = sum(picked.values())
    if total <= 0:
        n = len(channels) or 1
        return {c: 1.0 / n for c in channels}
    return {c: v / total for c, v in picked.items()}


def combine(pairs, weights, params):
    """pairs: [(channel, score)]，weights 已在该集合上归一。"""
    wsum = sum(weights[c] for c, _ in pairs) or 1.0
    total = 0.0
    for ch, score in pairs:
        total += (weights[ch] / wsum) * logit(score, params["eps"])
    scores = [s for _, s in pairs]
    spread = (max(scores) - min(scores)) if len(scores) > 1 else 0.0
    agree = sum(1 for s in scores if s >= params["agree_threshold"]) / float(len(scores))
    penalty = params["conflict_lambda"] * max(0.0, spread - params["conflict_tau"])
    combined = total + params["agree_bonus"] * agree - penalty
    return combined, spread, agree, penalty


def fuse_one(cand, weights, params, rng):
    scores = channel_scores(cand)
    if not scores:
        return {"status": "no_signal", "score": None, "p": None, "ci": [None, None],
                "contributions": {}, "flags": ["no_signal"],
                "spread": None, "agree": None, "penalty": None}

    channels = [c for c in CHANNELS if c in scores]
    w = normalize_weights(weights, channels)
    pairs = [(c, scores[c]) for c in channels]
    combined, spread, agree, penalty = combine(pairs, w, params)
    p = sigmoid(combined)

    # 通道级 bootstrap，固定种子
    boot = []
    n = len(pairs)
    for _ in range(int(params["bootstrap"])):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        val, _sp, _ag, _pe = combine(sample, w, params)
        boot.append(100.0 * sigmoid(val))
    lo = percentile(boot, 0.05)
    hi = percentile(boot, 0.95)

    base = {}
    total_abs = 0.0
    for ch, score in pairs:
        val = w[ch] * logit(score, params["eps"])
        base[ch] = val
        total_abs += abs(val)
    contributions = {}
    for ch, val in base.items():
        share = 0.0 if total_abs == 0 else 100.0 * val / total_abs
        contributions[ch] = {"score": round(scores[ch], 3),
                             "weight": round(w[ch], 3),
                             "logodds": round(val, 3),
                             "share_pct": round(share, 1)}

    flags = []
    if n <= 1:
        flags.append("sparse")
    if spread > params["conflict_tau"]:
        flags.append("conflict")
    if penalty > 0:
        flags.append("penalized")
    width = None if (lo is None or hi is None) else (hi - lo)
    if width is not None and width > params["low_confidence_width"]:
        flags.append("low_confidence")

    return {
        "status": "ok",
        "score": round(100.0 * p, 1),
        "p": round(p, 4),
        "ci": [None if lo is None else round(lo, 1),
               None if hi is None else round(hi, 1)],
        "ci_width": None if width is None else round(width, 1),
        "contributions": contributions,
        "spread": round(spread, 3),
        "agree": round(agree, 3),
        "penalty": round(penalty, 3),
        "channels_used": channels,
        "flags": flags,
    }


# ---------------------------------------------------------------- 校准

def fit_channel(pairs, channel, ridge):
    """单变量 logistic 牛顿迭代：P(a 胜 b) = sigmoid(beta * Δscore)。"""
    data = []
    for item in pairs:
        a, b, pref = item["a_scores"], item["b_scores"], item["preferred"]
        if channel not in a or channel not in b:
            continue
        delta = a[channel] - b[channel]
        if abs(delta) < 1e-9:
            continue
        data.append((delta, 1.0 if pref == "a" else 0.0))
    if len(data) < 4:
        return None, len(data)

    beta = 0.0
    for _ in range(80):
        grad, hess = 0.0, ridge
        for delta, y in data:
            pred = sigmoid(beta * delta)
            grad += (y - pred) * delta
            hess += pred * (1.0 - pred) * delta * delta
        grad -= ridge * beta
        if hess <= 1e-12:
            break
        step = grad / hess
        beta += step
        if abs(step) < 1e-9:
            break
    return beta, len(data)


def calibrate(labels, cands, params):
    items = labels.get("pairs") if isinstance(labels, dict) else labels
    if not isinstance(items, list) or not items:
        die("标注文件需为 {\"pairs\": [...]}")

    by_id = {}
    for cand in cands:
        by_id[str(cand.get("id"))] = channel_scores(cand)

    prepared = []
    for item in items:
        if not isinstance(item, dict):
            continue
        a, b, pref = str(item.get("a")), str(item.get("b")), str(item.get("preferred"))
        if a not in by_id or b not in by_id or pref not in ("a", "b"):
            continue
        if pref == "b":
            prepared.append({"a_scores": by_id[b], "b_scores": by_id[a], "preferred": "a"})
        else:
            prepared.append({"a_scores": by_id[a], "b_scores": by_id[b], "preferred": "a"})

    min_pairs = int(params["min_pairs"])
    betas, counts = {}, {}
    for ch in CHANNELS:
        beta, n = fit_channel(prepared, ch, params["ridge"])
        counts[ch] = n
        betas[ch] = beta

    usable = {c: b for c, b in betas.items() if b is not None and b > 0}
    source = "calibrated"
    note = "权重由标注对拟合得到"
    if len(prepared) < min_pairs or not usable:
        source = "prior"
        note = "标注对 %d 条（少于 %d）或无可拟合通道，退回先验权重" % (
            len(prepared), min_pairs)
        weights = dict(PRIOR_WEIGHTS)
    else:
        raw = {c: usable.get(c, 0.0) for c in CHANNELS}
        total = sum(raw.values()) or 1.0
        weights = {c: round(v / total, 4) for c, v in raw.items()}

    return {
        "source": source,
        "note": note,
        "pairs_used": len(prepared),
        "channels": weights,
        "betas": {c: (None if b is None else round(b, 4)) for c, b in betas.items()},
        "pairs_per_channel": counts,
        "params": params,
    }


# ---------------------------------------------------------------- 主流程

def main(argv=None):
    ap = argparse.ArgumentParser(description="校准融合打分")
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--weights", help="权重文件（可选，缺省用先验）")
    ap.add_argument("--calibrate", help="标注文件，给了就只做权重校准")
    ap.add_argument("--params", help="参数覆盖 JSON")
    args = ap.parse_args(argv)

    params = dict(DEFAULT_PARAMS)
    if args.params:
        override = load_json(args.params)
        if not isinstance(override, dict):
            die("params 需为对象")
        params.update(override)

    raw = load_json(args.candidates)
    cands = raw.get("candidates") if isinstance(raw, dict) else raw
    if not isinstance(cands, list):
        die("候选文件需为数组或 {\"candidates\": [...]}")

    if args.calibrate:
        result = calibrate(load_json(args.calibrate), cands, params)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        sys.stdout.write("fuse: 校准权重 source=%s pairs=%d -> %s\n" % (
            result["source"], result["pairs_used"], args.out))
        return 0

    weights = dict(PRIOR_WEIGHTS)
    weight_source = "prior"
    if args.weights:
        wf = load_json(args.weights)
        if isinstance(wf, dict) and isinstance(wf.get("channels"), dict):
            weights = {c: num(wf["channels"].get(c, 0.0)) or 0.0 for c in CHANNELS}
            weight_source = str(wf.get("source", "external"))
        elif isinstance(wf, dict):
            weights = {c: num(wf.get(c, 0.0)) or 0.0 for c in CHANNELS}
            weight_source = "external"
        else:
            die("权重文件需为对象")

    rng = random.Random(int(params["seed"]))
    out, ranked = [], []
    for cand in cands:
        gate_status = (cand.get("gate") or {}).get("status", "pass")
        item = dict(cand)
        if gate_status != "pass":
            item["fused"] = {"status": "skipped",
                             "reason": "gate_status=%s" % gate_status,
                             "score": None, "rank": None}
            out.append(item)
            continue
        block = fuse_one(cand, weights, params, rng)
        tier_cap = (cand.get("gate") or {}).get("tier_cap", "T1")
        block["tier_cap"] = tier_cap
        item["fused"] = block
        out.append(item)
        if block["status"] == "ok":
            ranked.append((block["score"], str(cand.get("id")), item))

    ranked.sort(key=lambda r: (-r[0], r[1]))
    rank_map = {}
    for idx, (_s, cid, _item) in enumerate(ranked, start=1):
        rank_map[cid] = idx
    for _s, cid, item in ranked:
        item["fused"]["rank"] = rank_map[cid]

    payload = {
        "weights": {c: round(weights.get(c, 0.0), 4) for c in CHANNELS},
        "weight_source": weight_source,
        "params": params,
        "summary": {
            "total": len(out),
            "scored": len(ranked),
            "conflict": len([1 for _s, _c, i in ranked if "conflict" in i["fused"]["flags"]]),
            "low_confidence": len([1 for _s, _c, i in ranked
                                   if "low_confidence" in i["fused"]["flags"]]),
        },
        "candidates": out,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    top = ranked[0] if ranked else None
    sys.stdout.write("fuse: 打分 %d 条（权重来源 %s），最高 %s %s -> %s\n" % (
        len(ranked), weight_source,
        top[1] if top else "-", top[0] if top else "-", args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

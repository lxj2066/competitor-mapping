#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval.py — 检索层指标与留一法回测

用法:
    python eval.py --labels labels.json --run run.json --out eval-report.json
    python eval.py --loo holdout.json --run-loo run-loo.json --out loo-report.json

指标:
    P@K = 前 K 中 rel>=1 的条数 / K
    R@K = 前 K 中 rel>=1 的条数 / 标注集中 rel>=1 的总数
    NDCG@K = DCG@K / IDCG@K, DCG = sum((2^rel - 1) / log2(i + 1))

退出码:
    0 正常；2 输入或用法错误
"""

import argparse
import json
import math
import os
import sys


def die(msg):
    sys.stderr.write("eval.py: %s\n" % msg)
    raise SystemExit(2)


def load_json(path):
    if not os.path.isfile(path):
        die("文件不存在: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as exc:
        die("%s 不是合法 JSON: %s" % (path, exc))


def as_ranking(payload, target):
    if isinstance(payload, dict):
        runs = payload.get("runs") if isinstance(payload.get("runs"), dict) else payload
        seq = runs.get(target)
    else:
        seq = payload
    if not isinstance(seq, list):
        return []
    out = []
    for item in seq:
        if isinstance(item, dict):
            out.append(str(item.get("id")))
        else:
            out.append(str(item))
    return out


def dcg(rels):
    total = 0.0
    for i, rel in enumerate(rels, start=1):
        total += (2.0 ** rel - 1.0) / math.log2(i + 1.0)
    return total


def metrics_for(judged, ranking, k):
    rel_map = {str(j["id"]): int(j.get("rel", 0)) for j in judged}
    top = ranking[:k]
    rels = [rel_map.get(cid, 0) for cid in top]
    hits = sum(1 for r in rels if r >= 1)
    relevant_total = sum(1 for r in rel_map.values() if r >= 1)
    p = hits / float(k)
    r = None if relevant_total == 0 else hits / float(relevant_total)
    ideal = sorted([v for v in rel_map.values() if v > 0], reverse=True)[:k]
    idcg = dcg([float(x) for x in ideal])
    ndcg = None if idcg <= 0 else dcg([float(x) for x in rels]) / idcg
    return {
        "k": k,
        "hits": hits,
        "relevant_total": relevant_total,
        "precision": round(p, 4),
        "recall": None if r is None else round(r, 4),
        "ndcg": None if ndcg is None else round(ndcg, 4),
        "ranked": top,
        "rels": rels,
    }


def macro(rows, key):
    vals = [row[key] for row in rows if row.get(key) is not None]
    if not vals:
        return None
    return round(sum(vals) / float(len(vals)), 4)


def run_metrics(labels, run_payload):
    k = int(labels.get("k", 10))
    judged_sets = labels.get("judgments") or []
    if not isinstance(judged_sets, list) or not judged_sets:
        die("labels 需含 judgments 数组")
    rows = []
    for entry in judged_sets:
        target = str(entry.get("target"))
        judged = entry.get("candidates") or []
        ranking = as_ranking(run_payload, target)
        row = {"target": target}
        row.update(metrics_for(judged, ranking, k))
        rows.append(row)
    worst = None
    scored = [r for r in rows if r["recall"] is not None]
    if scored:
        worst = min(scored, key=lambda r: (r["recall"], r["ndcg"] or 0.0))["target"]
    return {
        "mode": "metrics",
        "k": k,
        "targets": len(rows),
        "macro": {"precision": macro(rows, "precision"),
                  "recall": macro(rows, "recall"),
                  "ndcg": macro(rows, "ndcg")},
        "worst_target": worst,
        "note": "宏平均之外必须看最差目标；平均好看而单目标全错等于未评估。",
        "rows": rows,
    }


def run_loo(holdout, run_payload):
    k = int(holdout.get("k", 10))
    items = holdout.get("items") or []
    if not isinstance(items, list) or not items:
        die("holdout 需含 items 数组")
    rows, hits = [], 0
    for item in items:
        target, cid = str(item.get("target")), str(item.get("id"))
        ranking = as_ranking(run_payload, target)[:k]
        hit = cid in ranking
        hits += 1 if hit else 0
        rows.append({"target": target, "held_out": cid,
                     "hit": hit, "rank": (ranking.index(cid) + 1) if hit else None})
    total = len(rows)
    rate = round(hits / float(total), 4) if total else None
    if rate is None:
        verdict = "无样本"
    elif rate >= 0.70:
        verdict = "召回路径可用"
    elif rate >= 0.40:
        verdict = "依赖种子过度，检查图通道权重"
    else:
        verdict = "召回路径失效，回 S2 重做通道设计"
    return {
        "mode": "loo",
        "k": k,
        "items": total,
        "hits": hits,
        "loo_recall": rate,
        "verdict": verdict,
        "note": "留一法测的是「没有先验时能否自己找到」，优于纯排序指标。",
        "rows": rows,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="指标与留一法回测")
    ap.add_argument("--labels")
    ap.add_argument("--run")
    ap.add_argument("--loo")
    ap.add_argument("--run-loo")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    if args.loo:
        if not args.run_loo:
            die("--loo 需搭配 --run-loo")
        report = run_loo(load_json(args.loo), load_json(args.run_loo))
    else:
        if not (args.labels and args.run):
            die("需 --labels 与 --run，或 --loo 与 --run-loo")
        report = run_metrics(load_json(args.labels), load_json(args.run))

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    if report["mode"] == "metrics":
        sys.stdout.write("eval: %d 个目标 P@%d=%s R@%d=%s NDCG@%d=%s -> %s\n" % (
            report["targets"], report["k"], report["macro"]["precision"],
            report["k"], report["macro"]["recall"], report["k"],
            report["macro"]["ndcg"], args.out))
    else:
        sys.stdout.write("eval(loo): %d/%d 命中，LOO-Recall@%d=%s（%s）-> %s\n" % (
            report["hits"], report["items"], report["k"],
            report["loo_recall"], report["verdict"], args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

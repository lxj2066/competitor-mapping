#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_tests.py — 脚本回归用例（正例 / 反例 / 边界）

用法:
    python tests/run_tests.py
    python tests/run_tests.py -v

断言只增不改口径。要让用例变绿，先改包，不许改断言。
"""

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
FIX = HERE
SCRIPTS = os.path.join(SKILL, "scripts")

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def call(script, *args):
    proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + list(args),
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise AssertionError("%s 退出码 %d\nstdout: %s\nstderr: %s" % (
            script, proc.returncode, proc.stdout.strip(), proc.stderr.strip()))
    return proc


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def by_id(payload):
    return {str(c.get("id")): c for c in payload["candidates"]}


def pipeline(work):
    gated = os.path.join(work, "gated.json")
    fused = os.path.join(work, "fused.json")
    crit = os.path.join(work, "critique.json")
    call("gate.py", "--candidates", os.path.join(FIX, "fixture-candidates.json"),
         "--config", os.path.join(FIX, "fixture-mapping-scope.json"), "--out", gated)
    call("fuse.py", "--candidates", gated, "--out", fused)
    call("critique.py", "--target", os.path.join(FIX, "fixture-target-profile.json"),
         "--candidates", fused, "--out", crit)
    return gated, fused, crit


@case
def test_gate_rules(work):
    gated, _f, _c = pipeline(work)
    data = load(gated)
    c = by_id(data)
    expect = {"c001": "pass", "c002": "hold", "c003": "drop",
              "c004": "pass", "c005": "pass", "c006": "drop",
              "c007": "hold", "c008": "pass"}
    for cid, status in expect.items():
        assert c[cid]["gate"]["status"] == status, \
            "%s 期望 %s，实际 %s（%s）" % (cid, status, c[cid]["gate"]["status"],
                                        c[cid]["gate"]["reasons"])
    assert "G2" in c["c002"]["gate"]["codes"], "单源候选未命中 G2"
    assert "G1" in c["c003"]["gate"]["codes"], "停业候选未命中 G1"
    assert "G5" in c["c006"]["gate"]["codes"], "未分类候选未命中 G5"
    assert "G4" in c["c007"]["gate"]["codes"], "缺主体键未命中 G4"
    assert "G7" in c["c007"]["gate"]["codes"], "缺核验日期未命中 G7"
    assert c["c005"]["gate"]["tier_cap"] == "T3", "规模错配未收紧 tier_cap"
    assert data["summary"]["total"] == 8, "候选总数不对"


@case
def test_fuse_math_and_flags(work):
    _g, fused, _c = pipeline(work)
    data = load(fused)
    c = by_id(data)
    top = [cid for cid, item in c.items()
           if (item["fused"].get("rank") or 99) == 1]
    assert top == ["c004"], "按分排序第一名应为 c004，实际 %s" % top
    assert c["c001"]["fused"]["rank"] == 2, "c001 应为第二名"
    assert "conflict" not in c["c001"]["fused"]["flags"], "c001 不应判冲突"
    assert c["c001"]["fused"]["score"] >= 75, "c001 分数应达 T1 线"
    assert "conflict" in c["c008"]["fused"]["flags"], "通道冲突未被标记"
    assert c["c008"]["fused"]["penalty"] > 0, "冲突惩罚未生效"
    assert c["c008"]["fused"]["score"] < 55, "冲突候选分数未被压下"
    assert c["c002"]["fused"]["status"] == "skipped", "未过门候选不应打分"
    lo, hi = c["c001"]["fused"]["ci"]
    assert lo is not None and hi is not None and lo < hi, "置信区间不成立"
    contrib = c["c001"]["fused"]["contributions"]
    shares = sum(abs(v["share_pct"]) for v in contrib.values())
    assert 99.0 <= shares <= 101.0, "通道贡献占比未归一：%s" % shares


@case
def test_fuse_deterministic(work):
    gated, fused, _c = pipeline(work)
    again = os.path.join(work, "fused2.json")
    call("fuse.py", "--candidates", gated, "--out", again)
    with open(fused, "r", encoding="utf-8") as a, open(again, "r", encoding="utf-8") as b:
        assert a.read() == b.read(), "同输入两次打分结果不一致（随机种子未固定）"


@case
def test_calibration_discriminates(work):
    out = os.path.join(work, "weights.json")
    call("fuse.py", "--calibrate", os.path.join(FIX, "fixture-calib-labels.json"),
         "--candidates", os.path.join(FIX, "fixture-calib-candidates.json"), "--out", out)
    data = load(out)
    assert data["source"] == "calibrated", "12 对标注应能完成校准，实际 %s" % data["source"]
    assert data["pairs_used"] == 12, "标注对数不对：%s" % data["pairs_used"]
    assert data["channels"]["value_chain"] > 0.2, "判别力通道权重过低"
    assert data["channels"]["patent_semantic"] == 0.0, "无判别力通道应得 0 权重"
    total = sum(data["channels"].values())
    assert abs(total - 1.0) < 1e-6, "权重未归一：%s" % total

    prior = os.path.join(work, "weights-prior.json")
    call("fuse.py", "--calibrate", os.path.join(FIX, "fixture-calib-labels-short.json"),
         "--candidates", os.path.join(FIX, "fixture-calib-candidates.json"), "--out", prior)
    assert load(prior)["source"] == "prior", "标注不足时应退回先验权重"


@case
def test_critique_veto(work):
    _g, _f, crit = pipeline(work)
    data = load(crit)
    c = by_id(data)
    codes = [x["code"] for x in c["c004"]["critique"]["contradictions"]]
    assert "TR" in codes, "技术路线互斥未被识别"
    assert c["c004"]["critique"]["risk"] == "high", "高分加技术互斥应判高危"
    assert c["c001"]["critique"]["contradictions"] == [], "c001 不应有反证"
    assert c["c001"]["critique"]["risk"] == "low", "c001 风险应为 low"
    codes5 = [x["code"] for x in c["c005"]["critique"]["contradictions"]]
    assert "SC" in codes5, "规模错配反证未生成"
    assert c["c002"]["critique"]["risk"] == "unknown", "未过门候选不应给反证结论"
    assert "open" in json.dumps(data, ensure_ascii=False), "反证状态应为 open 而非已排除"


@case
def test_dossier_tiers(work):
    _g, fused, crit = pipeline(work)
    md = os.path.join(work, "dossier.md")
    html = os.path.join(work, "dossier.html")
    call("dossier.py", "--fused", fused, "--critique", crit, "--out", md,
         "--html", html, "--scope", os.path.join(FIX, "fixture-mapping-scope.json"))
    text = open(md, "r", encoding="utf-8").read()
    assert "同类A" in text, "T1 未包含 c001"
    assert "## 未决项" in text, "缺未决项段落"
    assert "## 证据强度" in text, "缺证据强度段落"
    assert "不构成投资建议" in text, "缺合规声明"

    data = load(fused)
    tier_c005 = "T3" if "小体量E" in text.split("## T3")[-1] else "missing"
    assert tier_c005 == "T3", "规模错配候选未进 T3"
    assert "跨路线D" in text, "跨路线候选未出现"
    section_t1 = text.split("## T1 深度档案")[1].split("## T2")[0]
    assert "跨路线D" not in section_t1, "高危反证候选不得进 T1"
    page = open(html, "r", encoding="utf-8").read()
    assert "<table>" in page and page.startswith("<!DOCTYPE html>"), "HTML 未生成"


@case
def test_metrics_exact(work):
    out = os.path.join(work, "eval.json")
    call("eval.py", "--labels", os.path.join(FIX, "fixture-labels.json"),
         "--run", os.path.join(FIX, "fixture-run.json"), "--out", out)
    data = load(out)
    macro = data["macro"]
    assert macro["precision"] == 0.5, "P@2 应为 0.5，实际 %s" % macro["precision"]
    assert macro["recall"] == 0.5, "R@2 应为 0.5，实际 %s" % macro["recall"]
    assert abs(macro["ndcg"] - 0.8262) < 1e-3, "NDCG@2 应为 0.8262，实际 %s" % macro["ndcg"]


@case
def test_loo(work):
    out = os.path.join(work, "loo.json")
    call("eval.py", "--loo", os.path.join(FIX, "fixture-holdout.json"),
         "--run-loo", os.path.join(FIX, "fixture-run-loo.json"), "--out", out)
    data = load(out)
    assert data["hits"] == 1 and data["items"] == 2, "留一法命中数不对"
    assert data["loo_recall"] == 0.5, "LOO-Recall 应为 0.5"
    assert "依赖种子" in data["verdict"], "0.5 应判依赖种子过度"


@case
def test_drift(work):
    out = os.path.join(work, "drift.json")
    call("drift.py", "--baseline", os.path.join(FIX, "fixture-drift-baseline.json"),
         "--current", os.path.join(FIX, "fixture-drift-current.json"), "--out", out,
         "--generated-at", "2026-10-26")
    data = load(out)
    by = {e["id"]: e for e in data["entities"]}
    assert "c001" in by, "漂移主体未被识别"
    codes = [t["code"] for t in by["c001"]["triggers"]]
    for code in ("D1", "D2", "D3", "D4", "D5"):
        assert code in codes, "漏触发 %s（实际 %s）" % (code, codes)
    assert data["full_rerun"] is True, "D4 触发必须要求全量重审"
    assert "c002" not in by, "无变化主体不应进触发列表"
    d1 = [t for t in by["c001"]["triggers"] if t["code"] == "D1"][0]
    assert d1["value"] > d1["threshold"], "JS 散度未超阈却触发"


@case
def test_bad_input_rejected():
    proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "gate.py"),
                           "--candidates", os.path.join(FIX, "__not_exist__.json"),
                           "--out", os.path.join(tempfile.gettempdir(), "x.json")],
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 2, "不存在的输入应退出码 2，实际 %d" % proc.returncode
    assert "不存在" in proc.stderr, "错误信息应说明文件不存在"


def main():
    verbose = "-v" in sys.argv
    work = tempfile.mkdtemp(prefix="cm-test-")
    failures = []
    try:
        for fn in CASES:
            name = fn.__name__
            try:
                needs = fn.__code__.co_argcount
                fn(work) if needs else fn()
                if verbose:
                    sys.stdout.write("PASS  %s\n" % name)
            except AssertionError as exc:
                failures.append((name, str(exc)))
                sys.stdout.write("FAIL  %s\n      %s\n" % (name, exc))
        total = len(CASES)
        sys.stdout.write("\n%d/%d 通过%s\n" % (total - len(failures), total,
                                             "，全部绿" if not failures else ""))
        return 0 if not failures else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
EVAL_DIR = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "outputs" / "prompt-eval-20260714-round1"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def calculate(run: dict, judge_run: dict) -> dict:
    gold = load(EVAL_DIR / judge_run["gold_file"])
    score = load(EVAL_DIR / judge_run["score_file"])
    points = {item["id"]: item for item in gold.get("points", [])}
    point_scores = {item["id"]: float(item["score"]) for item in score.get("point_coverage", [])}
    weighted_denominator = sum(int(item["importance"]) for item in points.values())
    weighted_recall = sum(int(item["importance"]) * point_scores.get(item_id, 0) for item_id, item in points.items()) / weighted_denominator
    critical = [point_scores.get(item_id, 0) for item_id, item in points.items() if int(item["importance"]) == 3]
    negative = [point_scores.get(item_id, 0) for item_id, item in points.items() if item["category"] in {"negative", "risk"}]

    questions = {item["id"]: item for item in gold.get("questions", [])}
    question_scores = {item["id"]: item for item in score.get("question_coverage", [])}
    intent_recall = mean([1.0 if question_scores.get(item_id, {}).get("question_retained") else 0.0 for item_id in questions])
    answer_total = sum(len(item.get("answer_elements", [])) for item in questions.values())
    answer_covered = sum(
        len({index for index in question_scores.get(item_id, {}).get("answer_elements_covered", []) if isinstance(index, int) and 0 <= index < len(item.get("answer_elements", []))})
        for item_id, item in questions.items()
    )
    answer_recall = answer_covered / answer_total if answer_total else None
    followup_total = sum(len(item.get("followup", [])) for item in questions.values())
    followup_covered = sum(
        len({index for index in question_scores.get(item_id, {}).get("followup_covered", []) if isinstance(index, int) and 0 <= index < len(item.get("followup", []))})
        for item_id, item in questions.items()
    )
    followup_recall = followup_covered / followup_total if followup_total else None
    unresolved_ids = [item_id for item_id, item in questions.items() if item.get("answer_status") in {"partial", "unanswered"}]
    unresolved_preservation = mean([1.0 if question_scores.get(item_id, {}).get("answer_status_preserved") else 0.0 for item_id in unresolved_ids])
    order_accuracy = mean([1.0 if question_scores.get(item_id, {}).get("order_correct") else 0.0 for item_id in questions])
    qa_components = [intent_recall, answer_recall, followup_recall, unresolved_preservation, order_accuracy]
    qa_weights = [0.20, 0.55, 0.10, 0.10, 0.05]
    qa_completeness = sum((component if component is not None else 1.0) * weight for component, weight in zip(qa_components, qa_weights))

    number_scores = [float(item["score"]) for item in score.get("number_coverage", [])]
    number_full = mean([1.0 if value == 1 else 0.0 for value in number_scores])
    number_semantic = mean(number_scores)
    uncertainty_recall = mean([1.0 if item.get("retained") else 0.0 for item in score.get("uncertainty_coverage", [])])
    unsupported = score.get("unsupported_claims", [])
    claims = max(int(score.get("summary_claims_count") or 0), 1)
    hallucination_rate = len(unsupported) / claims
    major_hallucinations = sum(1 for item in unsupported if item.get("severity") == "major")
    redundancy_rate = int(score.get("repeated_claims_count") or 0) / claims
    output_chars = int(run["output_characters"])
    compression_ratio = output_chars / int(run["transcript_characters"])

    point_notes = {item["id"]: item for item in score.get("point_coverage", [])}
    missed_critical = [
        {**item, "score": point_scores.get(item_id, 0), "note": point_notes.get(item_id, {}).get("note", "")}
        for item_id, item in points.items()
        if int(item["importance"]) == 3 and point_scores.get(item_id, 0) < 1
    ]
    missed_questions = [
        {
            **item,
            "question_retained": bool(question_scores.get(item_id, {}).get("question_retained")),
            "covered": len({index for index in question_scores.get(item_id, {}).get("answer_elements_covered", []) if isinstance(index, int) and 0 <= index < len(item.get("answer_elements", []))}),
            "total": len(item.get("answer_elements", [])),
            "note": question_scores.get(item_id, {}).get("note", ""),
        }
        for item_id, item in questions.items()
        if (not question_scores.get(item_id, {}).get("question_retained"))
        or len({index for index in question_scores.get(item_id, {}).get("answer_elements_covered", []) if isinstance(index, int) and 0 <= index < len(item.get("answer_elements", []))}) < len(item.get("answer_elements", []))
    ]
    return {
        "template": run["template_name"],
        "transcript": Path(run["transcript_path"]).name,
        "output_file": run["output_file"],
        "gold_file": judge_run["gold_file"],
        "score_file": judge_run["score_file"],
        "point_count": len(points),
        "question_count": len(questions),
        "number_count": len(number_scores),
        "uncertainty_count": len(gold.get("uncertainties", [])),
        "weighted_recall": weighted_recall,
        "omission_rate": 1 - weighted_recall,
        "critical_omission_rate": None if not critical else 1 - sum(critical) / len(critical),
        "negative_omission_rate": None if not negative else 1 - sum(negative) / len(negative),
        "intent_recall": intent_recall,
        "answer_recall": answer_recall,
        "followup_recall": followup_recall,
        "unresolved_preservation": unresolved_preservation,
        "order_accuracy": order_accuracy,
        "qa_completeness": qa_completeness,
        "number_full": number_full,
        "number_semantic": number_semantic,
        "uncertainty_recall": uncertainty_recall,
        "hallucination_rate": hallucination_rate,
        "major_hallucinations": major_hallucinations,
        "unsupported_claims": unsupported,
        "redundancy_rate": redundancy_rate,
        "output_chars": output_chars,
        "transcript_chars": int(run["transcript_characters"]),
        "compression_ratio": compression_ratio,
        "latency_ms": int(run["latency_ms"]),
        "finish_reason": run.get("finish_reason"),
        "missed_critical": missed_critical,
        "missed_questions": missed_questions,
    }


def main() -> None:
    manifest = load(EVAL_DIR / "manifest.json")
    if "round3" in EVAL_DIR.name:
        round_label = "第三轮"
    elif "round2" in EVAL_DIR.name:
        round_label = "第二轮"
    else:
        round_label = "第一轮"
    judge_runs = {item["template_name"]: item for item in manifest["judge"]["runs"]}
    results = [calculate(run, judge_runs[run["template_name"]]) for run in manifest["runs"] if run["template_name"] in judge_runs]
    (EVAL_DIR / "metrics.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# 四类 VC 访谈 Prompt {round_label}评测",
        "",
        "- 评测日期：2026-07-14",
        f"- 生成模型：{manifest['profile']['model']}（profile: {manifest['profile']['name']} v{manifest['profile']['version']}）",
        f"- 运行策略：{manifest['strategy']}；input budget={manifest['profile']['input_token_budget']}，max output={manifest['profile']['max_output_tokens']}",
        "- 真值范围：仅以各自 transcript 为准；ASR 错误不在本轮纠正。",
        "- 方法：先独立抽取 P/Q/N/U gold checklist，再逐项评分。覆盖分 1/0.5/0；遗漏率为加权要点召回的补数。",
        "- 限制：每个模板仅生成一次；gold 与评分均由同一模型完成，适合发现方向性问题，不等同于双人盲审。",
        "",
        "## 汇总",
        "",
        "| 模板 | 匹配 transcript | Gold 要点/Q | 遗漏率 | 关键点遗漏 | Q&A 完整性 | 数字完全保真 | 幻觉率/重大 | 输出字符 | 压缩比 | 冗余率 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in results:
        lines.append(
            f"| {item['template']} | {item['transcript']} | {item['point_count']}/{item['question_count']} | "
            f"{pct(item['omission_rate'])} | {pct(item['critical_omission_rate'])} | {pct(item['qa_completeness'])} | "
            f"{pct(item['number_full'])} | {pct(item['hallucination_rate'])}/{item['major_hallucinations']} | "
            f"{item['output_chars']:,} | {pct(item['compression_ratio'])} | {pct(item['redundancy_rate'])} |"
        )

    for item in results:
        lines.extend([
            "",
            f"## {item['template']}",
            "",
            f"- 原稿：`{item['transcript']}`（{item['transcript_chars']:,} 字符）",
            f"- 纪要：`{item['output_file']}`（{item['output_chars']:,} 字符；生成 {item['latency_ms']/1000:.1f}s；finish_reason={item['finish_reason']}）",
            f"- 要点：加权召回 {pct(item['weighted_recall'])}，遗漏 {pct(item['omission_rate'])}；关键点遗漏 {pct(item['critical_omission_rate'])}；负面/风险遗漏 {pct(item['negative_omission_rate'])}。",
            f"- Q&A：问题意图 {pct(item['intent_recall'])}，回答要素 {pct(item['answer_recall'])}，追问链 {pct(item['followup_recall'])}，未答/部分回答状态 {pct(item['unresolved_preservation'])}，综合完整性 {pct(item['qa_completeness'])}。",
            f"- 保真：数字完全保真 {pct(item['number_full'])}，数字语义覆盖 {pct(item['number_semantic'])}，疑点保留 {pct(item['uncertainty_recall'])}，幻觉 {len(item['unsupported_claims'])} 条（重大 {item['major_hallucinations']} 条）。",
            "",
            "### 主要关键点缺口",
            "",
        ])
        if item["missed_critical"]:
            for missed in item["missed_critical"][:8]:
                lines.append(f"- `{missed['id']}` [{missed['timestamp']}] 覆盖={missed['score']}: {missed['description']} — {missed['note']}")
        else:
            lines.append("- 未发现 importance=3 的缺口。")
        lines.extend(["", "### 主要 Q&A 缺口", ""])
        if item["missed_questions"]:
            for missed in item["missed_questions"][:8]:
                lines.append(
                    f"- `{missed['id']}` [{missed['timestamp']}] 问题保留={missed['question_retained']}，"
                    f"回答要素={missed['covered']}/{missed['total']}: {missed['intent']} — {missed['note']}"
                )
        else:
            lines.append("- 未发现 Q&A 缺口。")
        lines.extend(["", "### 无依据陈述", ""])
        if item["unsupported_claims"]:
            for claim in item["unsupported_claims"]:
                lines.append(f"- **{claim.get('severity', 'unknown')}**：{claim.get('claim', '')} — {claim.get('note', '')}")
        else:
            lines.append("- 未发现。")

    (EVAL_DIR / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(EVAL_DIR / "comparison.md")


if __name__ == "__main__":
    main()

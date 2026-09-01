from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
ROUND1 = ROOT / "outputs" / "prompt-eval-20260714-round1"
ROUND2 = ROOT / "outputs" / "prompt-eval-20260714-round2"
OUTPUT = ROOT / "outputs" / "prompt-eval-20260714-comparison.md"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def delta_pct(old: float | None, new: float | None, lower_is_better: bool = False) -> str:
    if old is None or new is None:
        return "N/A"
    delta = (old - new if lower_is_better else new - old) * 100
    return f"{delta:+.1f}pp"


def q_headings(directory: Path, file_name: str) -> int:
    text = (directory / file_name).read_text(encoding="utf-8")
    return len(re.findall(r"^### Q\d+", text, flags=re.MULTILINE))


def main() -> None:
    first = {item["template"]: item for item in load(ROUND1 / "metrics.json")}
    second = {item["template"]: item for item in load(ROUND2 / "metrics.json")}
    lines = [
        "# 四类 VC 访谈 Prompt 两轮对比",
        "",
        "## 结论",
        "",
        "第二轮显著降低四类纪要的要点遗漏，并把重大幻觉从 2 条降为 0。详细 Q&A 的显式问题数明显增加，但客户与团队模板对追问链、部分回答状态的结构化仍不稳定；通用模板出现较高重复率。这些是下一轮最值得继续优化的方向。",
        "",
        "## 指标变化",
        "",
        "| 模板 | 遗漏率 R1→R2 | 改善 | Q&A 完整性 R1→R2 | 变化 | 数字完全保真 R1→R2 | 重大幻觉 R1→R2 | Q 标题 R1→R2 | 篇幅 R1→R2 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, old in first.items():
        new = second[name]
        old_q = q_headings(ROUND1, old["output_file"])
        new_q = q_headings(ROUND2, new["output_file"])
        lines.append(
            f"| {name} | {pct(old['omission_rate'])}→{pct(new['omission_rate'])} | {delta_pct(old['omission_rate'], new['omission_rate'], True)} | "
            f"{pct(old['qa_completeness'])}→{pct(new['qa_completeness'])} | {delta_pct(old['qa_completeness'], new['qa_completeness'])} | "
            f"{pct(old['number_full'])}→{pct(new['number_full'])} | {old['major_hallucinations']}→{new['major_hallucinations']} | "
            f"{old_q}→{new_q} | {old['output_chars']:,}→{new['output_chars']:,} |"
        )

    lines.extend([
        "",
        "## 分模板判断",
        "",
        "### 首次交流",
        "",
        "- 遗漏率由 16.7% 降至 6.5%，负面/风险遗漏由 25.0% 降至 0，Q&A 完整性升至 98.6%。",
        "- Q 标题由 4 增至 13，仍低于 gold 的 28 个问题，原因是同主题问题仍被收束到同一个 Q 下。",
        "- 数字完全保真从 84.6% 降至 80.8%，主要是部分关键经营数字仍被省略，而非新增错误数字。",
        "",
        "### 通用模板",
        "",
        "- 遗漏率由 8.8% 降至 6.6%，Q&A 完整性由 87.3% 升至 98.8%，数字完全保真达到 100%。",
        "- 首轮出现的效率数字重大幻觉已消失，Q 标题从 11 增至 19，覆盖 gold 的 18 个问题。",
        "- 冗余率由 6.0% 升至 20.0%，说明“主题归纳 + 完整 Q&A”仍有重复，需要后续进一步压缩主题归纳。",
        "",
        "### 客户访谈",
        "",
        "- 遗漏率由 7.9% 降至 1.2%，关键点和负面/风险遗漏均降至 0，合同状态重大幻觉已消失。",
        "- Q 标题由 12 增至 28，已接近 gold 的 27 个问题；篇幅相应从 6,974 增至 10,929 字符。",
        "- Q&A 综合分下降主要来自追问没有按 gold 的父子关系呈现，以及部分回答要素被压缩；这是结构问题，不是问题意图遗漏。",
        "",
        "### 团队访谈",
        "",
        "- 遗漏率由 6.1% 降至 3.6%，关键点遗漏降至 0，幻觉降至 0。",
        "- Q 标题由 16 增至 27，接近 gold 的 30 个问题；回答要素召回仍为 98.6%。",
        "- 综合 Q&A 分下降来自一处部分回答状态未明确标注，以及 gold 追问链未按父子关系保留；正文信息覆盖本身没有明显退化。",
        "",
        "## 评测限制",
        "",
        "- 每个模板每轮仅生成一次，未测生成随机性。",
        "- gold 与评分使用同一模型；虽冻结 gold 后复用到第二轮，但仍不是人工双盲标注。",
        "- Q 标题数量不是完整性的充分条件，只用于辅助检查模型是否仍在选择性摘录问题。",
        "- 四份 transcript 均含未公开业务、客户、技术或人员信息，评测产物仅保存在本地 outputs，不能提交。",
    ])
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()

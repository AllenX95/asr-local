from __future__ import annotations

import json
import re
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parents[3]
OUTPUTS = ROOT / "outputs"
ROUND2 = OUTPUTS / "prompt-eval-20260714-round2"
RUN1 = OUTPUTS / "prompt-eval-20260714-round3-run1"
RUN2 = OUTPUTS / "prompt-eval-20260714-round3-run2"
REPORT = OUTPUTS / "prompt-eval-20260714-round3-comparison.md"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def metrics(directory: Path):
    return {item["template"]: item for item in load(directory / "metrics.json")}


def pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def avg(items: list[dict], key: str) -> float | None:
    values = [item[key] for item in items if item.get(key) is not None]
    return mean(values) if values else None


def range_pct(items: list[dict], key: str) -> str:
    values = [item[key] for item in items if item.get(key) is not None]
    if not values:
        return "N/A"
    return f"{min(values)*100:.1f}%–{max(values)*100:.1f}%"


def summary_text(directory: Path, item: dict) -> str:
    return (directory / item["output_file"]).read_text(encoding="utf-8")


def count_pattern(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text, flags=re.MULTILINE))


def section_text(text: str, heading_prefix: str, next_heading_prefix: str) -> str:
    start = text.find(heading_prefix)
    if start < 0:
        return ""
    end = text.find(next_heading_prefix, start + len(heading_prefix))
    return text[start : end if end >= 0 else len(text)]


def main() -> None:
    baseline = metrics(ROUND2)
    first = metrics(RUN1)
    second = metrics(RUN2)
    lines = [
        "# 四类 VC 访谈 Prompt 第三轮最小化精调评测",
        "",
        "## Overall Assessment: Share with caveats",
        "",
        "通用模板和团队模板的第三轮改动可保留；客户模板的第三轮改动在两次运行中稳定性和覆盖均退化，已回退到第二轮版本。评测使用修正后的显式问题 gold，并用同一 gold 回评第二轮基线。",
        "",
        "## 指标对比",
        "",
        "| 模板 | 指标 | 第二轮（修正 gold） | 第三轮两次均值 | 两次范围 | 判断 |",
        "|---|---|---:|---:|---:|---|",
    ]
    for name in ("通用模板", "客户访谈", "团队访谈问答"):
        old = baseline[name]
        runs = [first[name], second[name]]
        decision = {"通用模板": "保留", "客户访谈": "回退", "团队访谈问答": "保留"}[name]
        rows = [
            ("遗漏率", old["omission_rate"], avg(runs, "omission_rate"), range_pct(runs, "omission_rate")),
            ("Q&A 完整性", old["qa_completeness"], avg(runs, "qa_completeness"), range_pct(runs, "qa_completeness")),
            ("回答要素召回", old["answer_recall"], avg(runs, "answer_recall"), range_pct(runs, "answer_recall")),
            ("数字完全保真", old["number_full"], avg(runs, "number_full"), range_pct(runs, "number_full")),
        ]
        for index, (label, old_value, new_value, value_range) in enumerate(rows):
            lines.append(f"| {name if index == 0 else ''} | {label} | {pct(old_value)} | {pct(new_value)} | {value_range} | {decision if index == 0 else ''} |")

    generic_old = baseline["通用模板"]
    generic_runs = [first["通用模板"], second["通用模板"]]
    generic_old_text = summary_text(ROUND2, generic_old)
    generic_texts = [summary_text(RUN1, generic_runs[0]), summary_text(RUN2, generic_runs[1])]
    old_theme = section_text(generic_old_text, "## 四、", "## 五、")
    new_themes = [section_text(text, "## 四、", "## 五、") for text in generic_texts]

    customer_old = baseline["客户访谈"]
    customer_runs = [first["客户访谈"], second["客户访谈"]]
    team_old = baseline["团队访谈问答"]
    team_runs = [first["团队访谈问答"], second["团队访谈问答"]]
    team_texts = [summary_text(RUN1, team_runs[0]), summary_text(RUN2, team_runs[1])]

    lines.extend([
        "",
        "## 逐模板结论",
        "",
        "### 通用模板：保留第三轮",
        "",
        f"- 输出篇幅从 {generic_old['output_chars']:,} 字符降至平均 {mean(item['output_chars'] for item in generic_runs):,.0f} 字符，缩短 {(1-mean(item['output_chars'] for item in generic_runs)/generic_old['output_chars'])*100:.1f}%。",
        f"- 旧的“按主题归纳”段落为 {len(old_theme)} 字符；两次第三轮都严格输出纯 Q 编号索引，分别为 {len(new_themes[0])}/{len(new_themes[1])} 字符。",
        f"- Q 标题由第二轮 {count_pattern(generic_old_text, r'^### Q\d+')} 个增至 {count_pattern(generic_texts[0], r'^### Q\d+')}/{count_pattern(generic_texts[1], r'^### Q\d+')} 个；Q&A 完整性均未下降。",
        "- 两次第三轮均未检出无依据陈述；第二轮回评检出 2 条轻微无依据陈述。",
        "- 代价是数字完全保真由 95.8% 降至平均 89.6%，主要遗漏仓库面积、抓取成功率或销售目标等数字，未发现数字被改写成错误值。",
        "",
        "### 客户访谈：拒绝第三轮并回退",
        "",
        f"- 第二轮经修正 gold 回评：遗漏率 {pct(customer_old['omission_rate'])}、Q&A 完整性 {pct(customer_old['qa_completeness'])}、真实追问保留 {pct(customer_old['followup_recall'])}。",
        f"- 第三轮两次遗漏率为 {range_pct(customer_runs, 'omission_rate')}，Q&A 完整性为 {range_pct(customer_runs, 'qa_completeness')}，回答要素召回为 {range_pct(customer_runs, 'answer_recall')}。",
        "- 原因：更激进的父子归并把多个独立问题压成追问，第一次仅输出 17 个父 Q、第二次 22 个，低于 gold 的 27 个真实问题，并出现较大运行间波动。",
        "- 当前配置已恢复第二轮客户 Prompt；第三轮客户输出仅保留作失败实验记录。",
        "",
        "### 团队访谈：保留第三轮",
        "",
        f"- 遗漏率从 {pct(team_old['omission_rate'])} 降至平均 {pct(avg(team_runs, 'omission_rate'))}；Q&A 完整性从 {pct(team_old['qa_completeness'])} 升至平均 {pct(avg(team_runs, 'qa_completeness'))}。",
        f"- 第二轮未显式输出回答状态；第三轮两次分别输出 {count_pattern(team_texts[0], r'^\*\*(?:追问\s*\d+\s*)?回答状态')} 和 {count_pattern(team_texts[1], r'^\*\*(?:追问\s*\d+\s*)?回答状态')} 个状态标签。",
        f"- 两次输出长度分别为 {team_runs[0]['output_chars']:,}/{team_runs[1]['output_chars']:,} 字符，波动较大，但关键点遗漏均低，且未出现重大幻觉。",
        "- 修正后的 team gold 没有可计分的真实后续追问，因此“追问回答状态”本身只完成结构验证，仍需另一份含明确追问链的团队稿做专项复测。",
        "",
        "## 评测口径修正",
        "",
        "- Gold 只收录 transcript 中真实说出口的问题；不再把信息缺口或评测者建议当作追问。",
        "- Follow-up 只收录首次回答后真实发生的确认、澄清、量化或深挖问句。",
        "- 评分按语义匹配，不依赖 gold Q 编号、纪要 Q 编号或数组位置。",
        "- 覆盖索引会过滤越界下标，避免回答要素或追问保留率超过 100%。",
        "",
        "## 限制",
        "",
        "- 每个第三轮模板生成两次，仍不足以估计完整随机分布。",
        "- Gold 和逐项评分由同一模型完成，已做定义修正和局部人工抽查，但不是双人盲审。",
        "- 通用冗余改善主要由结构检查和篇幅验证支持；模型给出的重复率本轮区分度不足。",
        "- 评测产物包含未公开访谈信息，仅保存在本地 outputs，不应提交。",
    ])
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()

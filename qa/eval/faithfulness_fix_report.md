# RAGAS 评估口径修复报告：把图谱事实纳入检索上下文

> 目的：修正 faithfulness / context_recall 被**系统性低估**的测量缺陷。
> 结论：修复后 faithfulness 0.4027 → **0.7133**（+77%），
> 原"低分"主要是**测量问题**，而非回答质量本身。

---

## 一、问题：评估看不到图谱证据

问答引擎有**两条证据路径**，都会注入 Prompt：

| 路径 | 承载内容 | 进入 Prompt 的形式 | 是否被旧版评估采集 |
|---|---|---|---|
| 文档混合检索 | 向量 + BM25 + 重排后的文档 chunk | `参考资料 [1][2]…` | ✅ 采集 |
| 知识图谱 | 结构化事实（技术属性/材料/企业/政策…） | `知识图谱数据`（JSON） | ❌ **未采集** |

而 `qa/eval/gen_run.py` 采集 RAGAS 的 `contexts` 时只取了 `references`：

```python
# 修复前
"contexts": [r.get("text", "") for r in (result.get("references") or [])]
```

后果：**任何来自图谱的陈述，在 faithfulness / context_recall 判定时都算"无据可依"**。
典型如"磷酸铁锂电池的材料包括磷酸铁锂、石墨、铝箔…"——这句话完全由图谱
`materials` 字段支撑，但评估看不到，于是被判为不支持。

因此 0.4027 低分**不能直接解读为"幻觉严重"**，其中有相当部分是口径缺陷。

---

## 二、修复：让 Prompt 与评估共用同一段渲染文本

**关键设计**：不额外拼装文本给评估，而是**抽出唯一的渲染函数**，
Prompt 与评估都调它，保证"评估看到的 = LLM 实际看到的"。

```python
# qa/prompt_builder.py（新增）
@staticmethod
def format_kg_context(kg_context) -> Optional[str]:
    """把图谱上下文渲染成注入 Prompt 的那段文本（JSON 缩进格式）。"""
    if not kg_context:
        return None
    return json.dumps(kg_context, ensure_ascii=False, indent=2)
```

`build_user_prompt` 改为调用它（渲染结果逐字节不变），
`gen_run.py` 采集时在文档 contexts 之后追加同一函数输出。

> 为什么必须共用渲染：若两边各写一份，就可能出现"评估看到的文本 ≠
> LLM 看到的文本"，那等于**为了好看而操纵指标**。共用渲染是可验证的诚实做法。

实测验证：同一问题的 `contexts` 条数由 5 条变为 6 条，第 6 条即图谱事实 JSON。

---

## 三、修复前后对比（45 样本，全部成功判分）

| 指标 | 修复前 | 修复后 | 变化 |
|---|---|---|---|
| **faithfulness · 忠实度** | 0.4027 | **0.7133** | **+0.3106 (+77%)** |
| answer_relevancy · 答案相关性 | 0.8703 | 0.8643 | -0.0060（持平） |
| context_precision · 上下文精确率 | 0.4358 | 0.4419 | +0.0061 |
| **context_recall · 上下文召回率** | 0.3161 | **0.5868** | **+0.2707** |

（对照原始样本：`collected_samples.before_kg_fix.json`）

### 解读

1. **faithfulness +0.31** 主要来自"图谱事实重新变为有据可依"。
   这证实了原假设：低分是**测量缺陷**，不是回答在编造。
2. **context_recall +0.27** 同理——标准答案里的技术属性/材料/企业等陈述，
   原先图谱虽已提供却未被计入上下文，导致"覆盖率"被低估。
3. **context_precision 几乎不动**：图谱事实作为**单条**上下文加入，
   而 context_precision 是按排序计算的（越靠前的相关片段权重越高）；
   一条包罗万象的 JSON 放在末尾，对"排序精确率"帮助有限。
   这是该指标的口径使然，不代表图谱无用。
4. **answer_relevancy 持平**：它衡量的是"回答是否切题"，与证据来源无关，
   不受本次修改影响——这本身是一个**健康的对照信号**：
   若它也明显变化，反而说明我们的改动引入了全局偏差。

---

## 四、仍然存在的问题（诚实清单）

1. **faithfulness 0.71 仍有约 29% 的陈述未被支持**。这部分是真实的幻觉空间，
   值得继续查（候选方向：回答中超出证据的常识性补充、图谱与文档事实冲突时的取舍）。
2. **图谱上下文作为单条巨块**，对 context_precision 这类"排序敏感"指标不友好。
   若要更公平，应把图谱事实**拆成多条细粒度证据**（如按 技术属性/材料/企业 分条），
   但这会改变 contexts 结构，需单独设计。
3. **`context_recall` 只覆盖 45 个带 `reference_answer` 的条目**（主集 60 题无标准答案），
   因此它反映的是这批样本的语料充分性，不是全评测集的。
4. 本报告只换了"上下文采集口径"，**未改动任何生成逻辑**，故回答内容本身未变。

---

## 五、复现命令

```bash
# ① 修复后的采集 + 评分（含图谱事实）
python -m qa.eval.gen_run --collect-live --samples-output qa/eval/collected_samples.json

# ② 仅对已采集样本重新评分（不重新采集，省额度）
python -m qa.eval.gen_run --samples qa/eval/collected_samples.json
```

对照样本（修复前口径）保留在 `qa/eval/collected_samples.before_kg_fix.json`。

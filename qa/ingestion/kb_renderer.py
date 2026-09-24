#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
结构化知识 → Markdown 语料渲染层（可扩展知识供给线）

## 为什么需要这一层

项目里的事实来源只有一个：`data/new_energy.json`（每条技术一份 18 字段的结构化记录）。
它已经通过 `graph/build_newenergy_graph.py` 进入 Neo4j 知识图谱，但**没有被送进向量库**，
于是文档检索（向量/BM25/混合/重排）只能拿原始 PDF 当语料——实测那批 PDF 与评测集
期望实体的覆盖率只有 36%，导致检索消融实验无法可信归因。

本模块负责补上这条支路：

    data/new_energy.json
        ├──► graph/build_newenergy_graph.py ──► Neo4j     （已有，不动）
        └──► kb_renderer ──► docs/kb/<类别>/<技术名>.md
                                └──► qa.ingestion.run ──► Qdrant  （已有管道，零改动）

## 关键设计决策

1. **一条技术 = 一个文件**（而不是把 63 条拼成一个大 md）
   因为 `qa/ingestion/run.py::compute_doc_id()` 是**按文件内容算 SHA-256** 的：
   - 新增一项技术 → 只多一个文件，`classify_documents()` 自动识别为待处理，其余全部跳过；
   - 修订某项技术 → 只有那个文件的哈希变化，只重摄它。
   这是"知识库可持续扩展"能落地的前提，而非为了好看。

2. **文件内部用 `##` 分字段组**
   `chunker.py` 维护标题栈生成 `section` 路径（如
   `新能源技术知识库 / 储能 / 磷酸铁锂电池 / 材料与设备与企业`），
   引用溯源与 `expected_section` 判定都能直接使用。

3. **不修改既有摄取管道**
   本模块只产出标准 Markdown，之后完全复用 `qa.ingestion.run` 的
   幂等写入、增量去重、日志与测试——不重复造轮子。

4. **空值不渲染**
   字段为空/占位（None、""、[]、"未知"、"暂无" 等）时整节省略，
   避免产生"该技术暂无可提供的信息"这类无信息量的 chunk 稀释检索。

## 已知边界（诚实清单）

- **孤儿 chunk**：`upsert` 只覆盖同 `(doc_id, chunk_index)` 的点，不删除多余点。
  若某项技术字段变短（5 块 → 3 块），旧的第 4、5 块会残留在库里。
  修订后请用 `python -m qa.ingestion.run --collection newenergy_kb --recreate` 重建集合。
- **图谱侧非增量**：`build_newenergy_graph.py::clear_database()` 是全量清空重建，
  新增技术时需重跑它，否则两库会漂移（向量库有、图谱没有）。

用法：
    # 覆盖率自检（纯离线，不烧 API、不写文件）
    python -m qa.ingestion.kb_renderer check
    python -m qa.ingestion.kb_renderer check --json

    # 渲染出 Markdown（默认 docs/kb/）
    python -m qa.ingestion.kb_renderer render
    python -m qa.ingestion.kb_renderer render --dry-run

    # 渲染 + 直接摄取进独立集合
    python -m qa.ingestion.kb_renderer render --ingest --collection newenergy_kb
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from qa import config

# ==================== 路径与常量 ====================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_RECORDS = PROJECT_ROOT / "data" / "new_energy.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "kb"

# 生成物标记：写在每个文件头部，声明"这是生成文件，请勿手改"
GENERATED_MARKER = "<!-- generated-by: qa.ingestion.kb_renderer -->"

# 顶层标题：chunker 会把它拼进 section 路径的第一段
KB_ROOT_TITLE = "新能源技术知识库"

# 字段分组：渲染成 5 个 `##` 小节。
# 顺序即文件内顺序，也决定 chunk 在文档里的先后。
SECTION_FIELDS: List[Tuple[str, Tuple[str, ...]]] = [
    ("概述与原理", ("desc", "principle", "category")),
    ("优缺点与效率", ("advantage", "disadvantage", "efficiency", "maturity", "development_stage")),
    ("材料与设备与企业", ("materials", "equipments", "companies")),
    ("应用与政策", ("applications", "policies")),
    ("指标与成本与竞争", ("indicators", "cost_level", "market_share", "compete_technologies")),
]

# 字段 → 中文标签（渲染成 `- 标签：值`）
FIELD_LABELS: Dict[str, str] = {
    "desc": "技术描述",
    "principle": "工作原理",
    "category": "所属类别",
    "advantage": "优点",
    "disadvantage": "缺点",
    "efficiency": "效率",
    "maturity": "成熟度",
    "development_stage": "发展阶段",
    "materials": "所用材料",
    "equipments": "所需设备",
    "companies": "相关企业",
    "applications": "应用场景",
    "policies": "支持政策",
    "indicators": "技术指标",
    "cost_level": "成本等级",
    "market_share": "市场份额",
    "compete_technologies": "竞争技术",
}

# 视为"空"的占位值：出现时不渲染该字段
PLACEHOLDER_VALUES = {"", "-", "--", "无", "暂无", "未知", "待补充", "n/a", "na", "none", "null"}

# 文件名里需要替换掉的非法字符（Windows 最严格：\ / : * ? " < > |）
# 用 `+` 把连续非法字符合并成一个下划线，避免出现 "非法___字符" 这类名字
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


# ==================== 值格式化（纯函数）====================


def _is_placeholder(value: Any) -> bool:
    """判断一个标量值是否为空/占位（占位不渲染）"""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    return value.strip().lower() in PLACEHOLDER_VALUES


def normalize_list(value: Any) -> List[str]:
    """
    把可能是 list / str / None 的字段统一成"去空后的字符串列表"。

    JSON 里这些字段绝大多数是 list[str]，但为兼容手工录入时写成单个字符串，
    这里做一次归一化，避免渲染层因为数据形态不齐而崩。
    """
    if value is None:
        return []
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, (list, tuple, set)):
        candidates = list(value)
    else:
        candidates = [str(value)]

    cleaned: List[str] = []
    for item in candidates:
        if item is None:
            continue
        text = str(item).strip()
        if text and not _is_placeholder(text) and text not in cleaned:
            cleaned.append(text)
    return cleaned


def format_field(field: str, value: Any) -> List[str]:
    """
    把单个字段渲染成 Markdown 行（不含 `##` 标题）。

    - 列表字段 → `- 优点：A` / `- 优点：B`（每条独立成行，避免被切块截断后语义不全）
    - 标量字段 → `- 技术描述：xxx`

    Returns:
        渲染行列表；字段为空时返回 []（由调用方决定是否省略整节）。
    """
    label = FIELD_LABELS.get(field, field)

    if isinstance(value, (list, tuple, set)) or (value is not None and not isinstance(value, str)):
        items = normalize_list(value)
        return [f"- {label}：{item}" for item in items]

    if _is_placeholder(value):
        return []
    return [f"- {label}：{str(value).strip()}"]


def render_sections(record: Dict[str, Any]) -> List[Tuple[str, List[str]]]:
    """
    按 SECTION_FIELDS 把一条记录渲染成 [(小节名, 行列表), ...]。

    空小节会被整体跳过（不输出 `##` 标题），保证每个 chunk 都有实质内容。
    """
    sections: List[Tuple[str, List[str]]] = []
    for heading, fields in SECTION_FIELDS:
        lines: List[str] = []
        for field in fields:
            lines.extend(format_field(field, record.get(field)))
        if lines:
            sections.append((heading, lines))
    return sections


def sanitize_filename(name: str) -> str:
    """把技术名转成安全文件名（合并连续非法字符、压缩空白、去掉首尾多余符号）"""
    cleaned = _ILLEGAL_FILENAME_CHARS.sub("_", str(name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip("._")
    return cleaned or "unnamed"


def category_dir(record: Dict[str, Any]) -> str:
    """取类别名作为目录名；缺失或占位时归入 `未分类`"""
    raw = record.get("category")
    if _is_placeholder(raw):
        return "未分类"
    return sanitize_filename(str(raw).strip()) or "未分类"


# ==================== 文档渲染（纯函数）====================


def render_record_markdown(record: Dict[str, Any]) -> str:
    """
    把一条技术记录渲染成完整 Markdown 文档。

    结构：
        <!-- generated-by: ... -->   ← 生成物标记
        # 磷酸铁锂电池                 ← 顶层标题（chunker 用它做 section 第 1 段）
        ## 概述与原理                  ← 二级标题（section 第 2 段）
        - 技术描述：...
        ## 材料与设备与企业
        - 所用材料：磷酸铁锂
    """
    name = str(record.get("name") or "").strip()
    if not name:
        raise ValueError("记录缺少 name 字段，无法渲染")

    lines: List[str] = [GENERATED_MARKER, "", f"# {name}", ""]
    for heading, body in render_sections(record):
        lines.append(f"## {heading}")
        lines.extend(body)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ==================== 数据加载 ====================


def load_records(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """加载结构化知识记录（默认 data/new_energy.json）"""
    path = Path(path) if path else DEFAULT_RECORDS
    if not path.exists():
        raise FileNotFoundError(f"知识记录文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f)
    if not isinstance(records, list):
        raise ValueError(f"{path} 顶层应为数组，实际为 {type(records).__name__}")

    valid = [r for r in records if isinstance(r, dict) and str(r.get("name") or "").strip()]
    dropped = len(records) - len(valid)
    if dropped:
        logger.warning(f"[kb] 跳过 {dropped} 条缺少 name 的记录")
    logger.info(f"[kb] 已加载 {len(valid)} 条知识记录 <- {path.name}")
    return valid


# ==================== 覆盖率自检（不写文件、不调 API）====================


def coverage_report(
    records: List[Dict[str, Any]],
    expected_entities: List[str],
) -> Dict[str, Any]:
    """
    检查"评测集的期望实体"是否都能在渲染产物里找到。

    这是渲染层最重要的验收指标：语料覆盖不到某个实体，
    那么文档检索那几组（向量/BM25/混合/重排）对该题注定零召回，
    消融数字就反映"语料缺失"而不是"检索策略优劣"。

    Returns:
        {"records", "expected_total", "covered", "missing", "coverage",
         "per_record_hits": {name: [命中的期望实体]}}
    """
    # 只按渲染后的文本判定——必须保证"渲染出来的内容"真的包含该实体，
    # 而不是原始 JSON 里恰好有这个字符串
    per_record_hits: Dict[str, List[str]] = {}
    for record in records:
        text = render_record_markdown(record)
        hits = [e for e in expected_entities if e and e in text]
        per_record_hits[str(record.get("name"))] = hits

    covered = {e for hits in per_record_hits.values() for e in hits}
    missing = [e for e in expected_entities if e not in covered]

    return {
        "records": len(records),
        "expected_total": len(expected_entities),
        "covered": len(covered),
        "missing": missing,
        "coverage": (len(covered) / len(expected_entities)) if expected_entities else 1.0,
        "per_record_hits": per_record_hits,
    }


def load_expected_entities() -> List[str]:
    """
    从评测集读取全部期望实体（含多轮指代题——它们同样需要语料支撑）。

    延迟导入 `qa.eval.dataset`，避免模块导入期就拉起评测依赖。
    """
    from qa.eval.dataset import load_qa_golden

    entities: List[str] = []
    for entry in load_qa_golden():
        for name in entry.get("expected_entities") or []:
            if name and name not in entities:
                entities.append(name)
    return entities


# ==================== 渲染落盘 ====================


def build_render_plan(
    records: List[Dict[str, Any]],
    output_dir: Path,
) -> List[Dict[str, Any]]:
    """
    生成渲染计划（不改文件系统，便于测试与 --dry-run）。

    Returns:
        [{"record", "name", "category", "path", "content"}, ...]
        路径：<output_dir>/<类别>/<技术名>.md
    """
    output_dir = Path(output_dir)
    plan: List[Dict[str, Any]] = []
    for record in records:
        name = str(record["name"]).strip()
        path = output_dir / category_dir(record) / f"{sanitize_filename(name)}.md"
        plan.append({
            "record": record,
            "name": name,
            "category": category_dir(record),
            "path": path,
            "content": render_record_markdown(record),
        })
    return plan


def write_render_plan(
    plan: List[Dict[str, Any]],
    manifest_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    把渲染计划写入磁盘，并可选写出清单文件（便于追踪与清理历史生成物）。

    Returns:
        {"written", "unchanged", "manifest", "files"}
    """
    written, unchanged = 0, 0
    files: List[str] = []

    for item in plan:
        path: Path = item["path"]
        content: str = item["content"]
        path.parent.mkdir(parents=True, exist_ok=True)

        # newline="" 保证写出的是 LF，不受 Windows 自动转换影响——
        # 文件字节稳定，compute_doc_id 的哈希才稳定，增量判断才可靠
        if path.exists() and path.read_text(encoding="utf-8") == content:
            unchanged += 1
        else:
            path.write_text(content, encoding="utf-8", newline="")
            written += 1
        files.append(str(path))

    result = {"written": written, "unchanged": unchanged, "manifest": None, "files": files}

    if manifest_path is not None:
        manifest_path = Path(manifest_path)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "generated_by": "qa.ingestion.kb_renderer",
                    "record_count": len(plan),
                    "files": files,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
            newline="",
        )
        result["manifest"] = str(manifest_path)

    return result


def clean_stale(manifest_path: Path, keep_paths: List[str]) -> int:
    """
    清理"上次生成、这次不再生成"的文件（技术被删除/改名后的残留）。

    注意顺序：必须在**渲染之后**调用，且只删不在本次产物清单里的文件。
    早期版本在渲染前无条件删除全部历史产物，结果每轮都全量重写——
    内容虽然一致，但会白跑一次全量 embedding，日志也无法反映真实增量。

    Args:
        manifest_path: 上次运行写出的清单。
        keep_paths: 本次实际生成的文件路径（字符串形式）。

    Returns:
        删除的文件数。
    """
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        return 0
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[kb] 清单不可读，跳过陈旧文件清理：{e}")
        return 0

    keep = {str(Path(p)) for p in keep_paths}
    removed = 0
    for raw in data.get("files") or []:
        path = Path(raw)
        if str(path) in keep:
            continue
        if path.exists() and path.suffix == ".md":
            path.unlink()
            removed += 1

    # 顺带清理因此变空的类别目录
    for parent in {Path(raw).parent for raw in data.get("files") or []}:
        try:
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:  # pragma: no cover —— 目录删除失败不影响主流程
            pass

    if removed:
        logger.info(f"[kb] 已清理 {removed} 个不再生成的历史文件")
    return removed


# ==================== 摄取（复用既有管道）====================


def ingest_rendered(
    plan: List[Dict[str, Any]],
    collection: Optional[str] = None,
    recreate: bool = False,
    chunk_size: Optional[int] = None,
    overlap: Optional[int] = None,
) -> Dict[str, Any]:
    """
    把渲染产物交给既有摄取管道写入 Qdrant（增量 + 幂等）。

    这里刻意复用 `qa.ingestion.run` 的 `classify_documents` / `ingest_path`，
    而不是重写一遍：内容哈希、增量去重、维度检测、日志都保持一致。
    """
    from qa.ingestion.embedder import create_embedder
    from qa.ingestion.qdrant_store import QdrantStore
    from qa.ingestion.run import classify_documents, compute_doc_id, ingest_path

    store = QdrantStore(collection=collection)
    embedder = create_embedder()

    if recreate:
        logger.warning(f"[kb] --recreate：清空并重建集合 {store.collection_name}")
        store.delete_collection()
        existing: set = set()
    else:
        try:
            existing = store.list_doc_ids()
            logger.info(f"[kb] 集合已有 {len(existing)} 份文档，内容未变的将跳过")
        except Exception as e:
            logger.warning(f"[kb] 读取已有文档列表失败（{e}），本次全量处理")
            existing = set()

    files = [item["path"] for item in plan]
    pending, skipped = classify_documents(files, existing)
    if skipped:
        logger.info(f"[kb] 跳过 {len(skipped)} 个已入库且内容未变的文档")

    total_chunks = total_written = 0
    for path, doc_id in pending:
        result = ingest_path(
            path, store, embedder,
            chunk_size=chunk_size, overlap=overlap, doc_id=doc_id,
        )
        total_chunks += result["chunks"]
        total_written += result["written"]

    return {
        "collection": store.collection_name,
        "pending": len(pending),
        "skipped": len(skipped),
        "chunks": total_chunks,
        "written": total_written,
        "total_points": store.count(),
    }


# ==================== CLI ====================


def _cmd_check(args: argparse.Namespace) -> int:
    """覆盖率自检：默认行为，不写文件、不调 API"""
    records = load_records(args.records)
    expected = load_expected_entities()
    report = coverage_report(records, expected)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not report["missing"] else 1

    print("=" * 64)
    print("渲染层覆盖率自检（离线，不调用任何 API）")
    print("=" * 64)
    print(f"知识记录条数      : {report['records']}")
    print(f"评测集期望实体数  : {report['expected_total']}")
    print(f"渲染产物可覆盖    : {report['covered']}  ({report['coverage']:.1%})")

    if report["missing"]:
        print(f"\n[!] 未被覆盖的期望实体 {len(report['missing'])} 个：")
        for name in report["missing"]:
            print(f"    - {name}")
        print("\n提示：这些实体在语料中不可检索，对应题目的文档检索组将零召回。")
        return 1

    print("\n[OK] 全部期望实体均可被渲染语料覆盖。")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    records = load_records(args.records)
    output_dir = Path(args.output)
    plan = build_render_plan(records, output_dir)

    if args.dry_run:
        print(f"[dry-run] 将渲染 {len(plan)} 个文件到 {output_dir}")
        for item in plan[:10]:
            rel = item["path"].relative_to(output_dir)
            print(f"    {rel}  ({len(item['content'])} 字符)")
        if len(plan) > 10:
            print(f"    ... 其余 {len(plan) - 10} 个")
        return 0

    result = write_render_plan(plan, manifest_path=output_dir / "manifest.json")
    # 顺序关键：先渲染（正确报告 unchanged），再依据"本次产物清单"删除陈旧文件
    clean_stale(output_dir / "manifest.json", result["files"])
    logger.info(
        f"[kb] 渲染完成：新写 {result['written']}，内容未变 {result['unchanged']} "
        f"→ {output_dir}"
    )
    if result["manifest"]:
        logger.info(f"[kb] 清单已写出：{result['manifest']}")

    if args.ingest:
        stats = ingest_rendered(
            plan,
            collection=args.collection,
            recreate=args.recreate,
            chunk_size=args.chunk_size,
            overlap=args.chunk_overlap,
        )
        logger.info(
            f"[kb] 摄取完成 → 集合 {stats['collection']}："
            f"新处理 {stats['pending']}、跳过 {stats['skipped']}、"
            f"写入 {stats['written']} 向量，集合现有 {stats['total_points']} 条"
        )
    else:
        print(f"\n已渲染 {len(plan)} 个文件到 {output_dir}")
        print("下一步（摄取进向量库）：")
        coll = args.collection or config.QDRANT_COLLECTION
        print(f"    python -m qa.ingestion.run --input {output_dir} --collection {coll}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    argp = argparse.ArgumentParser(
        prog="python -m qa.ingestion.kb_renderer",
        description="把结构化知识（data/new_energy.json）渲染成可摄取的 Markdown 语料",
    )
    sub = argp.add_subparsers(dest="command")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--records", type=Path, default=DEFAULT_RECORDS,
                        help=f"知识记录 JSON（默认 {DEFAULT_RECORDS.name}）")

    p_check = sub.add_parser("check", parents=[common],
                             help="覆盖率自检（默认命令；离线，不写文件）")
    p_check.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    p_check.set_defaults(func=_cmd_check)

    p_render = sub.add_parser("render", parents=[common],
                              help="渲染 Markdown 语料到磁盘")
    p_render.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                          help=f"输出目录（默认 {DEFAULT_OUTPUT}）")
    p_render.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    p_render.add_argument("--ingest", action="store_true",
                          help="渲染后直接摄取进 Qdrant（复用既有管道）")
    p_render.add_argument("--collection", default="newenergy_kb",
                          help="目标 Qdrant 集合（默认 newenergy_kb，与 PDF 语料分开）")
    p_render.add_argument("--recreate", action="store_true",
                          help="摄取前清空目标集合（消除孤儿 chunk 用）")
    p_render.add_argument("--chunk-size", type=int, default=None,
                          help=f"切块字符数（默认 {config.CHUNK_SIZE}）")
    p_render.add_argument("--chunk-overlap", type=int, default=None,
                          help=f"相邻块重叠字符数（默认 {config.CHUNK_OVERLAP}）")
    p_render.set_defaults(func=_cmd_render)

    return argp


def main(argv: Optional[List[str]] = None) -> int:
    argp = build_parser()
    args = argp.parse_args(argv)

    # 未给子命令时默认执行 check（最安全的默认行为：什么都不改）
    if not getattr(args, "command", None):
        args = argp.parse_args(["check"] if argv is None else ["check", *argv])

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

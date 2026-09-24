# -*- coding: utf-8 -*-
"""
混合检索命令行演示

用法（需已配置 Neo4j 或已摄取文档到 Qdrant）：
    python -m qa.retrieval.cli --query "磷酸铁锂电池有哪些优点？"
    python -m qa.retrieval.cli --query "全钒液流电池适合什么场景？" --top-k 5
"""

import argparse
import sys

from qa.retrieval.factory import build_default_pipeline


def main(argv=None) -> int:
    argp = argparse.ArgumentParser(description="NewEnergyKG 混合检索演示")
    argp.add_argument("--query", required=True, help="检索问题")
    argp.add_argument("--top-k", type=int, default=5, help="返回条数（默认 5）")
    args = argp.parse_args(argv)

    pipeline = build_default_pipeline()
    if not pipeline.retrievers:
        print("没有可用的检索器，无法演示。请先：")
        print("  1) 配置 Neo4j 连接；或")
        print("  2) python -m qa.ingestion.run --input docs/ 摄取文档")
        return 1

    print(f"\n检索问题：{args.query}")
    print(f"参与检索的来源：{pipeline.list_sources()}\n")

    results = pipeline.retrieve(args.query, top_k=args.top_k)
    if not results:
        print("（没有检索到结果）")
        return 0

    print(f"共返回 {len(results)} 条结果：\n")
    for i, r in enumerate(results, start=1):
        print(f"[{i}] source={r.source} | score={r.score:.4f}")
        if r.section:
            print(f"    章节：{r.section}")
        preview = r.text.replace("\n", " ")[:80]
        print(f"    {preview}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

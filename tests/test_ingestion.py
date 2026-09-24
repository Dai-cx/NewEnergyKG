# -*- coding: utf-8 -*-
"""
D3 文档摄取管道单元测试

覆盖：
- parser：Markdown 标题/段落解析、纯文本解析、文件格式分发
- chunker：标题路径组织、超长段落切分、overlap 衔接、metadata 完整性
- embedder：Debug 向量确定性/归一化、工厂选择逻辑
- qdrant_store：建集合、幂等写入、向量检索、来源过滤（使用 Qdrant 内存模式）
- 端到端：ingest_path 完整链路（解析→切分→向量化→入库→可检索）
"""

import math

import pytest
from qdrant_client import QdrantClient

from qa import config
from qa.ingestion import chunker as chunker_mod
from qa.ingestion import parser as parser_mod
from qa.ingestion.embedder import (
    DashScopeEmbedder,
    DebugHashEmbedder,
    create_embedder,
)
from qa.ingestion.qdrant_store import QdrantStore
from qa.ingestion.run import collect_files, compute_doc_id, ingest_path

# ==================== parser ====================


class TestParser:
    MD_TEXT = (
        "# 光伏\n\n"
        "晶硅电池是当前主流。\n\n"
        "- PERC 电池效率约 24%。\n"
        "- TOPCon 电池效率约 25%。\n\n"
        "## 储能\n\n"
        "液流电池适合长时储能。\n"
    )

    def test_markdown_headings_detected(self):
        blocks = parser_mod.parse_markdown(self.MD_TEXT)
        headings = [b for b in blocks if b.type == "heading"]
        assert [(h.text, h.level) for h in headings] == [("光伏", 1), ("储能", 2)]

    def test_markdown_paragraph_grouping(self):
        blocks = parser_mod.parse_markdown(self.MD_TEXT)
        paragraphs = [b.text for b in blocks if b.type == "paragraph"]
        assert paragraphs[0] == "晶硅电池是当前主流。"
        # 列表行之间无空行 → 合并为一个段落块
        assert "PERC 电池效率约 24%。" in paragraphs[1]
        assert "TOPCon 电池效率约 25%。" in paragraphs[1]

    def test_plain_text_no_headings(self):
        blocks = parser_mod.parse_plain_text("第一段\n第二行\n\n第三段\n")
        assert all(b.type == "paragraph" for b in blocks)
        assert len(blocks) == 2  # 空行分隔成两段

    def test_parse_file_by_suffix(self, tmp_path):
        md_file = tmp_path / "doc.md"
        md_file.write_text("# 标题\n\n正文。\n", encoding="utf-8")
        blocks = parser_mod.parse_file(md_file)
        assert blocks[0].type == "heading"
        assert blocks[0].text == "标题"

    def test_parse_file_unsupported_raises(self, tmp_path):
        bad = tmp_path / "doc.docx"
        bad.write_text("x", encoding="utf-8")
        with pytest.raises(ValueError):
            parser_mod.parse_file(bad)

    def test_count_blocks(self):
        blocks = parser_mod.parse_markdown("# A\n\ntext\n## B\n\ntext2\n")
        stats = parser_mod.count_blocks(blocks)
        assert stats == {"total": 4, "headings": 2, "paragraphs": 2}


# ==================== chunker ====================


class TestChunker:
    def test_sections_follow_headings(self):
        md = "# 储能电池\n\n磷酸铁锂电池安全性高。\n\n三元锂电池能量密度高。\n\n# 光伏\n\n晶硅电池效率高。\n"
        chunks = chunker_mod.chunk_markdown(md, source="a.md", doc_id="d1")
        assert len(chunks) == 2
        assert chunks[0]["metadata"]["section"] == "储能电池"
        assert chunks[1]["metadata"]["section"] == "光伏"
        assert "磷酸铁锂电池" in chunks[0]["text"]

    def test_nested_heading_path(self):
        md = "# 光伏\n\n正文。\n\n## 晶硅电池\n\nPERC 效率高。\n"
        chunks = chunker_mod.chunk_markdown(md, source="a.md", doc_id="d1")
        assert any(c["metadata"]["section"] == "光伏 / 晶硅电池" for c in chunks)

    def test_metadata_complete(self):
        chunks = chunker_mod.chunk_markdown("# A\n\n正文。\n", source="s.md", doc_id="abc")
        meta = chunks[0]["metadata"]
        assert meta["source"] == "s.md"
        assert meta["doc_id"] == "abc"
        assert meta["chunk_index"] == 0
        assert meta["char_count"] == len(chunks[0]["text"])
        assert chunks[0]["text"].strip() != ""

    def test_chunk_index_sequential(self):
        md = "".join(f"# 章节{i}\n\n内容{i}。\n" for i in range(5))
        chunks = chunker_mod.chunk_markdown(md, source="s.md", doc_id="d")
        indices = [c["metadata"]["chunk_index"] for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_overlap_between_consecutive_chunks(self):
        # 5 段各 ~100 字、段间有空行（各自是独立段落），块上限 300：
        # p0+p1 拼满触发切块 → 新块携带旧块尾部 80 字作为 overlap 前缀
        paragraphs = "\n\n".join(f"第{i}段内容" * 20 for i in range(5))
        md = f"# 长文\n\n{paragraphs}"
        chunks = chunker_mod.chunk_markdown(
            md, source="s.md", doc_id="d", chunk_size=300, overlap=80
        )

        assert len(chunks) >= 3
        # 相邻块：新块以旧块尾部 80 字开头（overlap 前缀）
        assert chunks[1]["text"].startswith(chunks[0]["text"][-80:])

    def test_super_long_paragraph_split(self):
        md = "# 超长\n\n" + "超长文本内容" * 120  # ~720 字
        chunks = chunker_mod.chunk_markdown(
            md, source="s.md", doc_id="d", chunk_size=200, overlap=40
        )
        assert len(chunks) >= 3
        assert all(len(c["text"]) <= 200 for c in chunks)

    def test_text_before_first_heading(self):
        md = "开头没有标题的引言。\n\n# 第一章\n\n内容。\n"
        chunks = chunker_mod.chunk_markdown(md, source="s.md", doc_id="d")
        assert chunks[0]["metadata"]["section"] == ""
        assert "引言" in chunks[0]["text"]


# ==================== embedder ====================


class TestEmbedder:
    def test_debug_embedder_deterministic(self):
        emb = DebugHashEmbedder(dimension=16)
        v1 = emb.embed_one("磷酸铁锂电池安全性高")
        v2 = emb.embed_one("磷酸铁锂电池安全性高")
        assert v1 == v2
        assert len(v1) == 16

    def test_debug_embedder_normalized(self):
        emb = DebugHashEmbedder(dimension=32)
        vec = emb.embed_one("钠离子电池成本低")
        norm = math.sqrt(sum(x * x for x in vec))
        assert abs(norm - 1.0) < 1e-6

    def test_debug_embedder_different_texts(self):
        emb = DebugHashEmbedder(dimension=32)
        a = emb.embed_one("光伏逆变器工作原理")
        b = emb.embed_one("天气预报说明天有雨")
        assert a != b

    def test_create_embedder_without_key_returns_debug(self, monkeypatch):
        monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "")
        embedder = create_embedder()
        assert isinstance(embedder, DebugHashEmbedder)

    def test_create_embedder_with_key_returns_dashscope(self, monkeypatch):
        monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "sk-test")
        embedder = create_embedder()
        assert isinstance(embedder, DashScopeEmbedder)
        assert embedder.provider == "dashscope"


# ==================== qdrant_store ====================


@pytest.fixture
def mem_store():
    """内存模式 Qdrant：不依赖外部服务"""
    client = QdrantClient(":memory:")
    store = QdrantStore(collection="test_docs", client=client)
    yield store
    store.close()


def _make_chunks(texts, source="a.md", doc_id="doc1"):
    return [
        {
            "text": t,
            "metadata": {
                "doc_id": doc_id,
                "source": source,
                "section": f"章节{i}",
                "chunk_index": i,
                "char_count": len(t),
            },
        }
        for i, t in enumerate(texts)
    ]


class TestQdrantStore:
    def test_upsert_and_count(self, mem_store):
        emb = DebugHashEmbedder(dimension=8)
        chunks = _make_chunks(["宁德时代是电池龙头", "液流电池适合长时储能"])
        mem_store.ensure_collection(dimension=8)
        vectors = emb.embed([c["text"] for c in chunks])

        assert mem_store.upsert_chunks(chunks, vectors) == 2
        assert mem_store.count() == 2

    def test_upsert_idempotent(self, mem_store):
        emb = DebugHashEmbedder(dimension=8)
        chunks = _make_chunks(["宁德时代是电池龙头"])
        mem_store.ensure_collection(dimension=8)
        vectors = emb.embed([c["text"] for c in chunks])

        mem_store.upsert_chunks(chunks, vectors)
        mem_store.upsert_chunks(chunks, vectors)  # 同 doc_id+chunk_index → 覆盖
        assert mem_store.count() == 1  # 不堆积

    def test_search_returns_most_similar(self, mem_store):
        emb = DebugHashEmbedder(dimension=8)
        texts = ["宁德时代是动力电池龙头", "液流电池适合四小时以上长时储能"]
        chunks = _make_chunks(texts)
        mem_store.ensure_collection(dimension=8)
        mem_store.upsert_chunks(chunks, emb.embed(texts))

        query_vec = emb.embed_one("宁德时代动力电池怎么样")
        hits = mem_store.search(query_vec, top_k=1)
        assert len(hits) == 1
        assert "宁德时代" in hits[0]["payload"]["text"]
        assert hits[0]["score"] > 0

    def test_search_filter_by_source(self, mem_store):
        emb = DebugHashEmbedder(dimension=8)
        all_chunks = (
            _make_chunks(["宁德时代是电池龙头"], source="a.md", doc_id="d1")
            + _make_chunks(["宁德时代布局储能"], source="b.md", doc_id="d2")
        )
        mem_store.ensure_collection(dimension=8)
        mem_store.upsert_chunks(all_chunks, emb.embed([c["text"] for c in all_chunks]))

        query_vec = emb.embed_one("宁德时代")
        hits = mem_store.search(query_vec, top_k=10, source="b.md")
        assert len(hits) == 1
        assert hits[0]["payload"]["source"] == "b.md"

    def test_mismatched_lengths_raise(self, mem_store):
        mem_store.ensure_collection(dimension=8)
        with pytest.raises(ValueError):
            mem_store.upsert_chunks(_make_chunks(["只有一个"]), [[1.0] * 8, [2.0] * 8])

    def test_delete_collection(self, mem_store):
        mem_store.ensure_collection(dimension=8)
        assert mem_store.collection_exists()
        mem_store.delete_collection()
        assert not mem_store.collection_exists()


class TestIncremental:
    """增量摄取：只处理内容未入库过的新文档"""

    def test_list_doc_ids(self, mem_store):
        emb = DebugHashEmbedder(dimension=8)
        chunks = (
            _make_chunks(["宁德时代是电池龙头"], source="a.md", doc_id="doc-a")
            + _make_chunks(["液流电池适合长时储能"], source="b.md", doc_id="doc-b")
        )
        mem_store.ensure_collection(dimension=8)
        mem_store.upsert_chunks(chunks, emb.embed([c["text"] for c in chunks]))

        doc_ids = mem_store.list_doc_ids()
        assert doc_ids == {"doc-a", "doc-b"}

    def test_list_doc_ids_empty_when_no_collection(self, mem_store):
        assert mem_store.list_doc_ids() == set()

    def test_classify_documents_by_content_hash(self, tmp_path):
        from qa.ingestion.run import classify_documents, compute_doc_id

        existing = tmp_path / "已入库.md"
        existing.write_text("相同内容A", encoding="utf-8")
        new_file = tmp_path / "新文档.md"
        new_file.write_text("全新内容B", encoding="utf-8")

        existing_id = compute_doc_id(existing)
        pending, skipped = classify_documents([existing, new_file], {existing_id})

        assert [p.name for p, _ in skipped] == ["已入库.md"]
        assert [p.name for p, _ in pending] == ["新文档.md"]

    def test_classify_changed_content_is_new(self, tmp_path):
        from qa.ingestion.run import classify_documents, compute_doc_id

        f = tmp_path / "a.md"
        f.write_text("v1 内容", encoding="utf-8")
        old_id = compute_doc_id(f)

        f.write_text("v2 内容已更新", encoding="utf-8")  # 内容变了
        pending, skipped = classify_documents([f], {old_id})
        assert len(pending) == 1  # 修改过的文件会被当作新文档重新摄取
        assert len(skipped) == 0


# ==================== 端到端：ingest_path ====================


class TestIngestPipeline:
    @pytest.fixture
    def sample_md(self, tmp_path):
        md = (
            "# 储能技术\n\n"
            "锂离子电池储能响应快、能量密度高，代表企业宁德时代。\n\n"
            "## 液流电池\n\n"
            "全钒液流电池循环寿命上万次，适合长时储能。\n"
        )
        p = tmp_path / "储能笔记.md"
        p.write_text(md, encoding="utf-8")
        return p

    def test_ingest_path_writes_and_searches(self, sample_md, mem_store):
        embedder = DebugHashEmbedder(dimension=8)
        result = ingest_path(sample_md, mem_store, embedder)

        assert result["written"] == result["chunks"]
        assert result["chunks"] >= 1
        assert mem_store.count() == result["chunks"]

        # 检索验证：用文档里的句子能召回对应 chunk
        query_vec = embedder.embed_one("全钒液流电池循环寿命长，适合作长时储能")
        hits = mem_store.search(query_vec, top_k=1)
        assert "全钒液流电池" in hits[0]["payload"]["text"]

    def test_ingest_path_idempotent(self, sample_md, mem_store):
        embedder = DebugHashEmbedder(dimension=8)
        ingest_path(sample_md, mem_store, embedder)
        count_after_first = mem_store.count()
        ingest_path(sample_md, mem_store, embedder)
        assert mem_store.count() == count_after_first  # 重复导入不堆积

    def test_doc_id_stable_but_content_sensitive(self, sample_md, tmp_path):
        doc_id1 = compute_doc_id(sample_md)
        other = tmp_path / "other.md"
        other.write_text("完全不同内容", encoding="utf-8")
        doc_id2 = compute_doc_id(other)

        assert doc_id1 != doc_id2
        # 复制同一文件 → 相同 doc_id
        dup = tmp_path / "dup.md"
        dup.write_text(sample_md.read_text(encoding="utf-8"), encoding="utf-8")
        assert compute_doc_id(dup) == doc_id1

    def test_collect_files_directory(self, tmp_path):
        (tmp_path / "a.md").write_text("# a", encoding="utf-8")
        (tmp_path / "b.txt").write_text("b", encoding="utf-8")
        (tmp_path / "c.pdf").write_text("x", encoding="utf-8")  # 后缀被收集，解析时才报错
        (tmp_path / "skip.py").write_text("print(1)", encoding="utf-8")

        files = collect_files(tmp_path)
        names = {p.name for p in files}
        assert names == {"a.md", "b.txt", "c.pdf"}

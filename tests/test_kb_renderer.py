# -*- coding: utf-8 -*-
"""
渲染层（qa/ingestion/kb_renderer.py）单元测试

全部离线：不连 Qdrant、不调 DashScope、不写真实产物目录。
覆盖点：
- 字段格式化与空值/占位值省略（避免产出无信息量的 chunk）
- Markdown 结构（`#` 技术名 + 5 个 `##` 小节 → chunker 的 section 路径可用）
- 一条技术一个文件（可增量重摄的前提）与文件名/目录名净化
- 渲染计划、落盘幂等、清单与历史生成物清理
- 覆盖率自检：真实数据必须 100% 覆盖评测集期望实体
"""

import json

import pytest

from qa.ingestion import kb_renderer as kb


# ==================== 测试用记录 ====================

def make_record(**overrides):
    """一份最小但字段齐全的记录（形态与 data/new_energy.json 一致）"""
    record = {
        "name": "测试电池",
        "desc": "一种用于测试的电池。",
        "principle": "通过锂离子嵌入脱嵌工作。",
        "advantage": ["安全性高", "成本低"],
        "disadvantage": ["能量密度低"],
        "efficiency": "约 95%",
        "cost_level": "中低",
        "development_stage": "商业化",
        "market_share": "约 68%",
        "maturity": "成熟",
        "category": "锂离子电池",
        "companies": ["宁德时代"],
        "materials": ["磷酸铁锂"],
        "equipments": ["涂布机"],
        "applications": ["电动汽车"],
        "indicators": ["能量密度"],
        "policies": ["产业发展规划"],
        "compete_technologies": ["三元锂电池"],
    }
    record.update(overrides)
    return record


# ==================== 值归一化与格式化 ====================


class TestNormalizeList:
    def test_none_becomes_empty(self):
        assert kb.normalize_list(None) == []

    def test_string_wrapped_into_list(self):
        assert kb.normalize_list("磷酸铁锂") == ["磷酸铁锂"]

    def test_list_is_stripped_and_deduped(self):
        assert kb.normalize_list(["  甲 ", "乙", "甲", ""]) == ["甲", "乙"]

    def test_placeholder_items_dropped(self):
        assert kb.normalize_list(["甲", "未知", "暂无", "-"]) == ["甲"]

    def test_non_string_items_are_stringified(self):
        assert kb.normalize_list([2024, "甲"]) == ["2024", "甲"]


class TestFormatField:
    def test_list_field_renders_one_line_per_item(self):
        lines = kb.format_field("advantage", ["安全性高", "成本低"])
        assert lines == ["- 优点：安全性高", "- 优点：成本低"]

    def test_scalar_field_renders_with_label(self):
        assert kb.format_field("maturity", "成熟") == ["- 成熟度：成熟"]

    def test_empty_values_render_nothing(self):
        for value in (None, "", "   ", [], ["未知"], "暂无", "N/A"):
            assert kb.format_field("desc", value) == [], f"值 {value!r} 不应渲染"

    def test_unknown_field_falls_back_to_field_name(self):
        assert kb.format_field("custom_field", "值") == ["- custom_field：值"]


# ==================== 小节渲染 ====================


class TestRenderSections:
    def test_all_five_sections_present_for_full_record(self):
        sections = kb.render_sections(make_record())
        names = [name for name, _ in sections]
        assert names == [h for h, _ in kb.SECTION_FIELDS]

    def test_empty_sections_are_skipped(self):
        record = make_record(
            advantage=[], disadvantage=[], efficiency=None, maturity="", development_stage=None,
        )
        names = [name for name, _ in kb.render_sections(record)]
        assert "优缺点与效率" not in names
        assert "概述与原理" in names

    def test_record_with_only_name_yields_no_sections(self):
        assert kb.render_sections({"name": "只有名字"}) == []

    def test_every_field_is_mapped_into_exactly_one_section(self):
        """防止新增字段时漏配置分组（漏了就等于知识丢失）"""
        mapped = [f for _, fields in kb.SECTION_FIELDS for f in fields]
        assert len(mapped) == len(set(mapped)), "字段被重复分组"
        for field in kb.FIELD_LABELS:
            assert field in mapped, f"字段 {field} 未归入任何小节"


# ==================== 文档渲染 ====================


class TestRenderRecordMarkdown:
    def test_structure_and_headings(self):
        md = kb.render_record_markdown(make_record())
        lines = md.splitlines()
        assert lines[0] == kb.GENERATED_MARKER
        assert "# 测试电池" in lines
        # 五个二级标题（chunker 依赖它们生成 section 路径）
        assert lines.count("## 概述与原理") == 1
        assert sum(1 for ln in lines if ln.startswith("## ")) == 5

    def test_title_is_exact_tech_name_for_entity_matching(self):
        """技术名必须原样出现在标题里，否则实体匹配与引用溯源会失效"""
        md = kb.render_record_markdown(make_record(name="TOPCon电池"))
        assert md.splitlines()[2] == "# TOPCon电池"

    def test_missing_name_raises(self):
        with pytest.raises(ValueError):
            kb.render_record_markdown({"desc": "没有名字"})

    def test_rendering_is_deterministic(self):
        record = make_record()
        assert kb.render_record_markdown(record) == kb.render_record_markdown(record)

    def test_ends_with_single_newline(self):
        md = kb.render_record_markdown(make_record())
        assert md.endswith("\n") and not md.endswith("\n\n")


# ==================== 文件名与目录 ====================


class TestNaming:
    @pytest.mark.parametrize("raw,expected", [
        ("磷酸铁锂电池", "磷酸铁锂电池"),
        ('非法/字符\\测试:*?"<>|', "非法_字符_测试"),
        ("  前后空白  ", "前后空白"),
        ("", "unnamed"),
    ])
    def test_sanitize_filename(self, raw, expected):
        assert kb.sanitize_filename(raw) == expected

    def test_category_dir_uses_category(self):
        assert kb.category_dir(make_record(category="晶硅电池")) == "晶硅电池"

    @pytest.mark.parametrize("bad", [None, "", "未知", "暂无"])
    def test_missing_category_falls_back(self, bad):
        assert kb.category_dir(make_record(category=bad)) == "未分类"


# ==================== 渲染计划与落盘 ====================


class TestRenderPlanAndWrite:
    def test_plan_creates_one_file_per_record(self, tmp_path):
        plan = kb.build_render_plan([make_record(), make_record(name="另一个")], tmp_path)
        assert len(plan) == 2
        assert len({item["path"] for item in plan}) == 2, "路径必须互不冲突"

    def test_plan_path_layout(self, tmp_path):
        plan = kb.build_render_plan([make_record()], tmp_path)
        rel = plan[0]["path"].relative_to(tmp_path)
        assert rel.parts == ("锂离子电池", "测试电池.md")

    def test_write_creates_files_as_lf(self, tmp_path):
        plan = kb.build_render_plan([make_record()], tmp_path)
        kb.write_render_plan(plan)
        written = plan[0]["path"]
        assert written.exists()
        # 必须是 LF：文件字节稳定，compute_doc_id 的哈希才稳定
        assert b"\r\n" not in written.read_bytes()

    def test_rewrite_is_idempotent(self, tmp_path):
        plan = kb.build_render_plan([make_record()], tmp_path)
        first = kb.write_render_plan(plan)
        second = kb.write_render_plan(plan)
        assert first["written"] == 1
        assert second["written"] == 0 and second["unchanged"] == 1

    def test_manifest_written_and_lists_files(self, tmp_path):
        plan = kb.build_render_plan([make_record()], tmp_path)
        result = kb.write_render_plan(plan, manifest_path=tmp_path / "manifest.json")
        data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert data["record_count"] == 1
        assert str(plan[0]["path"]) in data["files"]
        assert result["manifest"].endswith("manifest.json")

    def test_clean_stale_removes_only_files_not_regenerated(self, tmp_path):
        """技术被删除后其 md 不应残留，但本次仍生成的文件绝不能动"""
        stale = tmp_path / "锂离子电池" / "已删除技术.md"
        kept = tmp_path / "锂离子电池" / "仍存在技术.md"
        stale.parent.mkdir(parents=True)
        stale.write_text("旧内容", encoding="utf-8")
        kept.write_text("内容", encoding="utf-8")
        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps({"files": [str(stale), str(kept)]}, ensure_ascii=False),
            encoding="utf-8",
        )

        removed = kb.clean_stale(manifest, keep_paths=[str(kept)])

        assert removed == 1
        assert not stale.exists(), "陈旧文件应被删除"
        assert kept.exists(), "本次仍生成的文件不能被删"

    def test_clean_stale_without_manifest_is_noop(self, tmp_path):
        assert kb.clean_stale(tmp_path / "missing.json", keep_paths=[]) == 0

    def test_repeated_render_is_incremental(self, tmp_path):
        """
        回归：早期实现在渲染前无条件删除历史产物，导致每轮都全量重写
        （内容虽一致，但会白跑一次全量 embedding）。第二次渲染必须报告"内容未变"。
        """
        out = tmp_path / "kb"
        assert kb.main(["render", "--output", str(out)]) == 0
        first = len(list(out.rglob("*.md")))

        # 第二次：捕获日志中的 written/unchanged 统计
        from loguru import logger as _logger

        messages = []
        sink_id = _logger.add(lambda m: messages.append(m), level="INFO")
        try:
            assert kb.main(["render", "--output", str(out)]) == 0
        finally:
            _logger.remove(sink_id)

        stats = "".join(str(m) for m in messages)
        assert "新写 0" in stats, f"第二次渲染不应重写任何文件：{stats}"
        assert f"内容未变 {first}" in stats, f"应全部报告为内容未变：{stats}"


# ==================== 覆盖率自检 ====================


class TestCoverageReport:
    def test_counts_and_missing(self):
        records = [make_record(name="甲电池"), make_record(name="乙电池", desc="乙的描述")]
        report = kb.coverage_report(records, ["甲电池", "乙电池", "丙电池"])
        assert report["expected_total"] == 3
        assert report["covered"] == 2
        assert report["coverage"] == pytest.approx(2 / 3)
        assert report["missing"] == ["丙电池"]

    def test_full_coverage(self):
        report = kb.coverage_report([make_record()], ["测试电池"])
        assert report["missing"] == []
        assert report["coverage"] == 1.0

    def test_no_expected_entities_is_full_coverage(self):
        assert kb.coverage_report([make_record()], [])["coverage"] == 1.0

    def test_empty_list_values_do_not_fake_coverage(self):
        """实体只出现在被清空的字段里时，不应算覆盖"""
        records = [make_record(name="甲电池", materials=["目标材料"])]
        assert kb.coverage_report(records, ["目标材料"])["covered"] == 1
        records_cleared = [make_record(name="甲电池", materials=[])]
        assert kb.coverage_report(records_cleared, ["目标材料"])["covered"] == 0


# ==================== 真实数据回归（渲染层的核心验收指标）====================


class TestRealData:
    """用仓库真实数据跑：这是本项目最关键的语料质量断言"""

    def test_real_records_load(self):
        records = kb.load_records()
        assert len(records) == 63
        assert all(r.get("name") for r in records)

    def test_every_real_record_renders_all_five_sections(self):
        """真实记录字段齐全，必须都能产出 5 个小节"""
        for record in kb.load_records():
            sections = kb.render_sections(record)
            assert len(sections) == 5, f"{record['name']} 只有 {len(sections)} 个小节"

    def test_expected_entities_fully_covered(self):
        """
        回归断言：渲染语料必须 100% 覆盖评测集的期望实体。

        之前只用 PDF 当语料时覆盖率仅 36%，导致检索消融无法可信归因；
        这条断言把"语料覆盖率"锁成受测指标，防止以后退化。
        """
        records = kb.load_records()
        expected = kb.load_expected_entities()
        assert expected, "应能从评测集读到期望实体"
        report = kb.coverage_report(records, expected)
        assert report["missing"] == [], f"未被覆盖：{report['missing']}"
        assert report["coverage"] == 1.0

    def test_real_plan_has_unique_paths(self, tmp_path):
        plan = kb.build_render_plan(kb.load_records(), tmp_path)
        assert len(plan) == 63
        assert len({item["path"] for item in plan}) == 63


# ==================== CLI 入口 ====================


class TestCli:
    def test_default_command_is_check(self, capsys):
        """不带任何参数时必须是最安全的只读行为"""
        rc = kb.main([])
        out = capsys.readouterr().out
        assert "覆盖率自检" in out
        assert rc == 0

    def test_check_json_output(self, capsys):
        rc = kb.main(["check", "--json"])
        payload = json.loads(capsys.readouterr().out)
        # 报告分两块：评测期望实体覆盖 + 领域词汇（技术名）覆盖
        assert payload["expected_entities"]["covered"] == \
            payload["expected_entities"]["expected_total"]
        assert payload["domain_vocabulary"]["covered"] == \
            payload["domain_vocabulary"]["expected_total"]
        assert rc == 0

    def test_render_dry_run_writes_nothing(self, tmp_path, capsys):
        rc = kb.main(["render", "--output", str(tmp_path), "--dry-run"])
        assert rc == 0
        assert "[dry-run]" in capsys.readouterr().out
        assert not list(tmp_path.rglob("*.md"))

    def test_render_writes_expected_files(self, tmp_path):
        rc = kb.main(["render", "--output", str(tmp_path)])
        assert rc == 0
        assert len(list(tmp_path.rglob("*.md"))) == 63
        assert (tmp_path / "manifest.json").exists()

    def test_check_fails_when_entity_uncovered(self, tmp_path, capsys):
        """期望实体覆盖不全时必须以非 0 退出，便于 CI 卡住"""
        records = tmp_path / "records.json"
        records.write_text(json.dumps([make_record(name="甲电池")], ensure_ascii=False),
                           encoding="utf-8")
        rc = kb.main(["check", "--records", str(records), "--json"])
        assert rc == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["expected_entities"]["missing"], "应报告未覆盖的实体"

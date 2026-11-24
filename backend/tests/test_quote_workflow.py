"""多页报价批量解析与字段映射测试：覆盖部分失败、零价、同名跨页和重复确认。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.services.field_mapper import FieldMapperService
from app.services.parser_rule import ParserRuleService
from app.services.quote_ledger import QuoteLedger, normalize_quote_record
from app.services.quote_ledger import _as_price as ledger_price
from app.services.source_pages import DEMO_SOURCE_PAGES, parse_price, parse_source_page


PAGE_4 = DEMO_SOURCE_PAGES[0]["html"]
PAGE_7 = DEMO_SOURCE_PAGES[3]["html"]
PAGE_7_FIXED = """<ul class="quote-list">
  <li class="quote-item" data-position="1"><span class="product-name">机械键盘</span><span class="price">329</span></li>
  <li class="quote-item" data-position="2"><span class="product-name">便携固态硬盘</span><span class="price">459</span></li>
  <li class="quote-item" data-position="3"><span class="product-name">USB集线器</span><span class="price">18</span></li>
</ul>"""


@pytest.fixture(autouse=True)
def clean_ledger():
    from app.services import quote_ledger as ledger_module

    ledger_module.quote_ledger.reset()
    yield
    ledger_module.quote_ledger.reset()


def test_zero_price_is_available_and_missing_price_is_pending():
    zero, zero_reason = parse_price("0.00")
    assert zero == 0
    assert zero_reason == ""

    missing, missing_reason = parse_price("")
    assert missing is None
    assert missing_reason

    broken, broken_reason = parse_price("--")
    assert broken is None
    assert broken_reason


def test_parse_page_keeps_position_and_order():
    records = parse_source_page(7, PAGE_7)
    assert [row["页内位置"] for row in records] == [1, 2, 3]
    assert [row["商品"] for row in records] == ["机械键盘", "便携固态硬盘", "USB集线器"]
    assert records[2]["提取状态"] == "失败"
    assert records[2]["报价"] is None
    assert records[0]["报价"] == 329


def test_batch_parse_partial_failure_keeps_completed_page():
    svc = ParserRuleService()
    result = svc.domain_parse_source_pages([4, 7])

    assert result["status"] == "部分成功"
    assert result["partial_failure"] is True
    pages = {page["page"]: page for page in result["pages"]}
    assert pages[4]["status"] == "已解析"
    assert pages[4]["pending_count"] == 0
    assert pages[7]["status"] == "部分成功"
    assert pages[7]["pending_positions"] == [3]
    assert result["counts"]["record_count"] == 6
    assert result["counts"]["available_count"] == 5
    assert result["pending_positions"] == [
        {"page": 7, "来源页": 7, "positions": [3], "reasons": [
            {"position": 3, "reason": "价格栏损坏或为占位符"}]}
    ]


def test_mapping_skips_failed_positions_and_keeps_original_location():
    parse = ParserRuleService()
    mapper = FieldMapperService()
    result = parse.domain_parse_source_pages([4, 7])

    outcome = mapper.domain_map_source_records(
        result["records"],
        [{"source": "商品", "target": "标准商品名"},
         {"source": "报价", "target": "标准报价", "transform": "int"}],
    )
    assert outcome["counts"] == {
        "accepted": 5, "skipped": 0, "rejected": 0, "pending": 1}
    locations = {(row["来源页"], row["页内位置"]) for row in outcome["accepted"]}
    assert locations == {(4, 1), (4, 2), (4, 3), (7, 1), (7, 2)}
    assert outcome["accepted"][0]["映射字段"]["标准报价"] == 299
    # 失败记录不会因为默认值被伪造成 0
    assert outcome["pending"][0]["报价"] is None
    assert outcome["pending"][0]["页内位置"] == 3


def test_same_name_on_two_pages_keeps_both_quotes():
    from app.services.quote_ledger import quote_ledger

    parse = ParserRuleService()
    mapper = FieldMapperService()
    result = parse.domain_parse_source_pages([4, 7, 8])
    mapper.domain_map_source_records(result["records"])

    results = quote_ledger.results()
    keyboards = [row for row in results["available"] if row["商品"] == "机械键盘"]
    assert {(row["来源页"], row["报价"]) for row in keyboards} == {(4, 299), (7, 329), (8, 309)}
    assert len({row["来源位置"] for row in keyboards}) == 3


def test_recipient_sees_available_quotes_and_pending_positions_before_repair():
    from app.services.quote_ledger import quote_ledger

    parse = ParserRuleService()
    mapper = FieldMapperService()
    result = parse.domain_parse_source_pages([4, 7])
    mapper.domain_map_source_records(result["records"])

    view = quote_ledger.results()
    assert view["counts"]["available"] == 5
    assert view["counts"]["pending"] == 1
    page_4 = next(item for item in view["pages"] if item["page"] == 4)
    page_7 = next(item for item in view["pages"] if item["page"] == 7)
    assert page_4["complete"] is True
    assert page_4["pending_positions"] == []
    assert page_7["complete"] is False
    assert page_7["pending_positions"] == [3]
    assert view["pending_pages"] == [{
        "page": 7,
        "来源页": 7,
        "positions": [3],
        "reasons": [{
            "position": 3,
            "商品": "USB集线器",
            "reason": "价格栏损坏或为占位符",
            "原始价格": "--",
        }],
    }]
    assert {row["来源位置"] for row in view["available"]} == {
        "第4页#1", "第4页#2", "第4页#3", "第7页#1", "第7页#2"}


def test_repair_then_resubmit_twice_confirms_missing_quote_once():
    from app.services.quote_ledger import quote_ledger

    parse = ParserRuleService()
    mapper = FieldMapperService()
    first = parse.domain_parse_source_pages([4, 7])
    mapper.domain_map_source_records(first["records"])

    fixed = parse.domain_parse_source_pages([
        {"page": 4, "html": PAGE_4},
        {"page": 7, "html": PAGE_7_FIXED},
    ])
    assert fixed["status"] == "已解析"
    assert fixed["counts"]["pending_count"] == 0

    first_resubmit = mapper.domain_map_source_records(fixed["records"])
    assert first_resubmit["counts"]["accepted"] == 1
    assert first_resubmit["counts"]["skipped"] == 5

    second_resubmit = mapper.domain_map_source_records(fixed["records"])
    assert second_resubmit["counts"]["accepted"] == 0
    assert second_resubmit["counts"]["skipped"] == 6

    view = quote_ledger.results()
    assert view["counts"]["available"] == 6
    assert view["counts"]["pending"] == 0
    assert view["pending_pages"] == []
    page_7 = next(item for item in view["pages"] if item["page"] == 7)
    assert page_7["complete"] is True
    assert sorted(
        (row["页内位置"], row["报价"])
        for row in view["available"] if row["来源页"] == 7
    ) == [(1, 329), (2, 459), (3, 18)]


def test_same_position_cannot_be_confirmed_with_another_quote():
    mapper = FieldMapperService()
    first = mapper.domain_map_source_records([
        {"来源页": 4, "页内位置": 1, "商品": "机械键盘", "报价": 299},
    ])
    assert first["counts"]["accepted"] == 1

    conflict = mapper.domain_map_source_records([
        {"来源页": 4, "页内位置": 1, "商品": "机械键盘", "报价": 399},
    ])
    assert conflict["counts"]["accepted"] == 0
    assert conflict["counts"]["rejected"] == 1


def test_zero_price_confirmed_but_flagged():
    mapper = FieldMapperService()
    outcome = mapper.domain_map_source_records([
        {"来源页": 5, "页内位置": 2, "商品": "桌面支架", "报价": 0},
    ])
    assert outcome["counts"]["accepted"] == 1
    assert outcome["accepted"][0]["零价"] is True
    assert outcome["accepted"][0]["报价"] == 0

    missing = mapper.domain_map_source_records([
        {"来源页": 5, "页内位置": 3, "商品": "收纳包", "报价": None},
    ])
    assert missing["counts"]["accepted"] == 0
    assert missing["counts"]["pending"] == 1


def test_structured_items_preserve_source_page_position():
    parse = ParserRuleService()
    result = parse.domain_parse_source_pages([
        {"page": 11, "items": [
            {"name": "机械键盘", "price": "399"},
            {"position": 5, "name": "机械键盘", "price": ""},
        ]},
    ])
    assert result["records"][0]["来源页"] == 11
    assert result["records"][0]["页内位置"] == 1
    assert result["records"][1]["页内位置"] == 5
    assert result["records"][1]["提取状态"] == "失败"


def test_independent_ledgers_do_not_leak():
    left = QuoteLedger()
    right = QuoteLedger()
    mapper = FieldMapperService()
    # 服务层用的是进程内公共台账，这里直接验证台账本身可以独立重置/隔离。
    left.confirm_records([{"来源页": 1, "页内位置": 1, "商品": "A", "报价": 1}])
    assert left.results()["counts"]["available"] == 1
    assert right.results()["counts"]["available"] == 0
    assert mapper is not None


def test_normalize_record_uses_shared_keys():
    record = normalize_quote_record({"page": "7", "position": "3", "name": "X", "price": "--"})
    assert record["来源页"] == 7
    assert record["页内位置"] == 3
    assert record["商品"] == "X"
    assert record["提取状态"] == "失败"
    assert ledger_price("18") == 18


def test_partial_repair_only_touches_pending_position():
    from app.services.quote_ledger import quote_ledger

    parse = ParserRuleService()
    mapper = FieldMapperService()
    first = parse.domain_parse_source_pages([4, 7])
    mapper.domain_map_source_records(first["records"])

    # 只重送修复后的第 7 页，第 4 页不参与本次解析也不能丢。
    repaired = parse.domain_parse_source_pages([{"page": 7, "html": PAGE_7_FIXED}])
    assert repaired["status"] == "已解析"
    outcome = mapper.domain_map_source_records(repaired["records"])
    assert outcome["counts"]["accepted"] == 1
    assert outcome["counts"]["skipped"] == 2

    view = quote_ledger.results()
    assert view["counts"]["available"] == 6
    assert view["counts"]["pending"] == 0
    page_4 = next(item for item in view["pages"] if item["page"] == 4)
    assert page_4["available_count"] == 3
    assert page_4["complete"] is True


def test_reparse_same_page_twice_does_not_duplicate_rows():
    from app.services.quote_ledger import quote_ledger

    parse = ParserRuleService()
    mapper = FieldMapperService()
    for _ in range(2):
        result = parse.domain_parse_source_pages([{"page": 7, "html": PAGE_7_FIXED}])
        mapper.domain_map_source_records(result["records"])
    view = quote_ledger.results()
    assert view["counts"]["available"] == 3
    assert [row["页内位置"] for row in view["available"]] == [1, 2, 3]


def test_item_without_price_slot_is_pending_not_dropped():
    records = parse_source_page(
        12,
        """<div class="item"><span class="title">Gamma</span></div>
<div class="item"><span class="title">Delta</span><span class="price">56</span></div>""",
    )
    assert [row["页内位置"] for row in records] == [1, 2]
    assert records[0]["商品"] == "Gamma"
    assert records[0]["提取状态"] == "失败"
    assert records[0]["失败原因"] == "价格栏缺失"
    assert records[1]["报价"] == 56


def test_failure_reasons_are_distinct():
    empty = parse_source_page(
        13, '<ul><li class="item"><span class="title">A</span><span class="price"></span></li></ul>')
    placeholder = parse_source_page(
        13, '<ul><li class="item"><span class="title">B</span><span class="price">--</span></li></ul>')
    zero = parse_source_page(
        13, '<ul><li class="item"><span class="title">C</span><span class="price">0</span></li></ul>')
    assert empty[0]["失败原因"] == "价格栏为空"
    assert placeholder[0]["失败原因"] == "价格栏损坏或为占位符"
    assert zero[0]["报价"] == 0
    assert zero[0]["零价"] is True
    assert zero[0]["提取状态"] == "成功"


def test_mapping_keeps_source_page_for_every_record():
    from app.services.quote_ledger import quote_ledger

    parse = ParserRuleService()
    mapper = FieldMapperService()
    result = parse.domain_parse_source_pages([4, 7, 8])
    outcome = mapper.domain_map_source_records(result["records"])
    assert len(outcome["accepted"]) == 7
    assert outcome["counts"]["pending"] == 1
    for row in quote_ledger.confirmed_rows():
        assert row["来源页"] in {4, 7, 8}
        assert row["页内位置"] >= 1
        assert row["来源位置"] == f"第{row['来源页']}页#{row['页内位置']}"


def test_dry_run_does_not_write_ledger():
    from app.services.quote_ledger import quote_ledger

    mapper = FieldMapperService()
    outcome = mapper.domain_map_source_records(
        [{"来源页": 4, "页内位置": 9, "商品": "试算", "报价": 1}], dry_run=True)
    assert outcome["dry_run"] is True
    assert outcome["counts"]["mappable"] == 1
    assert quote_ledger.results()["counts"]["available"] == 0

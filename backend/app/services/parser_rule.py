"""解析规则：把选中的多页来源 HTML 批量解析成报价记录。

原有 ``domain_parse_html`` 保留为单页文本预览；批量解析入口在
``domain_parse_source_pages``，失败价格和零价在这里就已经分开：

* ``提取状态=成功`` 且 ``报价=0``：页面确实标了 0 元，是可用报价；
* ``提取状态=失败``：价格栏缺失/损坏，记录仍保留页内位置，进入待补清单。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.quote_ledger import quote_ledger
from app.services.source_pages import (
    DEMO_SOURCE_PAGES,
    normalize_source_pages,
    parse_source_page,
)


class ParserRuleService(BaseModuleService):
    MODULE = "parser_rule"
    ENTITY = "解析规则"
    OPERATION_TERM = "解析"
    LIST_FIELDS = ["规则标识", "目标页面", "字段选择器", "数据类型", "默认取值", "正则过滤", "规则版本", "生效批次"]
    REQUIRED_FIELDS = ["规则标识", "目标页面", "字段选择器"]
    STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "启用规则": "已启用", "停用规则": "已停用"}
    NEGATIVE_ACTIONS = ["停用规则"]

    def domain_parse_html(self, html_content: str, rule_id: int = 0) -> dict:
        """使用规则解析HTML内容提取字段。"""
        from html.parser import HTMLParser

        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.texts: list[str] = []
                self.current_tag = ""

            def handle_starttag(self, tag, attrs):
                self.current_tag = tag

            def handle_data(self, data):
                stripped = data.strip()
                if stripped:
                    self.texts.append(stripped)

        extractor = TextExtractor()
        extractor.feed(html_content)        # 返回采集处理结果，含查询校验和回填批次信息

        return {
            "rule_id": rule_id,
            "field_count": len(extractor.texts),
            "extracted_fields": extractor.texts[:20],
            "html_length": len(html_content),
        }

    # ------------------------------------------------------------------
    # 批量解析
    # ------------------------------------------------------------------

    def domain_parse_source_pages(
        self,
        source_pages: Any,
        *,
        selectors: dict[str, str] | None = None,
        rule_id: int = 0,
    ) -> dict[str, Any]:
        """一次提交多页来源 HTML，逐页逐条产出报价记录。

        返回结构里每页都带状态和待补位置；整体状态允许「部分成功」，
        已完整解析的页面不会因为别的页面损坏而被丢弃。
        """
        pages = _resolve_source_pages(source_pages)
        parsed_pages: list[dict[str, Any]] = []
        all_records: list[dict[str, Any]] = []

        for spec in pages:
            records = parse_source_page(
                int(spec["page"]),
                html=spec.get("html") or "",
                items=spec.get("items"),
                selectors=selectors or {},
            )
            for record in records:
                record["来源页标题"] = spec.get("title") or f"第{spec['page']}页"
                record["rule_id"] = rule_id
            all_records.extend(records)

            page_status, page_reason = _page_status(records, bool(spec.get("html") or spec.get("items")))
            pending_positions = [
                int(record["页内位置"])
                for record in records
                if record["提取状态"] == "失败"
            ]
            parsed_pages.append({
                "page": int(spec["page"]),
                "page_number": int(spec["page"]),
                "title": spec.get("title") or f"第{spec['page']}页",
                "html": spec.get("html") or "",
                "status": page_status,
                "page_status": page_status,
                "reason": page_reason,
                "count": len(records),
                "available_count": len(records) - len(pending_positions),
                "pending_count": len(pending_positions),
                "pending_positions": pending_positions,
                "zero_price_count": sum(1 for record in records if record["零价"]),
                "records": records,
            })

        all_records.sort(key=lambda row: (int(row["来源页"]), int(row["页内位置"])))
        page_statuses = [page["status"] for page in parsed_pages]
        if not parsed_pages:
            overall = "解析失败"
        elif all(status == "已解析" for status in page_statuses):
            overall = "已解析"
        elif any(status != "解析失败" for status in page_statuses):
            overall = "部分成功"
        else:
            overall = "解析失败"

        pending = [record for record in all_records if record["提取状态"] == "失败"]
        result = {
            "rule_id": rule_id,
            "status": overall,
            "partial_failure": any(status != "已解析" for status in page_statuses),
            "pages": parsed_pages,
            "records": all_records,
            "counts": {
                "page_count": len(parsed_pages),
                "record_count": len(all_records),
                "available_count": len(all_records) - len(pending),
                "pending_count": len(pending),
                "zero_price_count": sum(1 for record in all_records if record["零价"]),
                "failed_page_count": sum(
                    1 for status in page_statuses if status != "已解析"),
            },
            "pending_positions": _group_pending(pending),
        }
        # 保存最近一次批量解析快照：接收方据此查看当前可用报价和待补位置。
        return quote_ledger.save_parse(result)

    # 入口别名：调用方用不同动词描述同一件事时都能落到同一实现。
    def domain_parse_pages(self, source_pages: Any, **kwargs: Any) -> dict[str, Any]:
        return self.domain_parse_source_pages(source_pages, **kwargs)

    def domain_batch_parse_source_pages(
        self, source_pages: Any, **kwargs: Any,
    ) -> dict[str, Any]:
        return self.domain_parse_source_pages(source_pages, **kwargs)

    def domain_batch_parse(self, source_pages: Any, **kwargs: Any) -> dict[str, Any]:
        return self.domain_parse_source_pages(source_pages, **kwargs)

    def latest_parse_result(self) -> dict[str, Any] | None:
        return quote_ledger.latest_parse()


def _demo_page(page: int) -> dict[str, Any] | None:
    for item in DEMO_SOURCE_PAGES:
        if int(item["page"]) == int(page):
            return item
    return None


def _resolve_source_pages(source_pages: Any) -> list[dict[str, Any]]:
    """补全来源页内容：只给页码时取演示页，页码不在演示集中则视为空页。"""
    pages = normalize_source_pages(source_pages)
    resolved: list[dict[str, Any]] = []
    for spec in pages:
        page_no = int(spec["page"])
        if not spec.get("html") and spec.get("items") is None:
            demo = _demo_page(page_no)
            if demo is not None and int(demo["page"]) == page_no:
                spec = {
                    **spec,
                    "html": demo["html"],
                    "title": spec.get("title") or demo["title"],
                }
        resolved.append(spec)
    return resolved


def _page_status(records: list[dict[str, Any]], has_source: bool) -> tuple[str, str]:
    if not records:
        return "解析失败", "来源页没有可用内容" if has_source else "来源页内容为空"
    failed = sum(1 for record in records if record["提取状态"] == "失败")
    if failed == 0:
        return "已解析", ""
    if failed == len(records):
        return "解析失败", "所有报价价格栏都提取失败"
    return "部分成功", f"{failed} 条报价价格栏提取失败"


def _group_pending(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    for page in sorted({int(record["来源页"]) for record in records}):
        rows = [record for record in records if int(record["来源页"]) == page]
        grouped.append({
            "page": page,
            "来源页": page,
            "positions": [int(record["页内位置"]) for record in rows],
            "reasons": [
                {
                    "position": int(record["页内位置"]),
                    "reason": record["失败原因"],
                }
                for record in rows
            ],
        })
    return grouped

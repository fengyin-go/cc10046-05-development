"""报价工作流接口：多页批量解析、逐条字段映射、结果回查。

路由只负责收参数和组装响应；页面解析、位置去重和提取失败判定都在
``app.services`` 里完成。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services.parser_rule import ParserRuleService
from app.services.field_mapper import FieldMapperService
from app.services.quote_ledger import quote_ledger
from app.services.source_pages import demo_source_page_list, parse_source_page

router = APIRouter(prefix="/api/quote-workflow", tags=["报价工作流"])
# 直接挂在模块路径下的入口，方便按解析规则/字段映射的传统路径调用。
module_router = APIRouter(prefix="/api", tags=["报价工作流"])

_parser = ParserRuleService()
_mapper = FieldMapperService()


@router.get("/source-pages")
def list_source_pages() -> dict[str, Any]:
    """可多选的来源页清单（演示数据，含每条可解析条数和待补提示）。"""
    pages = demo_source_page_list()
    return {"items": pages, "total": len(pages)}


@router.post("/parse")
def post_parse(payload: dict[str, Any]) -> dict[str, Any]:
    """批量解析：一次提交多个来源页，逐页逐条保留页内位置。

    页面内容可以来自 ``source_pages``（HTML 或结构化 items），只传页码时
    使用演示来源页；修复后的坏片段直接再提交一次即可。
    """
    source_pages = _source_pages_from_payload(payload)
    if not source_pages:
        raise HTTPException(status_code=400, detail="请至少选择一个来源页")

    result = _parser.domain_parse_source_pages(
        source_pages,
        selectors=payload.get("selectors") or {},
        rule_id=int(payload.get("rule_id", 0) or 0),
    )
    # 解析失败的位置不进入可用集合；已经确认过的位置在结果里仍然可见。
    return {
        **result,
        "available": [
            record for record in result["records"]
            if record["提取状态"] != "失败"
        ],
        "pending": [
            record for record in result["records"]
            if record["提取状态"] == "失败"
        ],
    }


@router.get("/parse/latest")
def latest_parse() -> dict[str, Any]:
    snapshot = quote_ledger.latest_parse()
    if snapshot is None:
        return {"exists": False, "records": [], "pages": []}
    return {
        "exists": True,
        **snapshot,
        "available": [
            record for record in snapshot.get("records", [])
            if record.get("提取状态") != "失败"
        ],
        "pending": [
            record for record in snapshot.get("records", [])
            if record.get("提取状态") == "失败"
        ],
    }


@router.get("/map-records")
def get_map_records(
    page: int | None = Query(default=None, ge=1),
    keyword: str = Query(default=""),
    position: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    """映射结果回查：可用报价 + 待补位置，供接收方打开结果页读取。"""
    return quote_ledger.results(page=page, keyword=keyword, position=position)


@router.post("/map")
def post_map(payload: dict[str, Any]) -> dict[str, Any]:
    """字段映射：批量接收来源页记录集，逐条按原始位置确认报价。

    ``records`` 可以是解析接口返回的记录（带来源页/页内位置），也可以是
    修复后手工整理的记录；``source_pages`` 则会在服务端先解析再映射。
    """
    source_pages = _source_pages_from_payload(payload)
    records = payload.get("records")
    if records is None:
        records = payload.get("source_records")
    mapping_rules = payload.get("mapping_rules") or _schedule_rules(payload)
    dry_run = bool(payload.get("dry_run"))

    if records is None:
        if not source_pages:
            raise HTTPException(status_code=400, detail="请提供来源页记录集或来源页")
        parsed = _parser.domain_parse_source_pages(
            source_pages,
            selectors=payload.get("selectors") or {},
            rule_id=int(payload.get("rule_id", 0) or 0),
        )
        records = parsed["records"]
        parsed_batch = parsed.get("batch_id", "")
    else:
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list):
            raise HTTPException(status_code=400, detail="记录集必须是列表")
        parsed_batch = ""

    if not records:
        raise HTTPException(status_code=400, detail="记录集为空，没有可映射的报价")

    result = _mapper.domain_map_source_records(
        records,
        mapping_rules,
        batch_id=str(payload.get("batch_id") or parsed_batch or ""),
        dry_run=dry_run,
    )
    if not dry_run:
        result["results"] = quote_ledger.results()
    return result


@router.get("/results")
def get_results(
    page: int | None = Query(default=None, ge=1),
    keyword: str = Query(default=""),
) -> dict[str, Any]:
    """接收方读取结果：当前可用的报价，以及哪些来源位置还没补齐。"""
    return quote_ledger.results(page=page, keyword=keyword)


@router.get("/source-page/{page}")
def get_source_page(page: int) -> dict[str, Any]:
    """单页解析预览：便于核对修复后的坏片段。"""
    from app.services.source_pages import DEMO_SOURCE_PAGES

    spec = next(
        (item for item in DEMO_SOURCE_PAGES if int(item["page"]) == page), None)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"没有第 {page} 页的来源数据")
    records = parse_source_page(page, spec["html"])
    return {
        "page": page,
        "title": spec["title"],
        "html": spec["html"],
        "records": records,
        "counts": {
            "record_count": len(records),
            "available_count": sum(1 for row in records if row["提取状态"] != "失败"),
            "pending_count": sum(1 for row in records if row["提取状态"] == "失败"),
            "zero_price_count": sum(1 for row in records if row["零价"]),
        },
    }


def _source_pages_from_payload(payload: dict[str, Any]) -> Any:
    if payload.get("source_pages") is not None:
        return payload["source_pages"]
    if payload.get("pages") is not None:
        return payload["pages"]
    if payload.get("page_numbers") is not None:
        return payload["page_numbers"]
    if payload.get("html") is not None:
        return {
            "page": payload.get("page", 1),
            "html": payload["html"],
            "items": payload.get("items"),
        }
    return None


def _schedule_rules(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """没有显式映射规则时，从 basic 输入里抽字段。"""
    return [
        {
            "source": payload.get("source_field") or "商品",
            "target": payload.get("target_field") or "标准商品名",
            "transform": payload.get("transform") or "",
            "default": payload.get("default") or "",
        },
        {
            "source": "报价",
            "target": "标准报价",
            "transform": "int",
            "default": "",
        },
    ]


# ---------------------------------------------------------------------------
# 模块路径入口：值班员从解析规则页 / 字段映射页直接调用时也能落到同一实现。
# ---------------------------------------------------------------------------

@module_router.post("/parser_rule/parse-batch")
def parser_rule_parse_batch(payload: dict[str, Any]) -> dict[str, Any]:
    """解析规则模块的批量解析入口。"""
    return post_parse(payload)


@module_router.post("/field_mapper/map-batch")
def field_mapper_map_batch(payload: dict[str, Any]) -> dict[str, Any]:
    """字段映射模块的批量映射入口。"""
    return post_map(payload)


@module_router.get("/field_mapper/map-results")
def field_mapper_map_results(
    page: int | None = Query(default=None, ge=1),
    keyword: str = Query(default=""),
    position: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    """字段映射模块回查映射结果和来源位置。"""
    return get_map_records(page=page, keyword=keyword, position=position)

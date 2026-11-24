"""报价确认台账：按来源页位置登记已落库报价，保证同一位置只确认一次。

台账独立于 CRUD 示例数据，保存两类状态：

* ``confirmed``：已经通过字段映射确认、可以对外提供的报价，键是
  ``(来源页, 页内位置)``，同名商品在不同页、不同位置不会互相覆盖；
* ``latest_parse``：最近一次批量解析的完整快照，接收方据此看到还有哪些
  位置待补。

价格提取失败的位置不会进入 ``confirmed``；价格明确为 0 的记录会作为有效报价
进入 ``confirmed``，但会带上 ``零价`` 标记。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

PAGE_KEYS = ("来源页", "页码", "page", "source_page", "sourcePage", "page_number")
POSITION_KEYS = ("页内位置", "位置", "position", "pos", "index", "item_index", "序号")
NAME_KEYS = ("商品", "商品名称", "名称", "name", "title", "product", "product_name")
PRICE_KEYS = ("报价", "价格", "price", "标准报价", "amount", "value")
RAW_PRICE_KEYS = ("原始价格", "raw_price", "price_text", "原始值")
STATUS_KEYS = ("提取状态", "extraction_status", "status", "状态")
REASON_KEYS = ("失败原因", "error", "reason", "错误")


def _pick(source: dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for key in keys:
        if key in source:
            return source[key]
    return default


def _as_page(value: Any) -> int:
    digits = re.search(r"-?\d+", str(value))
    return int(digits.group(0)) if digits else 0


def _as_position(value: Any) -> int:
    digits = re.search(r"-?\d+", str(value))
    return int(digits.group(0)) if digits else 0


def _as_price(value: Any) -> int | float | None:
    """把已解析价格归一化；``None`` 表示提取失败，0 保持 0。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if not text:
        return None
    cleaned = re.sub(r"[,\s，¥￥$€£]", "", text)
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _is_failed(record: dict[str, Any], price: Any) -> bool:
    status = str(_pick(record, STATUS_KEYS, "")).strip()
    if status in {"失败", "待补", "failed", "error", "invalid"}:
        return True
    if status in {"成功", "可用", "ok", "success"}:
        return False
    return price is None


def normalize_quote_record(record: dict[str, Any]) -> dict[str, Any]:
    """把解析结果或外部提交的报价记录补成统一结构。"""
    page = _as_page(_pick(record, PAGE_KEYS, 0))
    position = _as_position(_pick(record, POSITION_KEYS, 0))
    name = str(_pick(record, NAME_KEYS, "") or "").strip()
    raw_price = _pick(record, RAW_PRICE_KEYS, None)
    price = _pick(record, PRICE_KEYS, None)
    price = _as_price(price)
    failed = _is_failed(record, price)
    if failed:
        price = None
    reason = str(_pick(record, REASON_KEYS, "") or "")
    if failed and not reason:
        reason = "价格栏提取失败"
    return {
        "来源页": page,
        "页码": page,
        "source_page": page,
        "页内位置": position,
        "位置": position,
        "position": position,
        "source_position": position,
        "来源位置": f"第{page}页#{position}",
        "商品": name,
        "名称": name,
        "name": name,
        "报价": price,
        "价格": price,
        "price": price,
        "原始价格": raw_price,
        "raw_price": raw_price,
        "零价": bool(price == 0 and not failed),
        "zero_price": bool(price == 0 and not failed),
        "提取状态": "失败" if failed else "成功",
        "extraction_status": "failed" if failed else "ok",
        "状态": "待补" if failed else "可用",
        "status": "待补" if failed else "可用",
        "失败原因": reason,
        "error": reason,
        "来源页标题": record.get("来源页标题", f"第{page}页"),
        "rule_id": record.get("rule_id", 0),
    }


def _source_key(record: dict[str, Any]) -> tuple[int, int]:
    return int(record["来源页"]), int(record["页内位置"])


def _group_pending_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    for page in sorted({int(record["来源页"]) for record in records}):
        rows = [record for record in records if int(record["来源页"]) == page]
        grouped.append({
            "page": page,
            "来源页": page,
            "positions": [int(record["页内位置"]) for record in rows],
            "reasons": [
                {"position": int(record["页内位置"]), "reason": record["失败原因"]}
                for record in rows
            ],
        })
    return grouped


def _same_quote(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("商品") == right.get("商品")
        and _as_price(left.get("报价")) == _as_price(right.get("报价"))
    )


class QuoteLedger:
    """进程内台账。真实项目可平移到数据库的唯一键 ``(来源页, 页内位置)``。"""

    def __init__(self) -> None:
        self._confirmed: dict[tuple[int, int], dict[str, Any]] = {}
        self._order: list[tuple[int, int]] = []
        self._latest_parse: dict[str, Any] | None = None
        self._parse_seq = 0
        self._record_seq = 0

    # -- 解析快照 ------------------------------------------------------
    def save_parse(self, result: dict[str, Any]) -> dict[str, Any]:
        """保存解析快照并返回本次提交的结果。

        * 本次提交的结果原样返回（只重送一页时就只有这一页）；
        * 最近一次快照按页合并，只重送修复页时不会丢掉其他页的解析结果，
          接收方看到的是累计视图。
        """
        self._parse_seq += 1
        batch_id = f"PARSE-{self._parse_seq}"
        submitted = {
            **result,
            "batch_id": batch_id,
            "parse_batch": batch_id,
        }

        incoming_pages = {int(page["page"]): page for page in result.get("pages", [])}
        incoming_records = result.get("records", [])

        merged_pages: dict[int, dict[str, Any]] = {}
        merged_records: dict[tuple[int, int], dict[str, Any]] = {}
        if self._latest_parse is not None:
            for page in self._latest_parse.get("pages", []):
                merged_pages[int(page["page"])] = page
            for record in self._latest_parse.get("records", []):
                merged_records[(int(record["来源页"]), int(record["页内位置"]))] = record

        merged_pages.update(incoming_pages)
        for record in incoming_records:
            merged_records[(int(record["来源页"]), int(record["页内位置"]))] = record

        records = sorted(
            merged_records.values(),
            key=lambda row: (int(row["来源页"]), int(row["页内位置"])),
        )
        pages = [merged_pages[key] for key in sorted(merged_pages)]
        statuses = [page.get("status", "") for page in pages]
        if not pages:
            overall = "解析失败"
        elif all(status == "已解析" for status in statuses):
            overall = "已解析"
        elif any(status in {"已解析", "部分成功"} for status in statuses):
            overall = "部分成功"
        else:
            overall = "解析失败"

        self._latest_parse = {
            **result,
            "batch_id": batch_id,
            "parse_batch": batch_id,
            "pages": pages,
            "records": records,
            "status": overall,
            "partial_failure": any(status != "已解析" for status in statuses),
            "counts": {
                **result.get("counts", {}),
                "page_count": len(pages),
                "record_count": len(records),
                "available_count": sum(
                    1 for row in records if row.get("提取状态") != "失败"),
                "pending_count": sum(
                    1 for row in records if row.get("提取状态") == "失败"),
                "zero_price_count": sum(1 for row in records if row.get("零价")),
                "failed_page_count": sum(
                    1 for status in statuses if status != "已解析"),
            },
            "pending_positions": _group_pending_records(
                [row for row in records if row.get("提取状态") == "失败"]),
        }
        return submitted

    def latest_parse(self) -> dict[str, Any] | None:
        return self._latest_parse

    # -- 映射确认 ------------------------------------------------------
    def confirm_records(
        self,
        records: Iterable[dict[str, Any]],
        *,
        mapping_rules: list[dict[str, Any]] | None = None,
        batch_id: str = "",
    ) -> dict[str, Any]:
        mapping_rules = mapping_rules or []
        accepted: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []

        for raw in records:
            record = normalize_quote_record(raw)
            if record["提取状态"] == "失败":
                pending.append(record)
                continue

            key = _source_key(record)
            if key in self._confirmed:
                existing = self._confirmed[key]
                if _same_quote(existing, record):
                    skipped.append({
                        **record,
                        "跳过原因": "同一来源位置已确认过该报价，未重复登记",
                    })
                else:
                    rejected.append({
                        **record,
                        "拒绝原因": (
                            f"来源位置 {record['来源位置']} 已确认报价"
                            f"「{existing.get('商品')} {existing.get('报价')}」，"
                            "不能再用另一条报价覆盖"
                        ),
                    })
                continue

            self._record_seq += 1
            mapped = _apply_mapping(record, mapping_rules)
            entry = {
                "记录编号": f"QUOTE-{self._record_seq}",
                "来源页": record["来源页"],
                "页内位置": record["页内位置"],
                "来源位置": record["来源位置"],
                "source_page": record["来源页"],
                "source_position": record["页内位置"],
                "来源页标题": record["来源页标题"],
                "商品": record["商品"],
                "名称": record["商品"],
                "报价": record["报价"],
                "价格": record["报价"],
                "标准报价": record["报价"],
                "原始价格": record["原始价格"],
                "零价": record["零价"],
                "状态": "已确认",
                "status": "已确认",
                "parse_batch": batch_id or (self._latest_parse or {}).get("batch_id", ""),
                "映射字段": mapped,
                "mapping_rules": mapping_rules,
            }
            self._confirmed[key] = entry
            self._order.append(key)
            accepted.append(entry)

        return {
            "accepted": accepted,
            "skipped": skipped,
            "rejected": rejected,
            "pending": pending,
            "counts": {
                "accepted": len(accepted),
                "skipped": len(skipped),
                "rejected": len(rejected),
                "pending": len(pending),
            },
        }

    # -- 查询 ----------------------------------------------------------
    def confirmed_rows(
        self, *, page: int | None = None, keyword: str = "",
        position: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = [self._confirmed[key] for key in self._order]
        if page is not None:
            rows = [row for row in rows if int(row["来源页"]) == int(page)]
        if position is not None:
            rows = [row for row in rows if int(row["页内位置"]) == int(position)]
        if keyword:
            needle = keyword.casefold()
            rows = [
                row for row in rows
                if needle in str(row.get("商品", "")).casefold()
                or needle in str(row.get("来源位置", "")).casefold()
            ]
        return [dict(row) for row in rows]

    def pending_rows(self) -> list[dict[str, Any]]:
        if self._latest_parse is None:
            return []
        pending: list[dict[str, Any]] = []
        for raw in self._latest_parse.get("records", []):
            record = normalize_quote_record(raw)
            if record["提取状态"] != "失败":
                continue
            if _source_key(record) in self._confirmed:
                # 同一位置已经在后续提交中补齐，不再算待补。
                continue
            pending.append(record)
        return pending

    def parsed_available_rows(self) -> list[dict[str, Any]]:
        """最近一次解析里提取成功、但未必已经确认的报价。"""
        if self._latest_parse is None:
            return []
        rows: list[dict[str, Any]] = []
        for raw in self._latest_parse.get("records", []):
            record = normalize_quote_record(raw)
            if record["提取状态"] == "失败":
                continue
            key = _source_key(record)
            confirmed = self._confirmed.get(key)
            rows.append({
                **record,
                "已确认": confirmed is not None,
                "confirmed": confirmed is not None,
                "记录编号": (confirmed or {}).get("记录编号", ""),
            })
        return rows

    def results(
        self, *, page: int | None = None, keyword: str = "",
        position: int | None = None,
    ) -> dict[str, Any]:
        available = self.confirmed_rows(page=page, keyword=keyword, position=position)
        pending = self.pending_rows()
        parsed_available = self.parsed_available_rows()
        if page is not None:
            pending = [row for row in pending if int(row["来源页"]) == int(page)]
            parsed_available = [
                row for row in parsed_available if int(row["来源页"]) == int(page)]
        if position is not None:
            pending = [row for row in pending if int(row["页内位置"]) == int(position)]
            parsed_available = [
                row for row in parsed_available if int(row["页内位置"]) == int(position)]
        if keyword:
            needle = keyword.casefold()
            pending = [
                row for row in pending
                if needle in str(row.get("商品", "")).casefold()
                or needle in str(row.get("来源位置", "")).casefold()
            ]
            parsed_available = [
                row for row in parsed_available
                if needle in str(row.get("商品", "")).casefold()
                or needle in str(row.get("来源位置", "")).casefold()
            ]

        page_numbers = sorted({
            *[int(row["来源页"]) for row in available],
            *[int(row["来源页"]) for row in pending],
            *[int(row["来源页"]) for row in parsed_available],
        })
        pages: list[dict[str, Any]] = []
        for page_no in page_numbers:
            page_available = [
                row for row in available if int(row["来源页"]) == page_no]
            page_pending = [
                row for row in pending if int(row["来源页"]) == page_no]
            page_parsed = [
                row for row in parsed_available if int(row["来源页"]) == page_no]
            saved = self._parse_page(page_no)
            pages.append({
                "page": page_no,
                "来源页": page_no,
                "title": (saved or {}).get("title", f"第{page_no}页"),
                "available_count": len(page_available),
                "parsed_count": len(page_parsed),
                "pending_count": len(page_pending),
                "pending_positions": [int(row["页内位置"]) for row in page_pending],
                "parse_status": (saved or {}).get("status", ""),
                "complete": not page_pending,
            })

        pending_pages = [
            {
                "page": item["page"],
                "来源页": item["page"],
                "positions": item["pending_positions"],
                "reasons": [
                    {
                        "position": int(row["页内位置"]),
                        "商品": row["商品"],
                        "reason": row["失败原因"],
                        "原始价格": row["原始价格"],
                    }
                    for row in pending
                    if int(row["来源页"]) == item["page"]
                ],
            }
            for item in pages
            if item["pending_positions"]
        ]
        return {
            "available": available,
            "records": available,
            "parsed_available": parsed_available,
            "pending": pending,
            "pending_pages": pending_pages,
            "pages": pages,
            "counts": {
                "available": len(available),
                "parsed_available": len(parsed_available),
                "pending": len(pending),
                "zero_price": sum(1 for row in available if row.get("零价")),
                "page_count": len(pages),
            },
            "latest_parse": {
                "batch_id": (self._latest_parse or {}).get("batch_id", ""),
                "status": (self._latest_parse or {}).get("status", ""),
            },
        }

    def _parse_page(self, page_no: int) -> dict[str, Any] | None:
        if self._latest_parse is None:
            return None
        for item in self._latest_parse.get("pages", []):
            if int(item.get("page", 0)) == int(page_no):
                return item
        return None

    def reset(self) -> None:
        self._confirmed.clear()
        self._order.clear()
        self._latest_parse = None
        self._parse_seq = 0
        self._record_seq = 0


def _apply_mapping(record: dict[str, Any], rules: list[dict[str, Any]]) -> dict[str, Any]:
    """逐条把报价映射到目标字段；提取失败的价格不会被默认值伪造成 0。"""
    if not rules:
        return {
            "商品名称": record["商品"],
            "标准报价": record["报价"],
            "零价": record["零价"],
        }
    mapped: dict[str, Any] = {}
    for rule in rules:
        source = rule.get("source", "")
        target = rule.get("target", "")
        transform = rule.get("transform", "")
        default = rule.get("default", "")
        value = record.get(source, default)
        if transform == "upper":
            value = str(value).upper()
        elif transform == "lower":
            value = str(value).lower()
        elif transform == "int":
            if value is None:
                value = None
            else:
                try:
                    value = int(float(value))
                except (TypeError, ValueError):
                    value = None
        mapped[target or source] = value
    return mapped


quote_ledger = QuoteLedger()

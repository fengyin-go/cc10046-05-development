"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.quote_ledger import normalize_quote_record, quote_ledger


class FieldMapperService(BaseModuleService):
    MODULE = "field_mapper"
    ENTITY = "映射方案"
    OPERATION_TERM = "转换"
    LIST_FIELDS = ["方案标识", "来源字段", "目标字段", "转换函数", "默认取值", "映射版本", "适用站点", "依赖字段"]
    REQUIRED_FIELDS = ["方案标识", "来源字段", "目标字段"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "启用映射": "已生效", "停用映射": "已停用"}
    NEGATIVE_ACTIONS = ["停用映射"]

    def domain_map_fields(self, source_data: dict, mapping_rules: list[dict]) -> dict:
        """根据映射规则将来源字段转换为目标字段。"""
        result: dict = {}
        for rule in mapping_rules:
            src_field = rule.get("source", "")
            tgt_field = rule.get("target", "")
            default = rule.get("default", "")
            transform = rule.get("transform", "")

            value = source_data.get(src_field, default)
            if transform == "upper":
                value = str(value).upper()
            elif transform == "lower":
                value = str(value).lower()
            elif transform == "int":
                try:
                    value = int(float(value))
                except (ValueError, TypeError):
                    value = 0
            result[tgt_field] = value
        return result

    def domain_map_source_records(
        self,
        records: list[dict[str, Any]],
        mapping_rules: list[dict[str, Any]] | None = None,
        *,
        batch_id: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """批量接收来源页记录集，逐条按原始位置登记报价。

        * 每条记录都带着 ``来源页`` / ``页内位置`` 回查原始页面；
        * 价格提取失败的记录只进入 ``pending``，不会被映射成 0；
        * 同一 ``(来源页, 页内位置)`` 已经确认过时，重复提交会被 ``skipped``，
          换成另一条报价则被 ``rejected``，两种情况都不会二次落库。
        """
        normalized = [normalize_quote_record(record) for record in records]
        if dry_run:
            mapped = [
                {
                    **record,
                    "映射字段": _dry_map(record, mapping_rules or []),
                }
                for record in normalized
            ]
            return {
                "dry_run": True,
                "mapped": mapped,
                "counts": {
                    "received": len(normalized),
                    "mappable": sum(1 for row in normalized if row["提取状态"] != "失败"),
                    "pending": sum(1 for row in normalized if row["提取状态"] == "失败"),
                },
            }

        outcome = quote_ledger.confirm_records(
            normalized, mapping_rules=mapping_rules or [], batch_id=batch_id)
        outcome.update({
            "dry_run": False,
            "batch_id": batch_id or (quote_ledger.latest_parse() or {}).get("batch_id", ""),
            "message": _summary(outcome["counts"]),
        })
        return outcome

    # 入口别名：记录集映射与来源记录集映射是同一实现。
    def domain_map_records(
        self,
        records: list[dict[str, Any]],
        mapping_rules: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self.domain_map_source_records(records, mapping_rules, **kwargs)


def _dry_map(record: dict[str, Any], rules: list[dict[str, Any]]) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for rule in rules:
        source = rule.get("source", "")
        target = rule.get("target", "") or source
        value = record.get(source, rule.get("default", ""))
        transform = rule.get("transform", "")
        if transform == "int":
            try:
                value = int(float(value)) if value is not None else None
            except (TypeError, ValueError):
                value = None
        mapped[target] = value
    return mapped


def _summary(counts: dict[str, int]) -> str:
    parts = [f"确认 {counts['accepted']} 条"]
    if counts["skipped"]:
        parts.append(f"重复跳过 {counts['skipped']} 条")
    if counts["rejected"]:
        parts.append(f"位置冲突拒绝 {counts['rejected']} 条")
    if counts["pending"]:
        parts.append(f"待补 {counts['pending']} 条")
    return "，".join(parts)

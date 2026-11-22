"""访问审计领域服务：访问明细 + 按湖表归属聚合的分域计量。

明细记录只负责审计本身；周期结算、归属对照和分域数据集由
`domain_metering` 领域服务从这些明细派生，状态流转仍留在服务层。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.domain_metering import service as metering
from app.store import store


class AccessAuditService(BaseModuleService):
    MODULE = "access_audit"
    ENTITY = "访问记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["访问者", "目标湖表", "操作类型", "访问时间", "返回记录数"]
    REQUIRED_FIELDS = ["访问者", "目标湖表", "操作类型"]
    STATUS_ORDER = ["已记录", "异常访问"]
    ACTION_RULES = {"标记异常": "异常访问", "忽略告警": "已记录"}
    NEGATIVE_ACTIONS = ["忽略告警"]

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记访问时补齐访问编号，保证后续结算能按编号复查。"""
        entry, missing = super().create_entry(values)
        if entry is None:
            return entry, missing
        metering.ensure_access_number(entry)
        return entry, missing

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        """访问详情附带访问编号和按访问时点算出的业务域归属。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None
        access_no = metering.ensure_access_number(entry)
        detail = dict(entry)
        detail["访问编号"] = access_no
        detail["分域归属"] = metering.attribute_access(entry)
        ledger = [
            row for row in store.rows("settlement_ledger")
            if row.get("访问编号") == access_no
        ]
        detail["分域计量"] = ledger[0] if ledger else None
        return detail

    def attribution_for(self, entry_id: int) -> dict[str, Any] | None:
        """查询单条访问按访问时点得到的归属；未归属返回 None。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None
        return metering.attribute_access(entry)

    def domain_usage(self, domain: str, period: str) -> dict[str, Any]:
        """本业务域在指定周期的用量数据集。"""
        return metering.build_dataset(domain, period)

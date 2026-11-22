"""分域计量领域服务：把访问审计明细按湖表归属聚合成业务域用量。

数据流：
    湖表注册 ──(表标识 + 业务域归属记录)──┐
                                         ├─ 按访问时点归属 ── 结算归属对照
    访问审计 ──(访问编号 + 返回记录数)───┘        │
                                                 ├─ 历史周期：已出账、冻结，供旧域复查
                                                 └─ 当期周期：未结算，供新域复核用量

三条硬约束：
1. 每条访问在所有结算里最多出现一次，按访问编号唯一去重；
2. 归属以访问时点生效的归属记录为准，出账后再转域不会改写旧账；
3. 结算明细按业务域隔离，查看其他业务域的结算直接拒绝，不泄露明细。
"""
from __future__ import annotations

import datetime
import re
from typing import Any

from app.store import store

OWNERSHIP = "lake_table_ownership"
PERIODS = "settlement_period"
SETTLEMENTS = "domain_settlement"
LEDGER = "settlement_ledger"

ARCHIVE_EPOCH = "1970-01-01T00:00:00"


class DomainMeteringError(Exception):
    """分域计量的业务校验错误（对应 HTTP 400）。"""


class DomainScopeError(DomainMeteringError):
    """跨业务域读取结算明细；拒绝时不携带任何明细（对应 HTTP 403）。"""


class NotFoundError(DomainMeteringError):
    """结算、周期或访问编号不存在（对应 HTTP 404）。"""


def parse_dt(value: Any) -> datetime.datetime | None:
    """解析 ISO 时间/日期字符串，无法解析时返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime.datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def iso(dt: datetime.datetime) -> str:
    return dt.isoformat(timespec="seconds")


def to_int(value: Any) -> tuple[int, bool]:
    """返回 (记录数, 是否为合法数值)。非数值按 0 计量并标记异常。"""
    if isinstance(value, bool):
        return 0, False
    if isinstance(value, int):
        return value, True
    if isinstance(value, float):
        return int(value), True
    text = str(value or "").strip()
    if re.fullmatch(r"\d+", text):
        return int(text), True
    return 0, False


class DomainMeteringService:
    """湖表归属、周期结算和分域访问数据集的统一领域服务。"""

    # ---------- 湖表标识与归属登记 ----------

    def _next_id(self, module: str) -> int:
        rows = store.rows(module)
        return max((int(row.get("id", 0)) for row in rows), default=0) + 1

    def ensure_table_identifier(self, table: dict[str, Any]) -> str:
        """湖表注册必须带稳定的表标识；缺失时按序补齐。"""
        identifier = str(table.get("表标识") or "").strip()
        if identifier:
            return identifier
        identifiers = {
            str(row.get("表标识"))
            for row in store.rows("lake_table")
            if row.get("表标识")
        }
        sequence = 1
        while f"LT-{sequence:04d}" in identifiers:
            sequence += 1
        identifier = f"LT-{sequence:04d}"
        table["表标识"] = identifier
        return identifier

    def _next_ownership_no(self) -> str:
        used = {
            str(row.get("归属记录编号"))
            for row in store.rows(OWNERSHIP)
            if row.get("归属记录编号")
        }
        sequence = 1
        while f"OWN-{sequence:04d}" in used:
            sequence += 1
        return f"OWN-{sequence:04d}"

    def _append_ownership(
        self,
        *,
        table: dict[str, Any],
        domain: str,
        effective: str,
        reason: str,
        operator: str,
    ) -> dict[str, Any]:
        record = {
            "id": self._next_id(OWNERSHIP),
            "归属记录编号": self._next_ownership_no(),
            "表标识": self.ensure_table_identifier(table),
            "湖表ID": table.get("id"),
            "表名": table.get("表名", ""),
            "业务域": domain,
            "生效时间": effective,
            "失效时间": None,
            "状态": "已生效",
            "变更原因": reason,
            "操作人": operator,
        }
        store.rows(OWNERSHIP).append(record)
        return record

    def ensure_ownership(
        self,
        table: dict[str, Any],
        *,
        effective: str = ARCHIVE_EPOCH,
        reason: str = "湖表注册初始化归属",
    ) -> list[dict[str, Any]]:
        """湖表还没有归属记录时补一条归属；老表默认从最早时点生效。

        新建湖表应传入登记时刻，避免把登记前的访问也算进新表。
        """
        identifier = self.ensure_table_identifier(table)
        existing = [
            row for row in store.rows(OWNERSHIP)
            if row.get("表标识") == identifier
        ]
        if existing:
            for row in existing:
                row.setdefault("表名", table.get("表名", ""))
                row.setdefault("湖表ID", table.get("id"))
            return existing
        domain = str(table.get("所属业务域") or "").strip() or "未分配"
        return [self._append_ownership(
            table=table,
            domain=domain,
            effective=effective,
            reason=reason,
            operator="湖表注册",
        )]

    def register_ownership(
        self, table: dict[str, Any], operator: str = "湖表注册"
    ) -> dict[str, Any]:
        """湖表注册入湖时登记带表标识的业务域归属记录。"""
        domain = str(table.get("所属业务域") or "").strip() or "未分配"
        records = self.ensure_ownership(table)
        latest = max(
            records, key=lambda row: str(row.get("生效时间", ""))
        )
        if latest.get("业务域") == domain and latest.get("失效时间") is None:
            return latest
        effective = iso(datetime.datetime.now())
        latest_start = parse_dt(latest.get("生效时间"))
        if latest_start is not None and parse_dt(effective) <= latest_start:
            effective = iso(latest_start + datetime.timedelta(seconds=1))
        return self.change_ownership(
            int(table.get("id", 0)),
            domain=domain,
            effective_time=effective,
            reason="湖表注册入湖时确认归属",
            operator=operator,
        )

    def seed_ownership_for_new_table(self, table: dict[str, Any]) -> dict[str, Any]:
        """新建湖表：归属从登记时刻起生效，登记前的访问不归属新表。"""
        records = self.ensure_ownership(
            table,
            effective=iso(datetime.datetime.now()),
            reason="湖表登记时初始化归属",
        )
        return records[0]

    def list_ownership(
        self,
        *,
        table_id: int | None = None,
        table_name: str | None = None,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = list(store.rows(OWNERSHIP))
        if table_id is not None:
            table = store.find("lake_table", table_id)
            if table is None:
                return []
            identifier = str(table.get("表标识") or "").strip()
            if not identifier:
                return []
            rows = [row for row in rows if row.get("表标识") == identifier]
        if table_name:
            rows = [row for row in rows if row.get("表名") == table_name]
        if domain:
            rows = [row for row in rows if row.get("业务域") == domain]
        return sorted(
            rows,
            key=lambda row: (str(row.get("表标识", "")), str(row.get("生效时间", ""))),
        )

    def ownership_at(
        self, table_identifier: str, at: datetime.datetime
    ) -> dict[str, Any] | None:
        """取访问时点生效的归属记录；区间为 [生效时间, 失效时间)。"""
        candidates: list[tuple[datetime.datetime, dict[str, Any]]] = []
        for row in store.rows(OWNERSHIP):
            if row.get("表标识") != table_identifier:
                continue
            start = parse_dt(row.get("生效时间"))
            end = parse_dt(row.get("失效时间"))
            if start is None or at < start:
                continue
            if end is not None and at >= end:
                continue
            candidates.append((start, row))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def current_ownership(
        self, table_identifier: str, at: datetime.datetime | None = None
    ) -> dict[str, Any] | None:
        return self.ownership_at(table_identifier, at or datetime.datetime.now())

    def change_ownership(
        self,
        table_id: int,
        *,
        domain: str,
        effective_time: str | None = None,
        reason: str | None = None,
        operator: str = "湖表注册",
    ) -> dict[str, Any]:
        """变更湖表业务域：关闭旧归属记录并追加一条新的生效记录。"""
        table = store.find("lake_table", table_id)
        if table is None:
            raise NotFoundError(f"湖表 {table_id} 不存在")
        new_domain = str(domain or "").strip()
        if not new_domain:
            raise DomainMeteringError("业务域不能为空")

        identifier = self.ensure_table_identifier(table)
        records = self.ensure_ownership(table)
        effective_dt = parse_dt(effective_time) if effective_time else datetime.datetime.now()
        if effective_dt is None:
            raise DomainMeteringError("生效时间格式不正确，应为 ISO 时间")

        latest_start = max(
            (parse_dt(row.get("生效时间")) for row in records),
            default=None,
            key=lambda value: value or datetime.datetime.min,
        )
        if latest_start is not None and effective_dt <= latest_start:
            raise DomainMeteringError("生效时间必须晚于当前归属记录的生效时间")

        active = [
            row for row in records
            if row.get("失效时间") is None and row.get("状态") == "已生效"
        ]
        if len(active) == 1 and active[0].get("业务域") == new_domain:
            raise DomainMeteringError(f"湖表已归属业务域 {new_domain}，无需变更")

        effective_iso = iso(effective_dt)
        previous_domain = active[0].get("业务域", "") if active else ""
        for row in active:
            row["失效时间"] = effective_iso
            row["状态"] = "已失效"
            row["失效原因"] = "业务域变更"

        record = self._append_ownership(
            table=table,
            domain=new_domain,
            effective=effective_iso,
            reason=reason or f"业务域变更：{previous_domain} → {new_domain}",
            operator=operator,
        )
        table["所属业务域"] = new_domain

        store.log_event("lake_table_ownership_changed", "lake_table", "access_audit", {
            "table_id": table_id,
            "table_identifier": identifier,
            "ownership_no": record["归属记录编号"],
            "from_domain": previous_domain,
            "to_domain": new_domain,
            "effective_time": effective_iso,
        })
        return record

    # ---------- 访问归属 ----------

    def ensure_access_number(self, access: dict[str, Any]) -> str:
        """每条访问都要有可复查的访问编号；缺失时按访问时间生成。"""
        number = str(access.get("访问编号") or "").strip()
        if number:
            return number
        at = parse_dt(access.get("访问时间"))
        prefix = at.strftime("%Y%m") if at else "UNKNOWN"
        access_id = int(access.get("id", 0))
        used = {
            str(row.get("访问编号"))
            for row in store.rows("access_audit")
            if row.get("访问编号")
        }
        number = f"ACC-{prefix}-{access_id:03d}"
        while number in used:
            number += "-1"
        access["访问编号"] = number
        return number

    def _table_for_access(self, access: dict[str, Any]) -> dict[str, Any] | None:
        table_name = str(access.get("目标湖表") or "").strip()
        if not table_name:
            return None
        matches = store.find_by_field("lake_table", "表名", table_name)
        return matches[0] if matches else None

    def attribute_access(self, access: dict[str, Any]) -> dict[str, Any] | None:
        """按访问时点把访问归属到一个业务域；无法归属时返回 None。"""
        table = self._table_for_access(access)
        if table is None:
            return None
        self.ensure_ownership(table)
        identifier = self.ensure_table_identifier(table)
        at = parse_dt(access.get("访问时间"))
        if at is None:
            return None
        record = self.ownership_at(identifier, at)
        if record is None:
            return None
        return {
            "表标识": identifier,
            "业务域": record.get("业务域", ""),
            "归属记录编号": record.get("归属记录编号", ""),
            "归属生效时间": record.get("生效时间"),
            "归属失效时间": record.get("失效时间"),
            "归属依据": (
                f"访问时点归属：{record.get('生效时间')} 起生效的 "
                f"{record.get('归属记录编号')}（{record.get('业务域')}）"
            ),
        }

    # ---------- 周期与结算 ----------

    def find_period(self, period_id: str) -> dict[str, Any] | None:
        for row in store.rows(PERIODS):
            if row.get("周期编号") == period_id:
                return row
        return None

    def _ensure_period(self, at: datetime.datetime) -> dict[str, Any]:
        period_id = at.strftime("%Y-%m")
        existing = self.find_period(period_id)
        if existing is not None:
            return existing
        start = at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        period = {
            "id": self._next_id(PERIODS),
            "周期编号": period_id,
            "周期名称": f"{start.year}年{start.month:02d}月",
            "开始时间": iso(start),
            "结束时间": iso(end),
            "状态": "开放",
            "数据集类型": "当期",
            "结算时间": None,
        }
        store.rows(PERIODS).append(period)
        return period

    def _settlement_no(self, period_id: str, domain: str) -> str:
        return f"ST-{period_id}-{domain}"

    def _find_settlement(self, settlement_no: str) -> dict[str, Any] | None:
        for row in store.rows(SETTLEMENTS):
            if row.get("结算编号") == settlement_no:
                return row
        return None

    def _ensure_settlement(self, period: dict[str, Any], domain: str) -> dict[str, Any]:
        """取业务域在该周期仍开放的结算单；已出账时另开补计单，不改旧账。"""
        period_id = str(period.get("周期编号"))
        base_no = self._settlement_no(period_id, domain)
        existing = sorted(
            (
                row for row in self._settlements_for(period_id, domain)
            ),
            key=lambda row: str(row.get("结算编号")),
        )
        for row in existing:
            if not row.get("frozen"):
                return row
        if not existing:
            settlement_no = base_no
        else:
            # 主结算已冻结：新的访问进入后续补计单，序号递增；
            # 旧结算的归属对照和金额不会被改写。
            sequence = 1
            while self._find_settlement(f"{base_no}-补计{sequence}") is not None:
                sequence += 1
            settlement_no = f"{base_no}-补计{sequence}"
        settlement = {
            "id": self._next_id(SETTLEMENTS),
            "结算编号": settlement_no,
            "周期编号": period_id,
            "业务域": domain,
            "状态": "待结算",
            "结算状态": "未结算",
            "数据版本": "当期",
            "访问条数": 0,
            "记录数合计": 0,
            "湖表数": 0,
            "冲突条数": 0,
            "生成时间": iso(datetime.datetime.now()),
            "结算时间": None,
            "frozen": False,
            "补计": settlement_no != base_no,
        }
        store.rows(SETTLEMENTS).append(settlement)
        return settlement

    def _ledger_by_access_no(self) -> dict[str, dict[str, Any]]:
        return {
            str(row.get("访问编号")): row
            for row in store.rows(LEDGER)
            if row.get("访问编号")
        }

    def _annotate_conflict(self, row: dict[str, Any], table_identifier: str) -> None:
        current = self.current_ownership(table_identifier)
        current_domain = current.get("业务域") if current else None
        current_no = current.get("归属记录编号") if current else None
        conflict = bool(current_domain and current_domain != row.get("业务域"))
        row["当前归属业务域"] = current_domain
        row["当前归属记录编号"] = current_no
        row["归属冲突"] = conflict
        row["冲突说明"] = (
            f"访问时点归属 {row.get('业务域')}（{row.get('归属记录编号')}），"
            f"当前湖表已转至 {current_domain}（{current_no}）；"
            f"旧账仍计入 {row.get('业务域')}，不回滚、不重复计量"
            if conflict else ""
        )

    def materialize(self, period_id: str) -> dict[str, Any]:
        """把周期内尚未进入归属对照的访问补进对应业务域结算。

        已完成结算（冻结）的归属对照不会被改写，也不会把同一访问重复计给
        另一个业务域；这里只追加还没有访问编号的对照记录。
        """
        period = self.find_period(period_id)
        if period is None:
            raise NotFoundError(f"结算周期 {period_id} 不存在")
        start = parse_dt(period.get("开始时间"))
        end = parse_dt(period.get("结束时间"))
        if start is None or end is None:
            raise DomainMeteringError(f"结算周期 {period_id} 的时间范围不完整")

        ledger_index = self._ledger_by_access_no()
        added = 0
        unattributed = 0

        for access in list(store.rows("access_audit")):
            at = parse_dt(access.get("访问时间"))
            if at is None or not (start <= at < end):
                continue
            access_no = self.ensure_access_number(access)
            if access_no in ledger_index:
                # 已经是某张结算单的对照，绝不重复计给另一个业务域。
                continue
            attribution = self.attribute_access(access)
            if attribution is None:
                unattributed += 1
                continue
            settlement = self._ensure_settlement(period, attribution["业务域"])
            record_count, numeric = to_int(access.get("返回记录数"))
            row = {
                "id": self._next_id(LEDGER),
                "结算编号": settlement["结算编号"],
                "周期编号": period_id,
                "访问编号": access_no,
                "访问ID": access.get("id"),
                "业务域": attribution["业务域"],
                "目标湖表": access.get("目标湖表", ""),
                "表标识": attribution["表标识"],
                "表标识": attribution["表标识"],
                "访问时间": access.get("访问时间"),
                "访问者": access.get("访问者", ""),
                "操作类型": access.get("操作类型", ""),
                "返回记录数": record_count,
                "计量口径": "按访问实际返回记录数",
                "计量异常": not numeric,
                "归属记录编号": attribution["归属记录编号"],
                "归属生效时间": attribution["归属生效时间"],
                "归属失效时间": attribution["归属失效时间"],
                "归属依据": attribution["归属依据"],
                "结算状态": settlement.get("结算状态", "未结算"),
                "冻结": bool(settlement.get("frozen")),
            }
            self._annotate_conflict(row, attribution["表标识"])
            store.rows(LEDGER).append(row)
            ledger_index[access_no] = row
            added += 1

        self._recompute_settlements(period_id)
        return {
            "周期编号": period_id,
            "新增对照": added,
            "未归属访问": unattributed,
        }

    def _recompute_settlements(self, period_id: str) -> None:
        for settlement in store.rows(SETTLEMENTS):
            if settlement.get("周期编号") != period_id:
                continue
            rows = [
                row for row in store.rows(LEDGER)
                if row.get("结算编号") == settlement.get("结算编号")
            ]
            for row in rows:
                identifier = str(row.get("表标识") or "")
                if identifier:
                    self._annotate_conflict(row, identifier)
                else:
                    row["归属冲突"] = False
                    row["冲突说明"] = ""
                row["冻结"] = bool(settlement.get("frozen"))
                row["结算状态"] = settlement.get("结算状态", "未结算")
            settlement["访问条数"] = len(rows)
            settlement["记录数合计"] = sum(
                int(row.get("返回记录数", 0)) for row in rows
            )
            settlement["湖表数"] = len({
                str(row.get("目标湖表", "")) for row in rows
            })
            settlement["冲突条数"] = sum(
                1 for row in rows if row.get("归属冲突")
            )
            if settlement.get("frozen"):
                settlement["状态"] = "已出账"
                settlement["结算状态"] = "已结算"
                settlement["数据版本"] = "历史"
            else:
                settlement["状态"] = settlement.get("状态", "待结算")
                settlement["结算状态"] = settlement.get("结算状态", "未结算")
                settlement["数据版本"] = "当期"

    def _settlements_for_period(self, period_id: str) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(SETTLEMENTS)
            if row.get("周期编号") == period_id
        ]

    def _settlements_for(self, period_id: str, domain: str) -> list[dict[str, Any]]:
        return [
            row for row in self._settlements_for_period(period_id)
            if row.get("业务域") == domain
        ]

    # ---------- 分域数据集 ----------

    def build_dataset(self, domain: str, period_id: str) -> dict[str, Any]:
        """按业务域和周期生成分域访问数据集，包含按表聚合与可读明细。"""
        viewer = str(domain or "").strip()
        if not viewer:
            raise DomainScopeError("必须指定业务域后才能查看结算数据集")
        period = self.find_period(period_id)
        if period is None:
            raise NotFoundError(f"结算周期 {period_id} 不存在")

        self.materialize(period_id)
        own = self._settlements_for(period_id, viewer)
        if not own:
            # 本域在该周期没有归属记录：返回空数据集，不返回其他业务域的任何字段。
            return self._empty_dataset(viewer, period)
        # 一个业务域在同一周期可能既有已出账主结算，又有待结算补计单；
        # 数据集以仍开放的结算为主视图，没有开放结算时才回落到已出账结算。
        open_settlements = [row for row in own if not row.get("frozen")]
        primary = open_settlements[0] if open_settlements else own[-1]
        rows = sorted(
            [
                row for row in store.rows(LEDGER)
                if row.get("业务域") == viewer
                and row.get("周期编号") == period_id
            ],
            key=lambda row: str(row.get("访问时间", "")),
        )
        dataset = self._dataset_payload(
            viewer, period, primary, rows,
            settlements=own, scope="当期" if open_settlements else "历史",
        )
        return dataset

    def _empty_dataset(self, domain: str, period: dict[str, Any]) -> dict[str, Any]:
        return {
            "业务域": domain,
            "周期编号": period.get("周期编号"),
            "周期名称": period.get("周期名称"),
            "数据集类型": "历史" if period.get("状态") == "已结算" else "当期",
            "周期状态": period.get("状态"),
            "结算": None,
            "结算单": [],
            "总量": {
                "访问条数": 0,
                "记录数合计": 0,
                "湖表数": 0,
                "归属冲突条数": 0,
            },
            "按表聚合": [],
            "明细": [],
            "冲突": [],
        }

    def _dataset_payload(
        self,
        domain: str,
        period: dict[str, Any],
        settlement: dict[str, Any],
        rows: list[dict[str, Any]],
        *,
        settlements: list[dict[str, Any]] | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        by_table: dict[str, dict[str, Any]] = {}
        for row in rows:
            table_name = str(row.get("目标湖表", ""))
            bucket = by_table.setdefault(table_name, {
                "目标湖表": table_name,
                "表标识": row.get("表标识"),
                "业务域": row.get("业务域"),
                "访问条数": 0,
                "记录数合计": 0,
                "归属记录编号": set(),
                "归属依据": set(),
                "归属冲突": False,
            })
            bucket["访问条数"] += 1
            bucket["记录数合计"] += int(row.get("返回记录数", 0))
            bucket["归属记录编号"].add(str(row.get("归属记录编号", "")))
            bucket["归属依据"].add(str(row.get("归属依据", "")))
            bucket["归属冲突"] = bucket["归属冲突"] or bool(row.get("归属冲突"))

        for bucket in by_table.values():
            bucket["归属记录编号"] = sorted(bucket["归属记录编号"])
            bucket["归属依据"] = sorted(bucket["归属依据"])

        conflicts = [
            {
                "访问编号": row.get("访问编号"),
                "目标湖表": row.get("目标湖表"),
                "归属业务域": row.get("业务域"),
                "当前归属业务域": row.get("当前归属业务域"),
                "归属记录编号": row.get("归属记录编号"),
                "归属生效时间": row.get("归属生效时间"),
                "冲突说明": row.get("冲突说明"),
            }
            for row in rows if row.get("归属冲突")
        ]

        frozen = bool(settlement.get("frozen"))
        settlements = settlements or [settlement]
        dataset_type = scope or ("历史" if frozen else "当期")
        return {
            "业务域": domain,
            "周期编号": period.get("周期编号"),
            "周期名称": period.get("周期名称"),
            "数据集类型": dataset_type,
            "周期状态": period.get("状态"),
            "结算": {
                "结算编号": settlement.get("结算编号"),
                "状态": settlement.get("状态"),
                "结算状态": settlement.get("结算状态"),
                "生成时间": settlement.get("生成时间"),
                "结算时间": settlement.get("结算时间"),
                "冻结": frozen,
            },
            "结算单": [
                {
                    "结算编号": row.get("结算编号"),
                    "状态": row.get("状态"),
                    "结算状态": row.get("结算状态"),
                    "冻结": bool(row.get("frozen")),
                    "访问条数": row.get("访问条数", 0),
                    "记录数合计": row.get("记录数合计", 0),
                }
                for row in settlements
            ],
            "总量": {
                "访问条数": len(rows),
                "记录数合计": sum(int(row.get("返回记录数", 0)) for row in rows),
                "湖表数": len({str(row.get("目标湖表", "")) for row in rows}),
                "归属冲突条数": len(conflicts),
            },
            "按表聚合": sorted(
                by_table.values(), key=lambda item: str(item["目标湖表"])
            ),
            "明细": rows,
            "冲突": conflicts,
        }

    # ---------- 复核与越权防护 ----------

    def refresh_all_periods(self) -> None:
        """重算所有周期的结算汇总，保证历史账的冲突标记也跟着当前归属更新。"""
        for period in store.rows(PERIODS):
            self.materialize(str(period.get("周期编号")))

    def list_settlements(self, domain: str) -> dict[str, Any]:
        """业务域查看自己的结算统计；不含任何明细字段。"""
        viewer = str(domain or "").strip()
        if not viewer:
            raise DomainScopeError("必须指定业务域后才能查看结算统计")
        self.refresh_all_periods()

        rows = [
            row for row in store.rows(SETTLEMENTS)
            if row.get("业务域") == viewer
        ]
        items = []
        for settlement in rows:
            ledger_rows = [
                row for row in store.rows(LEDGER)
                if row.get("结算编号") == settlement.get("结算编号")
            ]
            by_table: dict[str, dict[str, Any]] = {}
            for row in ledger_rows:
                name = str(row.get("目标湖表", ""))
                bucket = by_table.setdefault(name, {
                    "目标湖表": name,
                    "表标识": row.get("表标识"),
                    "访问条数": 0,
                    "记录数合计": 0,
                    "归属冲突": False,
                })
                bucket["访问条数"] += 1
                bucket["记录数合计"] += int(row.get("返回记录数", 0))
                bucket["归属冲突"] = bucket["归属冲突"] or bool(row.get("归属冲突"))
            items.append({
                "结算编号": settlement.get("结算编号"),
                "周期编号": settlement.get("周期编号"),
                "业务域": settlement.get("业务域"),
                "数据集类型": "历史" if settlement.get("frozen") else "当期",
                "状态": settlement.get("状态"),
                "结算状态": settlement.get("结算状态"),
                "结算时间": settlement.get("结算时间"),
                "冻结": bool(settlement.get("frozen")),
                "访问条数": settlement.get("访问条数", len(ledger_rows)),
                "记录数合计": settlement.get("记录数合计", 0),
                "湖表数": settlement.get("湖表数", len(by_table)),
                "冲突条数": settlement.get("冲突条数", 0),
                "按表聚合": sorted(
                    by_table.values(), key=lambda value: str(value["目标湖表"])
                ),
            })
        items.sort(key=lambda item: str(item["周期编号"]))
        return {
            "业务域": viewer,
            "结算单数": len(items),
            "items": items,
        }

    def get_settlement_details(
        self, settlement_no: str, domain: str
    ) -> dict[str, Any]:
        """从本域结算统计追到相关访问；其他业务域一律拒绝。"""
        viewer = str(domain or "").strip()
        if not viewer:
            raise DomainScopeError("必须指定业务域后才能查看结算明细")
        settlement = self._find_settlement(settlement_no)
        if settlement is None:
            raise NotFoundError(f"结算单 {settlement_no} 不存在")
        if settlement.get("业务域") != viewer:
            raise DomainScopeError(
                f"业务域 {viewer} 无权查看该结算单的访问明细"
            )
        period_id = str(settlement.get("周期编号"))
        self.materialize(period_id)
        rows = sorted(
            [
                row for row in store.rows(LEDGER)
                if row.get("结算编号") == settlement_no
            ],
            key=lambda row: str(row.get("访问时间", "")),
        )
        period = self.find_period(period_id) or {}
        dataset = self._dataset_payload(viewer, period, settlement, rows)
        dataset["结算单"] = [{
            "结算编号": settlement.get("结算编号"),
            "状态": settlement.get("状态"),
            "结算状态": settlement.get("结算状态"),
            "冻结": bool(settlement.get("frozen")),
            "访问条数": settlement.get("访问条数", len(rows)),
            "记录数合计": settlement.get("记录数合计", 0),
        }]
        return dataset

    def trace_access(self, access_no: str, domain: str) -> dict[str, Any]:
        """按访问编号追溯到唯一一条归属对照，并校验业务域权限。"""
        viewer = str(domain or "").strip()
        if not viewer:
            raise DomainScopeError("必须指定业务域后才能追溯访问归属")
        rows = [
            row for row in store.rows(LEDGER)
            if row.get("访问编号") == access_no
        ]
        if not rows:
            raise NotFoundError(f"访问编号 {access_no} 尚未进入归属对照")
        row = rows[0]
        if row.get("业务域") != viewer:
            raise DomainScopeError(f"业务域 {viewer} 无权查看该访问的归属明细")
        return row

    # ---------- 状态流转 ----------

    def refresh_period(self, period_id: str) -> dict[str, Any]:
        result = self.materialize(period_id)
        result["结算单"] = [
            {
                "结算编号": row.get("结算编号"),
                "业务域": row.get("业务域"),
                "状态": row.get("状态"),
                "访问条数": row.get("访问条数"),
                "记录数合计": row.get("记录数合计"),
                "冲突条数": row.get("冲突条数"),
            }
            for row in self._settlements_for_period(period_id)
        ]
        store.log_event("domain_metering_refreshed", "access_audit", "cost_report", {
            "period_id": period_id,
            "added": result["新增对照"],
        })
        return result

    def settle(self, period_id: str, domain: str) -> dict[str, Any]:
        """关闭一个业务域在周期内的所有开放结算，并冻结归属对照。"""
        viewer = str(domain or "").strip()
        period = self.find_period(period_id)
        if period is None:
            raise NotFoundError(f"结算周期 {period_id} 不存在")
        if not viewer:
            raise DomainMeteringError("必须指定要结算的业务域")
        self.materialize(period_id)
        open_settlements = [
            row for row in self._settlements_for(period_id, viewer)
            if not row.get("frozen")
        ]
        if not open_settlements:
            raise NotFoundError(f"业务域 {viewer} 在周期 {period_id} 没有可结算的结算单")

        settled_at = iso(datetime.datetime.now())
        settled_nos = [str(row.get("结算编号")) for row in open_settlements]
        for settlement in open_settlements:
            settlement["状态"] = "已出账"
            settlement["结算状态"] = "已结算"
            settlement["结算时间"] = settled_at
            settlement["frozen"] = True
            for row in store.rows(LEDGER):
                if row.get("结算编号") == settlement["结算编号"]:
                    row["结算状态"] = "已出账"
                    row["冻结"] = True
        self._recompute_settlements(period_id)
        total_records = sum(
            int(row.get("记录数合计", 0)) for row in open_settlements
        )
        store.log_event("domain_settlement_issued", "access_audit", "cost_report", {
            "settlement_nos": settled_nos,
            "period_id": period_id,
            "domain": viewer,
            "record_count": total_records,
            "settled_at": settled_at,
        })
        return {
            "结算编号": settled_nos[0] if len(settled_nos) == 1 else settled_nos,
            "业务域": viewer,
            "周期编号": period_id,
            "状态": "已出账",
            "结算时间": settled_at,
            "访问条数": sum(int(row.get("访问条数", 0)) for row in open_settlements),
            "记录数合计": total_records,
        }

    def open_period(self, period_id: str) -> dict[str, Any]:
        """显式打开一个 YYYY-MM 结算周期，供跨月补计或历史回算使用。"""
        text = str(period_id or "").strip()
        match = re.fullmatch(r"(\d{4})-(\d{2})", text)
        if not match:
            raise DomainMeteringError("周期编号格式应为 YYYY-MM")
        year, month = int(match.group(1)), int(match.group(2))
        if not 1 <= month <= 12:
            raise DomainMeteringError("周期月份必须在 01 到 12 之间")
        existing = self.find_period(text)
        if existing is not None:
            return existing
        return self._ensure_period(datetime.datetime(year, month, 1))

    def list_periods(self) -> list[dict[str, Any]]:
        periods = sorted(
            store.rows(PERIODS), key=lambda row: str(row.get("周期编号", ""))
        )
        result = []
        for period in periods:
            settlements = self._settlements_for_period(str(period.get("周期编号")))
            result.append({
                **period,
                "业务域": sorted({
                    str(row.get("业务域")) for row in settlements
                }),
            })
        return result

    def overview(self) -> dict[str, Any]:
        """两个业务域的汇总口径，供复核入口选择；不返回任何访问明细。"""
        self.refresh_all_periods()
        domains: dict[str, dict[str, Any]] = {}
        for settlement in store.rows(SETTLEMENTS):
            domain = str(settlement.get("业务域", ""))
            bucket = domains.setdefault(domain, {
                "业务域": domain,
                "结算单数": 0,
                "访问条数": 0,
                "记录数合计": 0,
                "湖表数": 0,
                "冲突条数": 0,
                "结算单": [],
            })
            bucket["结算单数"] += 1
            bucket["访问条数"] += int(settlement.get("访问条数", 0))
            bucket["记录数合计"] += int(settlement.get("记录数合计", 0))
            bucket["湖表数"] += int(settlement.get("湖表数", 0))
            bucket["冲突条数"] += int(settlement.get("冲突条数", 0))
            bucket["结算单"].append({
                "结算编号": settlement.get("结算编号"),
                "周期编号": settlement.get("周期编号"),
                "数据集类型": "历史" if settlement.get("frozen") else "当期",
                "状态": settlement.get("状态"),
                "访问条数": settlement.get("访问条数", 0),
                "记录数合计": settlement.get("记录数合计", 0),
                "冲突条数": settlement.get("冲突条数", 0),
            })
        return {
            "业务域数": len(domains),
            "items": sorted(domains.values(), key=lambda item: item["业务域"]),
        }

    def overview_tables(self) -> list[dict[str, Any]]:
        """湖表维度的归属与已计量用量，用于注册页和计量页交叉核对。"""
        tables = []
        for table in store.rows("lake_table"):
            identifier = self.ensure_table_identifier(table)
            self.ensure_ownership(table)
            current = self.current_ownership(identifier)
            ledger_rows = [
                row for row in store.rows(LEDGER)
                if row.get("表标识") == identifier
            ]
            tables.append({
                "湖表ID": table.get("id"),
                "表标识": identifier,
                "表名": table.get("表名"),
                "当前业务域": (
                    current.get("业务域") if current else table.get("所属业务域")
                ),
                "当前归属记录编号": current.get("归属记录编号") if current else None,
                "归属记录数": len([
                    row for row in store.rows(OWNERSHIP)
                    if row.get("表标识") == identifier
                ]),
                "已计量访问条数": len(ledger_rows),
                "已计量记录数": sum(
                    int(row.get("返回记录数", 0)) for row in ledger_rows
                ),
            })
        return tables


service = DomainMeteringService()

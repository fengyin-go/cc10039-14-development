"""分域计量接口：湖表归属、周期结算和按业务域隔离的访问数据集。

所有查询都要求显式传入 `domain`（业务域），服务端按该业务域过滤；
跨域读取结算明细返回 403，且响应里不携带任何明细字段。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.domain_metering import (
    DomainMeteringError,
    DomainScopeError,
    NotFoundError,
    service,
)

router = APIRouter(prefix="/api/metering", tags=["分域计量"])


class OwnershipChangePayload(BaseModel):
    table_id: int
    domain: str
    effective_time: str | None = None
    reason: str | None = None
    operator: str = "湖表注册"


class SettlePayload(BaseModel):
    period: str
    domain: str


class AccessCreatePayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


def _raise(error: Exception) -> None:
    if isinstance(error, DomainScopeError):
        raise HTTPException(status_code=403, detail=str(error)) from error
    if isinstance(error, NotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, DomainMeteringError):
        raise HTTPException(status_code=400, detail=str(error)) from error
    raise error


@router.get("/overview")
def metering_overview() -> dict[str, Any]:
    """业务域口径总览：结算统计 + 湖表归属 + 周期，均不含访问明细。"""
    return {
        "业务域汇总": service.overview(),
        "结算周期": service.list_periods(),
        "湖表归属": service.overview_tables(),
    }


@router.get("/ownership")
def list_ownership(
    table_id: int | None = Query(default=None),
    table_name: str | None = Query(default=None),
    domain: str | None = Query(default=None),
) -> dict[str, Any]:
    """湖表注册产出的业务域归属记录，带表标识和生效区间。"""
    items = service.list_ownership(
        table_id=table_id, table_name=table_name, domain=domain
    )
    return {"total": len(items), "items": items}


@router.post("/ownership/change")
def change_ownership(payload: OwnershipChangePayload) -> dict[str, Any]:
    """变更湖表业务域：关闭旧归属记录并追加新记录。"""
    try:
        record = service.change_ownership(
            payload.table_id,
            domain=payload.domain,
            effective_time=payload.effective_time,
            reason=payload.reason,
            operator=payload.operator,
        )
    except DomainMeteringError as error:
        _raise(error)
    return {"ok": True, "message": "湖表业务域归属已变更", "entry": record}


@router.get("/periods")
def list_periods() -> dict[str, Any]:
    items = service.list_periods()
    return {"total": len(items), "items": items}


@router.post("/periods/{period_id}/refresh")
def refresh_period(period_id: str) -> dict[str, Any]:
    """把周期内新增访问补进归属对照，返回该周期各业务域的汇总。"""
    try:
        service.open_period(period_id)
        result = service.refresh_period(period_id)
    except DomainMeteringError as error:
        _raise(error)
    return {"ok": True, "message": f"周期 {period_id} 计量已刷新", "entry": result}


@router.get("/settlements")
def list_settlements(
    domain: str = Query(..., description="查看者业务域，只能是本域"),
) -> dict[str, Any]:
    """本域结算统计：只返回汇总和按表聚合，不含访问明细。"""
    try:
        return service.list_settlements(domain)
    except DomainMeteringError as error:
        _raise(error)


@router.get("/settlements/{settlement_no}")
def settlement_details(
    settlement_no: str,
    domain: str = Query(..., description="查看者业务域，必须与结算归属一致"),
) -> dict[str, Any]:
    """从本域结算统计追溯到相关访问；跨域读取返回 403。"""
    try:
        return service.get_settlement_details(settlement_no, domain)
    except DomainMeteringError as error:
        _raise(error)


@router.get("/datasets")
def domain_dataset(
    domain: str = Query(..., description="业务域"),
    period: str = Query(..., description="结算周期，例如 2026-08"),
) -> dict[str, Any]:
    """生成指定业务域、指定周期的分域访问数据集。"""
    try:
        return service.build_dataset(domain, period)
    except DomainMeteringError as error:
        _raise(error)


@router.get("/accesses/{access_no}")
def trace_access(
    access_no: str,
    domain: str = Query(..., description="查看者业务域"),
) -> dict[str, Any]:
    """按访问编号追溯唯一归属对照；跨域读取返回 403。"""
    try:
        return service.trace_access(access_no, domain)
    except DomainMeteringError as error:
        _raise(error)


@router.post("/settle")
def settle_period(payload: SettlePayload) -> dict[str, Any]:
    """关闭一个业务域的结算并冻结归属对照。"""
    try:
        result = service.settle(payload.period, payload.domain)
    except DomainMeteringError as error:
        _raise(error)
    return {"ok": True, "message": f"业务域 {payload.domain} 已出账", "entry": result}

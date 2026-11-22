"""分域计量测试：归属时点、结算唯一性、越权隔离和转域冲突。

每个用例使用独立的数据快照，避免全局 store 单例在测试间互相污染。
"""
from __future__ import annotations

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.store import store
from app.services import domain_metering
from app.services.domain_metering import (
    DomainMeteringError,
    DomainScopeError,
    DomainMeteringService,
    NotFoundError,
)


TABLE = "订单明细湖表"
TABLE_ID = 4
HISTORY_PERIOD = "2026-08"
CURRENT_PERIOD = "2026-09"
OLD_DOMAIN = "交易域"
NEW_DOMAIN = "风控域"


@pytest.fixture()
def isolated_store():
    """备份并恢复 store，让每个用例从同一份种子数据出发。"""
    tables = copy.deepcopy(store._tables)
    events = copy.deepcopy(store._events)
    snapshots = copy.deepcopy(store._snapshots)
    yield DomainMeteringService()
    store._tables = tables
    store._events = events
    store._snapshots = snapshots


def _accesses(access_no: str) -> list[dict]:
    return [
        row for row in store.rows("access_audit")
        if row.get("访问编号") == access_no
    ]


class TestOwnership:
    def test_registration_carries_table_identifier(self, isolated_store):
        svc = isolated_store
        table = store.find("lake_table", TABLE_ID)
        record = svc.register_ownership(table)
        assert record["表标识"] == "LT-0004"
        assert record["归属记录编号"]
        assert record["生效时间"]

    def test_ownership_at_uses_access_time(self, isolated_store):
        svc = isolated_store
        before = svc.attribute_access(_accesses("ACC-202608-001")[0])
        after = svc.attribute_access(_accesses("ACC-202609-001")[0])
        assert before["业务域"] == OLD_DOMAIN
        assert after["业务域"] == NEW_DOMAIN
        assert before["归属记录编号"] != after["归属记录编号"]

    def test_change_ownership_closes_previous_record(self, isolated_store):
        svc = isolated_store
        table = store.find("lake_table", TABLE_ID)
        svc.change_ownership(
            TABLE_ID,
            domain="营销域",
            effective_time="2026-10-01T00:00:00",
            reason="测试转域",
        )
        records = svc.list_ownership(table_id=TABLE_ID)
        open_records = [row for row in records if row.get("失效时间") is None]
        assert len(open_records) == 1
        assert open_records[0]["业务域"] == "营销域"
        assert table["所属业务域"] == "营销域"
        previous = [row for row in records if row["业务域"] == NEW_DOMAIN][0]
        assert previous["失效时间"] == "2026-10-01T00:00:00"

    def test_change_ownership_rejects_non_increasing_effective_time(self, isolated_store):
        svc = isolated_store
        with pytest.raises(DomainMeteringError):
            svc.change_ownership(
                TABLE_ID,
                domain="营销域",
                effective_time="2026-08-01T00:00:00",
            )

    def test_ownership_history_is_reviewable(self, isolated_store):
        svc = isolated_store
        records = svc.list_ownership(table_id=TABLE_ID)
        assert [row["业务域"] for row in records] == [OLD_DOMAIN, NEW_DOMAIN]
        assert all(row["归属记录编号"] for row in records)
        assert records[0]["失效时间"] == records[1]["生效时间"]


class TestDatasets:
    def test_history_dataset_belongs_to_old_domain(self, isolated_store):
        svc = isolated_store
        dataset = svc.build_dataset(OLD_DOMAIN, HISTORY_PERIOD)
        assert dataset["数据集类型"] == "历史"
        assert dataset["总量"]["访问条数"] == 2
        assert dataset["总量"]["记录数合计"] == 200
        assert {row["业务域"] for row in dataset["明细"]} == {OLD_DOMAIN}
        assert {row["归属记录编号"] for row in dataset["明细"]} == {"OWN-0001"}

    def test_current_dataset_belongs_to_new_domain(self, isolated_store):
        svc = isolated_store
        dataset = svc.build_dataset(NEW_DOMAIN, CURRENT_PERIOD)
        assert dataset["数据集类型"] == "当期"
        assert dataset["总量"]["访问条数"] == 2
        assert dataset["总量"]["记录数合计"] == 200
        assert {row["业务域"] for row in dataset["明细"]} == {NEW_DOMAIN}
        assert {row["归属记录编号"] for row in dataset["明细"]} == {"OWN-0002"}

    def test_table_aggregate_matches_detail_sum(self, isolated_store):
        svc = isolated_store
        for domain, period in [
            (OLD_DOMAIN, HISTORY_PERIOD),
            (NEW_DOMAIN, CURRENT_PERIOD),
        ]:
            dataset = svc.build_dataset(domain, period)
            detail_total = sum(row["返回记录数"] for row in dataset["明细"])
            aggregate = [
                row for row in dataset["按表聚合"] if row["目标湖表"] == TABLE
            ]
            assert len(aggregate) == 1
            assert aggregate[0]["记录数合计"] == detail_total
            assert aggregate[0]["访问条数"] == len(dataset["明细"])
            assert aggregate[0]["记录数合计"] == dataset["总量"]["记录数合计"]

    def test_returned_record_count_drives_usage(self, isolated_store):
        svc = isolated_store
        dataset = svc.build_dataset(NEW_DOMAIN, CURRENT_PERIOD)
        counts = sorted(row["返回记录数"] for row in dataset["明细"])
        assert counts == [50, 150]
        assert all(row["计量口径"] == "按访问实际返回记录数" for row in dataset["明细"])

    def test_conflict_is_annotated_on_old_domain_only(self, isolated_store):
        svc = isolated_store
        history = svc.build_dataset(OLD_DOMAIN, HISTORY_PERIOD)
        current = svc.build_dataset(NEW_DOMAIN, CURRENT_PERIOD)
        assert history["总量"]["归属冲突条数"] == 2
        assert current["总量"]["归属冲突条数"] == 0
        for row in history["冲突"]:
            assert row["归属业务域"] == OLD_DOMAIN
            assert row["当前归属业务域"] == NEW_DOMAIN

    def test_other_domain_period_returns_empty_dataset(self, isolated_store):
        svc = isolated_store
        dataset = svc.build_dataset(OLD_DOMAIN, CURRENT_PERIOD)
        assert dataset["明细"] == []
        assert dataset["总量"]["记录数合计"] == 0


class TestSettlementUniqueness:
    def test_each_access_is_metered_once(self, isolated_store):
        svc = isolated_store
        svc.materialize(HISTORY_PERIOD)
        svc.materialize(CURRENT_PERIOD)
        access_nos = [row["访问编号"] for row in store.rows("settlement_ledger")]
        assert len(access_nos) == len(set(access_nos))

    def test_transfer_after_settlement_does_not_move_old_usage(self, isolated_store):
        svc = isolated_store
        history_before = svc.build_dataset(OLD_DOMAIN, HISTORY_PERIOD)
        record_counts_before = history_before["总量"]["记录数合计"]

        svc.change_ownership(
            TABLE_ID,
            domain="营销域",
            effective_time="2026-10-01T00:00:00",
            reason="二次转域",
        )
        svc.materialize(HISTORY_PERIOD)
        svc.materialize(CURRENT_PERIOD)

        history_after = svc.build_dataset(OLD_DOMAIN, HISTORY_PERIOD)
        assert history_after["总量"]["记录数合计"] == record_counts_before
        assert {row["业务域"] for row in history_after["明细"]} == {OLD_DOMAIN}

        # 当期访问仍归风控域（访问时点归属），不会被营销域重复计量。
        current = svc.build_dataset(NEW_DOMAIN, CURRENT_PERIOD)
        assert current["总量"]["记录数合计"] == 200
        marketing = svc.build_dataset("营销域", CURRENT_PERIOD)
        assert marketing["总量"]["记录数合计"] == 0

    def test_settle_freezes_and_is_idempotent(self, isolated_store):
        svc = isolated_store
        result = svc.settle(CURRENT_PERIOD, NEW_DOMAIN)
        assert result["状态"] == "已出账"
        dataset = svc.build_dataset(NEW_DOMAIN, CURRENT_PERIOD)
        assert dataset["数据集类型"] == "历史"
        assert dataset["结算"]["冻结"] is True
        with pytest.raises(DomainMeteringError):
            svc.settle(CURRENT_PERIOD, NEW_DOMAIN)

    def test_new_access_after_settlement_goes_to_supplement(self, isolated_store):
        svc = isolated_store
        svc.settle(CURRENT_PERIOD, NEW_DOMAIN)
        store.rows("access_audit").append({
            "id": 99,
            "status": "已记录",
            "pending": False,
            "abnormal": False,
            "访问编号": "ACC-202609-099",
            "访问者": "风控引擎",
            "目标湖表": TABLE,
            "操作类型": "查询",
            "访问时间": "2026-09-28T09:00:00",
            "返回记录数": 30,
        })
        svc.materialize(CURRENT_PERIOD)
        dataset = svc.build_dataset(NEW_DOMAIN, CURRENT_PERIOD)
        assert dataset["总量"]["记录数合计"] == 230
        assert sum(
            row["记录数合计"] for row in dataset["结算单"]
        ) == 230
        # 每条访问仍只对应一条归属对照。
        numbers = [row["访问编号"] for row in store.rows("settlement_ledger")]
        assert len(numbers) == len(set(numbers))


class TestDomainIsolation:
    def test_settlement_statistics_scoped_to_domain(self, isolated_store):
        svc = isolated_store
        own = svc.list_settlements(OLD_DOMAIN)
        assert all(item["业务域"] == OLD_DOMAIN for item in own["items"])
        assert len(own["items"]) == 1
        leaked = svc.list_settlements(NEW_DOMAIN)
        assert {item["业务域"] for item in leaked["items"]} == {NEW_DOMAIN}

    def test_cross_domain_settlement_details_denied(self, isolated_store):
        svc = isolated_store
        with pytest.raises(DomainScopeError):
            svc.get_settlement_details("ST-2026-08-交易域", NEW_DOMAIN)

    def test_cross_domain_access_trace_denied(self, isolated_store):
        svc = isolated_store
        svc.materialize(HISTORY_PERIOD)
        row = svc.trace_access("ACC-202608-001", OLD_DOMAIN)
        assert row["业务域"] == OLD_DOMAIN
        with pytest.raises(DomainScopeError):
            svc.trace_access("ACC-202608-001", NEW_DOMAIN)

    def test_unknown_settlement_returns_not_found(self, isolated_store):
        svc = isolated_store
        with pytest.raises(NotFoundError):
            svc.get_settlement_details("ST-UNKNOWN", OLD_DOMAIN)

    def test_settlement_details_traceable_to_accesses(self, isolated_store):
        svc = isolated_store
        stats = svc.list_settlements(OLD_DOMAIN)
        settlement_no = stats["items"][0]["结算编号"]
        details = svc.get_settlement_details(settlement_no, OLD_DOMAIN)
        assert [row["访问编号"] for row in details["明细"]] == [
            "ACC-202608-001", "ACC-202608-002",
        ]
        assert details["总量"]["记录数合计"] == 200


class TestApiContract:
    """接口级契约：越权返回 403，正常返回 200 且字段可读。

    当前环境没有 httpx/httpx2，TestClient 不可用，这里直接驱动 ASGI 应用。
    """

    @pytest.fixture()
    def call(self, isolated_store):
        import asyncio
        import json
        from urllib.parse import urlencode

        from app.main import app

        def _call(method: str, path: str, params: dict[str, str] | None = None):
            raw_query = urlencode(params or {}).encode()
            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "root_path": "",
                "query_string": raw_query,
                "headers": [(b"host", b"testserver")],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
            }
            messages: list[dict] = []
            finished = False

            async def receive():
                nonlocal finished
                if finished:
                    return {"type": "http.disconnect"}
                finished = True
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                messages.append(message)

            asyncio.run(app(scope, receive, send))
            status = next(
                m["status"] for m in messages if m["type"] == "http.response.start"
            )
            body = b"".join(
                m.get("body", b"") for m in messages if m["type"] == "http.response.body"
            )
            text = body.decode("utf-8")
            try:
                payload = json.loads(text)
            except ValueError:
                payload = None
            return status, payload, text

        return _call

    def test_dataset_endpoint(self, call):
        status, payload, _ = call(
            "GET",
            "/api/metering/datasets",
            {"domain": OLD_DOMAIN, "period": HISTORY_PERIOD},
        )
        assert status == 200
        assert payload["总量"]["记录数合计"] == 200
        assert payload["按表聚合"][0]["目标湖表"] == TABLE

    def test_cross_domain_settlement_endpoint_denied(self, call):
        status, payload, text = call(
            "GET",
            "/api/metering/settlements/ST-2026-08-交易域",
            {"domain": NEW_DOMAIN},
        )
        assert status == 403
        # 拒绝响应只包含一句权限说明，不携带访问编号、湖表或记录数。
        assert "ACC-202608-001" not in text
        assert TABLE not in text
        assert "120" not in text
        assert "OWN-0001" not in text

    def test_missing_domain_denied(self, call):
        status, _, _ = call("GET", "/api/metering/settlements")
        assert status == 422

    def test_cross_module_endpoints_still_reachable(self, call):
        """新增固定前缀路由后，/api/cross/* 不能被 /api/{module} 抢占。"""
        assert call("GET", "/api/cross/events")[0] == 200
        assert call("GET", "/api/cross/pipeline-status")[0] == 200

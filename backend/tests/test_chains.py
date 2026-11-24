"""5条跨模块业务链路测试。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.routers.chains import (
    run_crawl_pipeline,
    run_data_process,
    run_quote_pipeline,
    run_reliability,
    run_auth,
    run_delivery,
)

class TestCrawlPipeline:
    def test_basic(self):
        result = run_crawl_pipeline({
            "config_id": 1,
            "config_name": "test-cfg",
            "period": "daily",
            "batch_label": "B-CP-1",
            "row_count": 100,
            "valid_count": 95,
        })
        assert result["chain"] == "crawl-pipeline"
        steps = result["steps"]
        assert steps["validate_config"]["config_id"] == 1
        assert steps["create_schedule"]["period"] == "daily"
        assert steps["track_batch"]["status"] == "执行中"
        assert steps["store_result"]["原始行数"] == "100"

class TestDataProcess:
    def test_basic(self):
        html = "<html><body><h1>Hello</h1><p>World</p></body></html>"
        result = run_data_process({
            "html": html,
            "source_data": {"name": "Alice", "count": "42"},
            "mapping_rules": [
                {"source": "name", "target": "title", "default": "", "transform": ""},
                {"source": "count", "target": "total", "default": "0", "transform": "int"},
            ],
            "key_fields": ["title"],
        })
        assert result["chain"] == "data-process"
        steps = result["steps"]
        assert steps["parse_html"]["field_count"] >= 1
        assert steps["map_fields"]["title"] == "Alice"
        assert steps["clean_html"]["cleaned_length"] > 0
        assert "dedup" in steps

class TestReliability:
    def test_basic(self):
        result = run_reliability({
            "site": "example.com",
            "protocol": "http",
            "request_count": 100,
            "error_type": "timeout",
            "attempt": 2,
        })
        assert result["chain"] == "reliability"
        steps = result["steps"]
        assert "allocate_proxy" in steps
        assert steps["check_rate"]["site"] == "example.com"
        assert steps["retry_calc"]["error_type"] == "timeout"
        assert steps["log_error"]["error_code"] == "ERR-CHECK"

class TestAuth:
    def test_basic(self):
        result = run_auth({
            "site": "test-auth.com",
            "auth_type": "token",
            "session_type": "http",
        })
        assert result["chain"] == "auth"
        steps = result["steps"]
        assert steps["get_cookies"]["site"] == "test-auth.com"
        assert steps["authenticate"]["authenticated"] is True
        assert steps["create_session"]["status"] == "活跃"

class TestDelivery:
    def test_basic(self):
        result = run_delivery({
            "batch_id": "B-DEL-1",
            "config_name": "delivery-cfg",
            "export_format": "csv",
            "fields": ["id", "title", "count"],
            "cleanup_module": "result_store",
        })
        assert result["chain"] == "delivery"
        steps = result["steps"]
        assert steps["store_result"]["批次标识"] == "B-DEL-1"
        assert steps["export"]["导出格式"] == "csv"
        assert steps["audit"]["操作类型"] == "export"
        assert steps["cleanup"]["任务标识"] == "CLEANUP-B-DEL-1"

class TestQuotePipeline:
    def test_partial_failure_keeps_complete_page(self):
        from app.services import quote_ledger as ledger_module

        ledger_module.quote_ledger.reset()
        result = run_quote_pipeline({"page_numbers": [4, 7]})
        assert result["chain"] == "quote-pipeline"
        parse_step = result["steps"]["parse_pages"]
        assert parse_step["status"] == "部分成功"
        pages = {page["page"]: page for page in parse_step["pages"]}
        assert pages[4]["status"] == "已解析"
        assert pages[7]["pending_positions"] == [3]
        map_step = result["steps"]["map_records"]
        assert map_step["counts"]["accepted"] == 5
        assert map_step["counts"]["pending"] == 1
        assert result["results"]["counts"]["pending"] == 1
        ledger_module.quote_ledger.reset()

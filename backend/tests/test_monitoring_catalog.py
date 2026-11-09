from typing import get_args

import pytest
from test_resources_alerts import isolated_secrets as isolated_secrets
from test_resources_alerts import server_body as server_body
from test_resources_alerts import server_id as server_id

from app.schemas.resources import Metric, RuleInput
from app.services.monitoring.config import rule_comparison
from app.services.monitoring.metrics import metric_availability, metric_expression


@pytest.mark.parametrize("metric", get_args(Metric))
def test_fixed_catalog_has_query_and_rule_support(metric):
    expression = metric_expression(metric)
    assert expression and "{" in expression
    threshold = 1 if metric.endswith("_up") or metric == "target_up" else 0
    RuleInput(code="CatalogCheck", metric=metric, threshold=threshold)
    assert rule_comparison(metric) == (
        "<"
        if metric
        in (
            "target_up",
            "http_up",
            "container_up",
            "host_memory_available_bytes",
            "host_disk_available_bytes",
        )
        else ">"
    )


def test_missing_swap_and_idle_io_are_filtered_not_zero():
    assert "SwapTotal_bytes" in metric_expression("host_swap_percent")
    assert "> 0" in metric_expression("host_swap_percent")
    assert "> 0" in metric_expression("host_disk_io_wait_seconds")
    result = metric_availability(
        {"resultType": "vector", "result": [{"metric": {}, "value": [1, "NaN"]}]}
    )
    assert result["availability"] == "unavailable" and result["result"] == []


def test_optional_collector_is_explicitly_unconfigured(client, admin_headers, server_id):
    for metric in ("container_restarts_5m", "container_oom_5m", "container_unhealthy"):
        response = client.get(
            "/api/v1/servers/" + server_id + "/metrics",
            headers=admin_headers,
            params={"metric": metric},
        )
        assert response.status_code == 200
        assert response.json()["availability"] == "unconfigured"
        assert response.json()["result"] == []

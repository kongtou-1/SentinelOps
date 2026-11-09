from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, HistogramMetricFamily

from app.repositories import usage as repository


def metrics(db):
    families = []
    business = CounterMetricFamily(
        "sentinelops_business_requests_total",
        "Explicit workflow results",
        labels=["operation", "outcome", "traffic"],
    )
    for operation, outcome, count in repository.business_counts(db):
        business.add_metric([operation, outcome, "business"], count)
    families.append(business)
    labels = ["operation", "model", "traffic", "usage_source"]
    requests = CounterMetricFamily(
        "sentinelops_llm_requests_total", "Platform provider calls", labels=labels + ["outcome"]
    )
    tokens = CounterMetricFamily(
        "sentinelops_llm_tokens_total", "Provider-reported tokens", labels=labels + ["kind"]
    )
    availability = CounterMetricFamily(
        "sentinelops_llm_usage_observations_total",
        "Usage availability",
        labels=labels + ["availability"],
    )
    totals = {}
    for (
        model,
        purpose,
        outcome,
        count,
        input_tokens,
        output_tokens,
        unknown,
    ) in repository.metrics_groups(db):
        values = [purpose, model, "probe" if purpose == "probe" else "business", "platform_gateway"]
        requests.add_metric([*values, outcome], count)
        total = totals.setdefault((model, purpose), [0, 0, 0, 0])
        for i, value in enumerate((count, input_tokens, output_tokens, unknown)):
            total[i] += value or 0
    for (model, purpose), (count, input_tokens, output_tokens, unknown) in totals.items():
        values = [purpose, model, "probe" if purpose == "probe" else "business", "platform_gateway"]
        tokens.add_metric([*values, "input"], input_tokens)
        tokens.add_metric([*values, "output"], output_tokens)
        availability.add_metric([*values, "known"], count - unknown)
        availability.add_metric([*values, "unknown"], unknown)
    families.extend([requests, tokens, availability])
    bounds = [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 180]
    for field, name in (
        ("duration_seconds", "sentinelops_llm_duration_seconds"),
        ("ttft_seconds", "sentinelops_llm_ttft_seconds"),
    ):
        family = HistogramMetricFamily(name, "Observed provider timing", labels=labels)
        for model, purpose, count, total, *counts in repository.histogram_groups(db, field, bounds):
            family.add_metric(
                [purpose, model, "probe" if purpose == "probe" else "business", "platform_gateway"],
                [[str(bound), value] for bound, value in zip(bounds, counts, strict=True)]
                + [["+Inf", count]],
                total,
            )
        families.append(family)

    class Collector:
        def collect(self):
            return iter(families)

    registry = CollectorRegistry()
    registry.register(Collector())
    return generate_latest(registry)

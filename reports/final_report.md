# Day 10 Reliability Final Report

## Metrics Summary

| Metric | Value |
|---|---:|
| availability | 0.995 |
| cache_hit_rate | 0.71 |
| circuit_open_count | 3 |
| error_rate | 0.005 |
| estimated_cost | 0.026878 |
| estimated_cost_saved | 0.142 |
| fallback_success_rate | 0.9655 |
| latency_p50_ms | 0.23 |
| latency_p95_ms | 503.04 |
| latency_p99_ms | 526.96 |
| recovery_time_ms | None |
| total_requests | 200 |

## Chaos Scenarios

| Scenario | Status |
|---|---|
| primary_timeout_100 | pass |
| primary_flaky_50 | pass |
| cache_stale_candidate | pass |
| cache_latency_ab | pass |
| all_healthy | pass |

## Cache comparison (JSON)

```json
{
  "cache_latency_ab": {
    "without_cache": {
      "total_requests": 40,
      "availability": 0.95,
      "error_rate": 0.05,
      "latency_p50_ms": 282.93,
      "latency_p95_ms": 515.41,
      "latency_p99_ms": 534.1,
      "fallback_success_rate": 0.9259,
      "cache_hit_rate": 0.0,
      "circuit_open_count": 3,
      "recovery_time_ms": 2222.0098972320557,
      "estimated_cost": 0.016798,
      "estimated_cost_saved": 0.0,
      "scenarios": {}
    },
    "with_cache": {
      "total_requests": 40,
      "availability": 1.0,
      "error_rate": 0.0,
      "latency_p50_ms": 0.31,
      "latency_p95_ms": 504.31,
      "latency_p99_ms": 535.96,
      "fallback_success_rate": 1.0,
      "cache_hit_rate": 0.65,
      "circuit_open_count": 0,
      "recovery_time_ms": null,
      "estimated_cost": 0.007206,
      "estimated_cost_saved": 0.026,
      "scenarios": {}
    }
  }
}
```

## Analysis

Summarize failures, fallback behavior, and production changes you would make next.
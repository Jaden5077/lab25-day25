from __future__ import annotations

import argparse

from reliability_lab.chaos import load_queries, run_simulation
from reliability_lab.config import load_config
from reliability_lab.redis_env import describe_redis_connection, effective_redis_url, redis_url_source


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--out", default="reports/metrics.json")
    args = parser.parse_args()
    config = load_config(args.config)
    if config.cache.backend == "redis":
        url = effective_redis_url(config.cache)
        print(f"redis_url source={redis_url_source()} url={url!r}")
        print(describe_redis_connection(url))
    metrics = run_simulation(config, load_queries())
    metrics.write_json(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

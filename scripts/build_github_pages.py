#!/usr/bin/env python3
"""生成 GitHub Pages 静态站点及公开 JSON API 文件。"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    output = root / "_site"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    payload = json.loads(
        (root / "data" / "dashboard_api.json").read_text(encoding="utf-8")
    )
    shutil.copy2(root / "dashboard.html", output / "index.html")
    (output / ".nojekyll").write_text("", encoding="utf-8")

    endpoints = {
        "dashboard.json": payload,
        "pricing.json": {"meta": payload["meta"], "rows": payload["pricing"]},
        "business.json": {"meta": payload["meta"], "rows": payload["business"]},
        "tokens.json": {"meta": payload["meta"], "rows": payload["tokens"]},
        "sources.json": {"meta": payload["meta"], "rows": payload["sources"]},
        "signals.json": {"meta": payload["meta"], "rows": payload["signals"]},
        "history.json": {
            "meta": payload["meta"],
            "series": payload["history_series"],
        },
        "health.json": {
            "status": "ok",
            "snapshot_at": payload["meta"]["generated_at"],
            "metrics_as_of": payload["meta"]["metrics_as_of"],
            "source_success": payload["kpis"]["source_success"],
            "delivery": "github-pages-static-snapshot",
        },
    }
    api_root = output / "api"
    for filename, value in endpoints.items():
        write_json(api_root / filename, value)

    write_json(
        api_root / "index.json",
        {
            "name": "LLM Token Economy Monitor API",
            "snapshot_at": payload["meta"]["generated_at"],
            "refresh_schedule": "Monday and Friday 00:30 UTC / 08:30 Asia/Shanghai",
            "endpoints": [f"./{name}" for name in endpoints],
            "note": "GitHub Pages 提供版本化静态 JSON；来源状态为最近一次 Actions 更新结果。",
        },
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

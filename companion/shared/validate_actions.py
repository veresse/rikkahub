"""动作库校验 CLI。

用法：
    python -m companion.shared.validate_actions
    python -m companion.shared.validate_actions --catalog path/to/actions.json

退出码：0 = 通过；1 = 校验失败。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from companion.shared.catalog import CATALOG_PATH, SCHEMA_PATH, load_catalog, validate_catalog


def validate_schema(catalog: dict, schema_path: Path) -> list[str]:
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - 仅在没有安装 jsonschema 时触发
        return ["未安装 jsonschema，跳过 JSON Schema 校验（请 pip install jsonschema）"]

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    validator = jsonschema.Draft202012Validator(schema)
    return [f"schema: {e.message} @ {list(e.absolute_path)}" for e in validator.iter_errors(catalog)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 companion/shared/actions.json")
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--schema", type=Path, default=SCHEMA_PATH)
    args = parser.parse_args(argv)

    catalog = load_catalog(args.catalog)
    errors = validate_schema(catalog, args.schema) + validate_catalog(catalog)

    if errors:
        print(f"FAILED: {len(errors)} error(s)")
        for e in errors:
            print(f"  - {e}")
        return 1

    print(
        f"OK: actions={len(catalog['actions'])} version={catalog['version']} "
        f"default_max={catalog['limits']['default_max_duration_ms']}ms "
        f"hard_max={catalog['limits']['hard_max_duration_ms']}ms"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

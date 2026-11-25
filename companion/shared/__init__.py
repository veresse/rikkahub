"""companion.shared: 三端共享契约（动作库、协议常量）。"""

from companion.shared.catalog import (
    CATALOG_PATH,
    SCHEMA_PATH,
    CatalogError,
    build_llm_action_menu,
    estimate_duration_ms,
    get_action,
    get_action_names,
    get_catalog,
    load_catalog,
    resolve_params,
    validate_catalog,
)

__all__ = [
    "CATALOG_PATH",
    "SCHEMA_PATH",
    "CatalogError",
    "build_llm_action_menu",
    "estimate_duration_ms",
    "get_action",
    "get_action_names",
    "get_catalog",
    "load_catalog",
    "resolve_params",
    "validate_catalog",
]

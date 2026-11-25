"""动作库契约测试（Phase 1）。

目标：任何一端（Server/App/Firmware）改动 actions.json 时，都能在 CI 中
立刻发现结构、范围或档位语义被破坏。
"""

import copy
import json

import pytest

from companion.shared.catalog import (
    CATALOG_PATH,
    SCHEMA_PATH,
    CatalogError,
    build_llm_action_menu,
    estimate_duration_ms,
    get_action,
    get_action_names,
    load_catalog,
    resolve_params,
    validate_catalog,
)


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def test_catalog_matches_json_schema(catalog):
    jsonschema = pytest.importorskip("jsonschema")
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = json.load(f)
    jsonschema.Draft202012Validator(schema).validate(catalog)


def test_catalog_is_valid(catalog):
    assert validate_catalog(catalog) == []


def test_exactly_eighteen_actions(catalog):
    names = get_action_names()
    assert len(names) == 18
    assert len(set(names)) == 18


def test_action_names_are_stable(catalog):
    expected = {
        "home", "nod", "shake", "look_left", "look_right", "look_up", "look_down",
        "look_around", "bow", "peek", "surprised", "sleep", "wake", "panic",
        "swing", "tilt_left", "tilt_right", "dance",
    }
    assert set(get_action_names()) == expected


def test_angle_limits_match_contract(catalog):
    limits = catalog["limits"]
    assert (limits["pan"]["min"], limits["pan"]["max"]) == (-90.0, 90.0)
    assert (limits["tilt"]["min"], limits["tilt"]["max"]) == (-20.0, 45.0)
    assert limits["volume"]["min"] == 0
    assert limits["volume"]["max"] == 100


def test_duration_contract(catalog):
    limits = catalog["limits"]
    assert limits["default_max_duration_ms"] == 3000
    assert limits["hard_max_duration_ms"] == 5000

    for action in catalog["actions"]:
        max_ms = action["max_duration_ms"]
        if action["name"] == "dance":
            assert 3000 < max_ms <= 5000
        else:
            assert max_ms <= 3000
        assert estimate_duration_ms(action) <= max_ms


def test_look_left_intensity_mapping():
    action = get_action("look_left")
    scale = action["intensity_scale"]
    full_pan = action["keyframes"][-1]["pan"]
    assert round(full_pan * scale[0]) == -25
    assert round(full_pan * scale[1]) == -45
    assert round(full_pan * scale[2]) == -75


def test_look_up_and_down_intensity_mapping():
    up = get_action("look_up")
    up_full = up["keyframes"][-1]["tilt"]
    assert [round(up_full * s) for s in up["intensity_scale"]] == [-8, -14, -20]

    down = get_action("look_down")
    down_full = down["keyframes"][-1]["tilt"]
    assert [round(down_full * s) for s in down["intensity_scale"]] == [15, 30, 45]


def test_all_linked_expressions_are_known():
    catalog = load_catalog()
    known = set(catalog["expressions"])
    linked = {a["linked_expression"] for a in catalog["actions"] if a["linked_expression"]}
    assert linked <= known
    assert linked == {"surprised", "sleep", "idle"}


def test_tool_menu_lists_every_action():
    menu = build_llm_action_menu()
    for name in get_action_names():
        assert f"- {name}（" in menu
    assert "intensity 1/2/3" in menu
    assert "repeat 1-5" in menu


def test_resolve_params_defaults_and_ranges():
    nod = get_action("nod")
    assert resolve_params(nod, None) == {"intensity": 2, "speed": 2, "repeat": 1}
    assert resolve_params(nod, {"intensity": 3, "speed": 1, "repeat": 5}) == {
        "intensity": 3,
        "speed": 1,
        "repeat": 5,
    }
    with pytest.raises(CatalogError):
        resolve_params(nod, {"intensity": 0})
    with pytest.raises(CatalogError):
        resolve_params(nod, {"intensity": 4})
    with pytest.raises(CatalogError):
        resolve_params(nod, {"speed": 0})
    with pytest.raises(CatalogError):
        resolve_params(nod, {"repeat": 6})
    with pytest.raises(CatalogError):
        resolve_params(get_action("home"), {"intensity": 3})


def test_resolve_params_rejects_unknown_action_inputs():
    with pytest.raises(CatalogError):
        resolve_params(get_action("nod"), {"intensity": "2"})


@pytest.mark.parametrize(
    "mutator, needle",
    [
        (lambda c: c["actions"][0].update(intensity_scale=[0.5, 0.8, 0.9]), "第三档必须为 1.0"),
        (lambda c: c["actions"][1]["keyframes"][1].update(tilt=99), "超出安全范围"),
        (lambda c: c["actions"][2]["keyframes"][1].update(pan=-200), "超出安全范围"),
        (lambda c: c["actions"][3].update(category="nope"), "category 未在 categories 中定义"),
        (lambda c: c["actions"][4].update(linked_expression="nope"), "linked_expression 未在 expressions 中定义"),
        (lambda c: c["actions"][5].update(max_duration_ms=4000), "仅白名单动作"),
        (lambda c: c["actions"][6].update(name="home"), "动作名存在重复"),
        (lambda c: c["actions"].pop(), "缺少动作"),
        (lambda c: c["actions"][7].update(intensity_scale=[0.6, 0.4, 1.0]), "必须非递减"),
    ],
)
def test_validator_rejects_broken_catalog(catalog, mutator, needle):
    broken = copy.deepcopy(catalog)
    mutator(broken)
    errors = validate_catalog(broken)
    assert any(needle in e for e in errors), errors


def test_catalog_path_exists():
    assert CATALOG_PATH.exists()
    assert SCHEMA_PATH.exists()

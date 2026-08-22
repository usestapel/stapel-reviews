"""Per-module contract triad + capabilities + drift gate (contract-pipeline.md §2-3).

stapel-reviews emits its own contract triad — ``docs/schema.json`` (OpenAPI),
``docs/flows.json`` ([], no @flow_step here) and ``docs/errors.json`` — plus
``docs/capabilities.json`` (§2 fourth artifact), from a single-module
``{reviews + core}`` Django instance mounted at ``/reviews/api/v1/``.

reviews is not mounted in stapel-example-monolith yet, so there is no aggregate
slice to diff against for byte-identity — standalone validation
(contract-pipeline.md §9 fallback) substitutes: determinism, self-contained
$ref closure, JWT security on protected ops, canonical-prefix paths.

Regenerate after any change to a serializer/view/url/error key:

    make contract

then commit docs/{schema,flows,errors,capabilities}.json.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_PY = sys.version_info[:2]
if _PY != (3, 12):
    _GOT = f"{_PY[0]}.{_PY[1]}"
    pytest.skip(
        "stapel-reviews contract tests require Python 3.12 (the CI/monolith "
        f"pin) — running {_GOT}. drf-spectacular renders component descriptions "
        "(Optional[X] vs X | None) differently across Python minor versions, so "
        "drift/identity checks emitted+compared under any other minor produce "
        "false diffs. Skipping on any non-3.12 interpreter.",
        allow_module_level=True,
    )

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
TRIAD = ("schema.json", "flows.json", "errors.json")
# The fifth artifact (badge-canon §3): docs/llms.txt, rendered from
# docs/capabilities.json (+schema/errors/flows) by stapel_tools.llms_txt.
ARTIFACTS = TRIAD + ("capabilities.json", "llms.txt")


def _emit(out_dir: Path) -> None:
    for module in ("stapel_reviews._codegen", "stapel_reviews._capabilities"):
        subprocess.run(
            [sys.executable, "-m", module, "--out", str(out_dir)],
            cwd=str(REPO),
            check=True,
            capture_output=True,
        )
    # llms.txt is rendered from the REAL committed docs/capabilities.json (not
    # the just-regenerated tmp one) — same as `make contract-check` — so this
    # step also catches a stale llms.txt independently of the loop above.
    subprocess.run(
        [sys.executable, "-m", "stapel_tools.llms_txt", ".", "--out", str(out_dir)],
        cwd=str(REPO),
        check=True,
        capture_output=True,
    )


def test_contract_artifacts_committed():
    for name in ARTIFACTS:
        assert (DOCS / name).is_file(), f"missing docs/{name} — run `make contract`"
    assert (DOCS / "capabilities.meta.json").is_file(), (
        "missing docs/capabilities.meta.json — the curated layer is "
        "hand-written and committed, not generated"
    )


def test_contract_has_no_drift(tmp_path):
    _emit(tmp_path)
    for name in ARTIFACTS:
        committed = (DOCS / name).read_bytes()
        regenerated = (tmp_path / name).read_bytes()
        assert committed == regenerated, (
            f"docs/{name} drifted — run `make contract` and commit docs/{name}"
        )


def test_emission_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _emit(a)
    _emit(b)
    for name in ARTIFACTS:
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_paths_carry_canonical_prefix():
    schema = json.loads((DOCS / "schema.json").read_text())
    assert schema["paths"], "schema has no paths"
    assert all(p.startswith("/reviews/api/v1/") for p in schema["paths"]), (
        "schema paths are not mounted at the canonical /reviews/api/v1/ prefix"
    )


def test_flows_are_empty_no_flow_step_annotations():
    flows = json.loads((DOCS / "flows.json").read_text())
    assert flows == [], (
        "docs/flows.json is non-empty but no @flow_step annotation exists in "
        "stapel_reviews — investigate before assuming [] is still correct"
    )


def _all_refs(obj) -> set[str]:
    return set(re.findall(r'"#/components/schemas/([^"]+)"', json.dumps(obj)))


def test_schema_refs_are_self_contained():
    schema = json.loads((DOCS / "schema.json").read_text())
    comps = schema.get("components", {}).get("schemas", {})
    seen: set[str] = set()
    stack = list(_all_refs(schema["paths"]))
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        if name in comps:
            stack.extend(_all_refs(comps[name]))
    dangling = seen - set(comps)
    assert not dangling, f"dangling $ref(s) with no component definition: {dangling}"


def test_protected_paths_carry_jwt_security():
    schema = json.loads((DOCS / "schema.json").read_text())
    missing = []
    for path, operations in schema["paths"].items():
        for method, op in operations.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            security = op.get("security") or []
            if not any("JWTCookieAuth" in entry for entry in security):
                missing.append(f"{method.upper()} {path}")
    assert not missing, f"operations missing JWTCookieAuth security: {missing}"


def test_list_and_aggregate_declare_target_query_params():
    """`views.py:_target_params` reads target_type/target_key off the query
    string for both GET endpoints (never declared before A2/0.2.2 — a pure
    codegen client had no way to pass them, the storefront spec §1.8).
    `include` is list-only (views.py:118-132); aggregate has no such param."""
    schema = json.loads((DOCS / "schema.json").read_text())
    list_params = {
        p["name"]: p
        for p in schema["paths"]["/reviews/api/v1/reviews"]["get"]["parameters"]
    }
    assert list_params["target_type"]["in"] == "query"
    assert list_params["target_type"]["required"] is True
    assert list_params["target_key"]["in"] == "query"
    assert list_params["target_key"]["required"] is True
    assert list_params["include"]["in"] == "query"
    assert not list_params["include"].get("required")

    aggregate_params = {
        p["name"]: p
        for p in schema["paths"]["/reviews/api/v1/reviews/aggregate"]["get"][
            "parameters"
        ]
    }
    assert aggregate_params["target_type"]["required"] is True
    assert aggregate_params["target_key"]["required"] is True
    assert "include" not in aggregate_params


def test_list_declares_anchor_pagination_params():
    """`ReviewAnchorPagination`'s own params (views.py:38-45) — undeclared
    before the storefront spec §13.8 note 3 because a bare `APIView`
    never runs drf-spectacular's paginator introspection. All three are
    optional: their absence means "first page, default size, forward"."""
    schema = json.loads((DOCS / "schema.json").read_text())
    list_params = {
        p["name"]: p
        for p in schema["paths"]["/reviews/api/v1/reviews"]["get"]["parameters"]
    }
    for name in ("anchor", "limit", "direction"):
        assert name in list_params, f"{name} missing from GET /reviews parameters"
        assert not list_params[name].get("required")
    assert set(list_params["direction"]["schema"]["enum"]) == {"next", "prev", "center"}
    # The aggregate endpoint has no pagination — it is a single object.
    aggregate_params = {
        p["name"]: p
        for p in schema["paths"]["/reviews/api/v1/reviews/aggregate"]["get"][
            "parameters"
        ]
    }
    for name in ("anchor", "limit", "direction"):
        assert name not in aggregate_params


def test_list_declares_the_anchor_pagination_envelope():
    """`GET /reviews` returns `AnchorPagination`'s envelope
    (`{items, next_anchor, prev_anchor, has_next, has_prev, count}`), not a
    bare array — the drift `ReviewListCreateView` used to declare
    (the storefront spec §13.8 note 3, fixed by `ReviewPageSerializer`
    in serializers.py)."""
    schema = json.loads((DOCS / "schema.json").read_text())
    op = schema["paths"]["/reviews/api/v1/reviews"]["get"]
    response_schema = op["responses"]["200"]["content"]["application/json"]["schema"]
    assert "$ref" in response_schema, (
        "GET /reviews 200 response is not a $ref to a named component — "
        "looks like it regressed to a bare array"
    )
    ref_name = response_schema["$ref"].rsplit("/", 1)[-1]
    envelope = schema["components"]["schemas"][ref_name]
    assert envelope["type"] == "object"
    assert set(envelope["properties"]) == {
        "items",
        "next_anchor",
        "prev_anchor",
        "has_next",
        "has_prev",
        "count",
    }
    items_schema = envelope["properties"]["items"]
    assert items_schema["type"] == "array"


def test_anonymous_reads_carry_optional_security():
    """`GET /reviews` and `GET /reviews/aggregate` are `AllowAny` (storefront
    F5 verdict) — drf-spectacular renders that as an extra `{}` alternative
    alongside `JWTCookieAuth` (mirrors stapel-search's public endpoints), so
    an anonymous caller is a documented option, not just an accident of
    testing against the browsable API. Writes keep JWTCookieAuth as the only
    option."""
    schema = json.loads((DOCS / "schema.json").read_text())
    for path in ("/reviews/api/v1/reviews", "/reviews/api/v1/reviews/aggregate"):
        security = schema["paths"][path]["get"]["security"]
        assert {"JWTCookieAuth": []} in security
        assert {} in security, f"GET {path} lost its anonymous-access alternative"
    write_ops = [
        ("/reviews/api/v1/reviews", "post"),
        ("/reviews/api/v1/reviews/{review_id}/moderate", "post"),
        ("/reviews/api/v1/reviews/{review_id}/response", "post"),
    ]
    for path, method in write_ops:
        security = schema["paths"][path][method]["security"]
        assert security == [{"JWTCookieAuth": []}], (
            f"{method.upper()} {path} must stay authenticated-only"
        )


# --- capabilities.json content sanity (capability-config.md §2) ---------------


def _capabilities() -> dict:
    return json.loads((DOCS / "capabilities.json").read_text())


def test_capabilities_axes():
    """Two CTO-facing axes: MODERATION_DEFAULT (enum) and RESPONSES (bool)."""
    axes = {a["key"]: a for a in _capabilities()["axes"]}
    assert set(axes) == {"MODERATION_DEFAULT", "RESPONSES"}
    assert axes["MODERATION_DEFAULT"]["kind"] == "enum"
    assert axes["MODERATION_DEFAULT"]["default"] == "post"
    assert axes["RESPONSES"]["kind"] == "bool"
    for axis in axes.values():
        # Behavioral, not gating — they change behavior, not which ops exist.
        assert axis["gates"]["operations"] == []
        assert axis["curated"]["business_label"]


def test_capabilities_extension_points_cover_the_seams():
    names = {e["name"] for e in _capabilities()["extension_points"]}
    assert "TARGET_TYPES" in names


def test_capabilities_operations_total_matches_schema():
    schema = json.loads((DOCS / "schema.json").read_text())
    methods = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
    total = sum(1 for item in schema["paths"].values() for m in item if m in methods)
    assert _capabilities()["operations_total"] == total


def test_capabilities_envelope():
    doc = _capabilities()
    import tomllib

    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert doc["module"] == pyproject["project"]["name"]
    assert doc["version"] == pyproject["project"]["version"]
    assert doc["provides"]
    assert doc["extension_points"]
    assert doc["requires"]


# --- README.md — the sixth artifact (tracker #257) ---------------------------
#
# README.md is assembled by ``stapel_tools.readme`` from docs/readme.md (the
# human half: what this module is and how to think about it) plus the contract
# documents above (badges, version, surface counts, doc links). Everything a
# hand-written README used to restate — and therefore used to get wrong one
# release later — is generated and gated here.

def test_readme_is_assembled_and_has_no_drift():
    from stapel_tools.readme import load_inputs, render, static_languages

    inputs = load_inputs(REPO)
    languages = static_languages(REPO)
    assert languages == ["en"], "expected exactly the English static body docs/readme.md"
    committed = (REPO / "README.md").read_text()
    assert committed == render(REPO, inputs, "en", languages), (
        "README.md drifted — run `make contract` and commit README.md "
        "(edit prose in docs/readme.md, never README.md itself)"
    )


def test_readme_version_matches_the_package():
    """The #226 gate, at the point where the number is published."""
    import tomllib

    from stapel_tools.readme import load_inputs, resolve_version

    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert resolve_version(load_inputs(REPO)) == pyproject["project"]["version"]

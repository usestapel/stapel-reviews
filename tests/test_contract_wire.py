"""Every response body the contract declares is a body the views actually send.

``docs/schema.json`` is emitted from the views' ``@extend_schema``
annotations, and an annotation is a CLAIM: it says what the view returns, and
the generator has no way to check it against the method body.
``tests/test_contract.py`` compares the committed document against a FRESH
EMISSION of the same annotations — it proves the file is not stale, and
nothing else, because both sides come from the claim. stapel-alerts 0.2.0
shipped ``GET /issues`` declared as ``Issue[]`` while the wire carried
``{count, offset, limit, results}``: the drift gate was green and the
frontend pair rendered ``undefined``.

This is the gate the generator cannot be: it performs every operation the
committed schema declares with a JSON response body, and validates the body
it gets against the schema it was promised.

Rules this file holds itself to:

* an operation with a declared JSON response and no entry in ``RECIPES``
  FAILS LOUDLY — a gate that quietly covers three of four rows is the family
  of green that proves nothing;
* a path parameter the gate cannot fill fails at the point of substitution,
  naming the operation;
* the operations that genuinely cannot be driven in-process are listed by
  name in ``UNDRIVABLE`` with a one-line reason each. That list is asserted
  to be exactly current: a stale entry, or a missing reason, fails;
* a collection that comes back empty fails in the populated pass — an empty
  array validates against any item schema, and an empty object validates
  against any ``additionalProperties`` map, so an empty answer is a check
  that looked at nothing. That covers the nested ``items`` array of the page
  envelope and the free-form owner map, neither of which a generic "is the
  body a list" check can see;
* every read is driven a SECOND time in its emptiest legal state
  (``EMPTY_STATE``): a subject with reviews and a subject with none, a review
  carrying no body and no owner reply, an aggregate over nothing. Every null
  finding in the first wave of this gate was there.

Runs on every interpreter: it reads the committed schema and never emits.
``tests/test_contract.py`` re-emits and therefore has an interpreter opinion;
this file does not.

THE MOUNT. ``codegen_urls.py`` mounts ``reviews/`` → ``stapel_reviews.urls``,
which contributes ``api/v1/``, so the document is written against
``/reviews/api/v1/…``. ``tests/urls.py`` mounts the identical ``reviews/``
prefix, so this module's suite has always been looking where the document
points — unlike five of the first eight libraries in this wave, whose test
urlconf pointed somewhere the document does not describe. The emission mount
is declared here rather than borrowed, so
``test_every_declared_path_resolves_under_this_urlconf`` fails at the one
moment it is cheap to fix: when somebody changes a mount.

WHAT IT FOUND on its first run: 6 of 6 operations driven, 0 red. The claims
this module makes about its own wire are honest, in both the populated and
the empty state, including the one ``nullable`` shape it declares
(``ReviewResponse.response``, the owner's reply) and the two nullable anchors
of the page envelope. ``test_the_gate_is_not_blind`` proves that is a finding
rather than a gate that never looked: it re-validates every driven body
against ``{"type": "string"}`` and requires all of them to fail.

Two shared defects were looked for by name and are absent here:

* ``stapel_core.django.api.presenters._infer_type`` (presenters.py:130) maps a
  Django model field class to a Python type through ``_TYPE_MAP`` without
  reading ``field.null``, so any presenter naming a nullable column in
  ``fields = (...)`` emits a REQUIRED non-nullable scalar the wire answers
  null for. stapel-reviews uses no presenter at all — every response is a
  hand-written ``dto.py`` dataclass rendered by ``StapelDataclassSerializer``
  (serializers.py), and the one optional member is spelled
  ``Optional[ResponseResponse]`` explicitly, which the emitter renders as
  ``nullable: true``.
* ``RevisionViewSetMixin.data_json`` and ``BulkUpdateResponse.updated_ids``
  (both fixed in stapel-core 0.71.0). Neither symbol appears anywhere in this
  module; its ``stapel-core>=0.26.0,<1.0`` floor therefore inherits nothing
  to record.
"""
import copy
import json
import re
import uuid
from pathlib import Path

import jsonschema
import pytest
from django.test import override_settings
from django.urls import include, path as url_path
from rest_framework.test import APIClient

REPO = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((REPO / "docs" / "schema.json").read_text())

#: The mount the contract is emitted at, reproduced for the test client
#: (``codegen_urls.py``: ``reviews/`` → ``stapel_reviews.urls``, which
#: contributes ``api/v1/``).
urlpatterns = [
    url_path("reviews/", include("stapel_reviews.urls")),
]

pytestmark = [pytest.mark.django_db, pytest.mark.urls(__name__)]

V1 = "/reviews/api/v1"

#: The one target type this gate registers. The module ships knowing none
#: (``registry.BUILTIN_TARGET_TYPES`` is empty) — a host declares what may be
#: reviewed, and so does the gate, through the same door.
TARGET_TYPE = "seller"

#: The comm Function name the type's ``can_moderate`` policy points at. The
#: module CALLS the host to ask who may moderate or reply; the gate stands
#: that call up rather than reaching around it, so everything on this side of
#: the seam runs for real.
CAN_MODERATE = "wire.contract.can_moderate"


def reviews_settings(**extra):
    """A fresh override each time: one instance cannot be entered twice.

    Two rate limiters are stood down and neither is part of the contract.
    ``LIST_THROTTLE`` and ``AGGREGATE_THROTTLE`` are scoped buckets living in
    the process-wide locmem cache, so they answer 429 depending on what ran
    before them in the same run — which says nothing about the 200 body the
    contract declares. This module's own suites pin the throttling behaviour.
    """
    config = {
        "TARGET_TYPES": {TARGET_TYPE: {"can_moderate": CAN_MODERATE}},
        "LIST_THROTTLE": None,
        "AGGREGATE_THROTTLE": None,
    }
    config.update(extra)
    return override_settings(STAPEL_REVIEWS=config)


def target_types(**policy):
    """``reviews_settings`` with the one registered type's policy adjusted."""
    merged = {"can_moderate": CAN_MODERATE}
    merged.update(policy)
    return reviews_settings(TARGET_TYPES={TARGET_TYPE: merged})


@pytest.fixture(autouse=True)
def _registered_type_and_moderator():
    """The registry entry and the host callback every recipe needs.

    ``can_moderate`` is fail-closed when a type registers no callback
    (registry.py), so without this the moderate and respond operations could
    not answer 2xx at all and their declared bodies would go unchecked.
    """
    from stapel_core.comm import register_function

    register_function(CAN_MODERATE, lambda payload: True)
    with reviews_settings():
        yield


@pytest.fixture(autouse=True)
def _media_root(tmp_path):
    """Nothing here writes files today; pin the root so nothing ever does.

    ``MEDIA_ROOT`` is unset in this module's harness settings
    (``_codegen_settings.py``), so it defaults to the working directory — in
    stapel-auth that put a data export into the checkout, where under a flat
    package layout a stray directory also shadowed a real submodule.
    """
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# The contract side: what the document declares
# ─────────────────────────────────────────────────────────────────────────────


def _json_schema(node):
    """OpenAPI 3.0 → JSON Schema, for the divergence that matters here.

    OAS 3.0 spells "may be null" as ``nullable: true`` beside a ``type`` (or
    beside the ``allOf`` that wraps a ``$ref``, which is how
    ``ReviewResponse.response`` is emitted); JSON Schema has no such keyword
    and would refuse the null — which is exactly what a review with no owner
    reply answers. Everything else drf-spectacular emits here (``$ref``,
    ``allOf``, ``required``, ``additionalProperties``) is JSON Schema as
    written.
    """
    if isinstance(node, list):
        return [_json_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    rebuilt = {k: _json_schema(v) for k, v in node.items() if k != "nullable"}
    if node.get("nullable"):
        return {"anyOf": [rebuilt, {"type": "null"}]}
    return rebuilt


def _drop_nullable(node):
    """The same conversion with ``nullable`` DROPPED instead of honored.

    Used by one test only: a schema built this way refuses every null, so an
    empty-state body that still validates under it never contained a null at
    all — which would mean the empty-state recipes are not reaching the state
    they were written for.
    """
    if isinstance(node, list):
        return [_drop_nullable(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {k: _drop_nullable(v) for k, v in node.items() if k != "nullable"}


def _validator(response_schema, *, convert=_json_schema):
    root = copy.deepcopy(response_schema)
    root["components"] = copy.deepcopy(SCHEMA["components"])
    return jsonschema.Draft202012Validator(convert(root))


def _operations():
    """Every ``(method, path, 2xx code, JSON body schema)`` the contract declares."""
    ops = []
    for path, methods in SCHEMA["paths"].items():
        for method, op in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            for code, response in op.get("responses", {}).items():
                body = (
                    response.get("content", {})
                    .get("application/json", {})
                    .get("schema")
                )
                if body is not None and code.startswith("2"):
                    ops.append((method.upper(), path, int(code), body))
    return sorted(ops, key=lambda o: (o[1], o[0], o[2]))


OPERATIONS = _operations()


# ─────────────────────────────────────────────────────────────────────────────
# The wire side: harness
# ─────────────────────────────────────────────────────────────────────────────


def _unique(prefix):
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def make_user(**kwargs):
    from django.contrib.auth import get_user_model

    defaults = dict(
        username=_unique("wire_"),
        email=f"{_unique('wire-')}@example.com",
        password="wire-contract-password-7",
    )
    defaults.update(kwargs)
    return get_user_model().objects.create_user(**defaults)


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def anonymous():
    return APIClient()


def make_review(target_key, *, author=None, rating=5, body="", status=None):
    """One review through the real service, so the owner-key stamp, the
    moderation default and the emitted fact all happen for real."""
    from stapel_reviews import services
    from stapel_reviews.models import Review

    review = services.create_review(
        target_type=TARGET_TYPE,
        target_key=target_key,
        author=author or make_user(),
        rating=rating,
        body=body,
    )
    if status is not None and review.status != status:
        Review.objects.filter(pk=review.pk).update(status=status)
        review.refresh_from_db()
    return review


def make_response(review, *, body="Thank you for the kind words.", author=None):
    """The target owner's single reply, through the service and its gate."""
    from stapel_reviews import services

    return services.respond(review, author=author or make_user(), body=body)


def target_query(target_key, **extra):
    parts = [f"target_type={TARGET_TYPE}", f"target_key={target_key}"]
    parts += [f"{name}={value}" for name, value in extra.items()]
    return "?" + "&".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# The recipe table
# ─────────────────────────────────────────────────────────────────────────────


class Call:
    """Performs one declared operation, and refuses to guess a path parameter."""

    def __init__(self, method, path):
        self.method = method
        self.path = path

    def __call__(self, client, params=None, data=None, query="", **extra):
        url = self.path
        for name, value in (params or {}).items():
            url = url.replace("{%s}" % name, str(value))
        assert "{" not in url, (
            f"{self.method} {self.path}: a path parameter this gate does not "
            "know how to fill — teach its recipe, or the operation goes unchecked"
        )
        send = getattr(client, self.method.lower())
        if self.method in ("GET", "DELETE"):
            return send(url + query, **extra)
        return send(url + query, data if data is not None else {}, format="json", **extra)


#: How to perform each operation the contract declares with a JSON response
#: body, keyed by ``(METHOD, path template)``. A recipe returns the response it
#: produced, or a list of ``(label, response)`` pairs when one operation has
#: more than one answering state worth asking.
RECIPES = {}

#: The same operations again, in the emptiest state the contract still has to
#: describe: a subject nobody has reviewed, a review with no body and no
#: reply, an owner map with no owners in it. A populated answer cannot say
#: what a field holds when there is nothing to hold, and that is where every
#: null finding in the first wave of this gate was.
EMPTY_STATE = {}


def recipe(method, path, table=None):
    def register(fn):
        target = RECIPES if table is None else table
        key = (method, V1 + path)
        assert key not in target, f"duplicate recipe for {method} {path}"
        target[key] = fn
        return fn

    return register


def empty_state(method, path):
    return recipe(method, path, table=EMPTY_STATE)


#: Operations that cannot be driven in-process, by name and with the reason.
#: A short, visible list is acceptable here; a silent skip is not.
#:
#: EMPTY. The one thing this module genuinely cannot answer for itself — who
#: may moderate or reply — is a declared comm Function seam, so the gate wires
#: it the way a host does and everything on this side of it runs for real.
UNDRIVABLE: dict = {}

#: Collections nested inside an object body that must actually carry a row in
#: the populated pass. An empty array validates against any item schema, so a
#: populated run that leaves one empty looked at nothing — and the generic
#: "is the body a list" check cannot see a list one level down.
POPULATED_COLLECTIONS = {
    ("GET", V1 + "/reviews"): ("items",),
}

#: Operations whose body is a FREE-FORM MAP (``additionalProperties``). An
#: empty object validates against such a schema vacuously, exactly the way an
#: empty array does against an item schema — so the populated pass requires at
#: least one key.
POPULATED_MAPS = {
    ("POST", V1 + "/reviews/aggregates/by-owner"),
}

#: Writes that are reads in everything but the verb, and are therefore held to
#: the same empty-state requirement as a ``GET``. ``POST
#: /reviews/aggregates/by-owner`` is a POST only because the owner keys are
#: opaque host strings of unbounded length (views.py, ``OwnerAggregatesView``
#: docstring); it reads and never writes.
READ_SHAPED_WRITES = {
    ("POST", V1 + "/reviews/aggregates/by-owner"),
}


# ── the review list ──────────────────────────────────────────────────────────


@recipe("GET", "/reviews")
def _review_list(call):
    """Three states of the page envelope in one operation: a full page (both
    anchors null), a page with more behind it (``next_anchor`` a real string,
    which is the only branch that proves the anchor is a string at all), and
    the owner axis, which is a different query path into the same envelope."""
    owner_key = _unique("owner-")
    with target_types(can_moderate=CAN_MODERATE, owner_key_for=lambda key: owner_key):
        target_key = _unique("target-")
        reviewed = make_review(target_key, rating=5, body="Exactly as described.")
        make_response(reviewed)
        make_review(target_key, rating=4, body="Good, a little slow.")
        make_review(target_key, rating=3)

        whole = call(anonymous(), query=target_query(target_key))
        short = call(anonymous(), query=target_query(target_key, limit=2))
        by_owner = call(anonymous(), query=f"?owner_key={owner_key}")

        # The anchors are the only nullable members of the envelope, and a
        # null validates against a nullable claim whatever the truth is. So
        # the branch that exists to exercise the STRING side has to be held
        # to it here, or "both states driven" is a sentence rather than a
        # check.
        assert isinstance(short.json()["next_anchor"], str), (
            "the short page was meant to leave rows behind it, so next_anchor "
            f"must be a string here: {short.json()['next_anchor']!r}"
        )
        assert whole.json()["next_anchor"] is None, (
            "the whole page has nothing behind it, so next_anchor must be null"
        )

        return [
            ("one whole page, nothing behind it", whole),
            ("a short page with more behind it (next_anchor is set)", short),
            ("the owner axis (every review of everything that owner owns)", by_owner),
        ]


@empty_state("GET", "/reviews")
def _review_list_empty(call):
    """Two kinds of empty, and the second is the one that matters: a page
    with no rows at all, and a page whose single row carries none of its
    optional values — no body, no owner reply. An empty ARRAY validates
    against any item schema, so a collection's emptiest interesting state is
    one row holding nothing."""
    nobody_reviewed = _unique("target-")
    bare = _unique("target-")
    make_review(bare, rating=1)
    return [
        (
            "a subject nobody has reviewed",
            call(anonymous(), query=target_query(nobody_reviewed)),
        ),
        (
            "one review with no body and no reply",
            call(anonymous(), query=target_query(bare)),
        ),
    ]


@recipe("POST", "/reviews")
def _review_create(call):
    return call(
        client_for(make_user()),
        data={
            "target_type": TARGET_TYPE,
            "target_key": _unique("target-"),
            "rating": 5,
            "body": "Written by the wire gate.",
        },
    )


@empty_state("POST", "/reviews")
def _review_create_empty(call):
    """The minimum a create accepts: no body. ``body`` is the declared empty
    string and ``response`` is the declared null — the two fields most likely
    to be a lie in this shape."""
    return call(
        client_for(make_user()),
        data={
            "target_type": TARGET_TYPE,
            "target_key": _unique("target-"),
            "rating": 1,
        },
    )


# ── moderation and the owner's reply ─────────────────────────────────────────


@recipe("POST", "/reviews/{review_id}/moderate")
def _review_moderate(call):
    """Both verbs, on rows that carry everything the shape can carry: hiding
    a published review that already has an owner reply, and publishing one
    that was held by pre-moderation."""
    from stapel_reviews.models import ReviewStatus

    with target_types(moderation="pre"):
        held = make_review(_unique("target-"), rating=2, body="Not what I ordered.")
        assert held.status == ReviewStatus.PENDING, held.status
        published = make_review(
            _unique("target-"),
            rating=5,
            body="Would buy again.",
            status=ReviewStatus.PUBLISHED,
        )
        make_response(published)
        return [
            (
                "publish a review held by pre-moderation",
                call(
                    client_for(make_user()),
                    params={"review_id": held.id},
                    data={"action": "publish", "reason": "checked by the wire gate"},
                ),
            ),
            (
                "hide a published review that carries an owner reply",
                call(
                    client_for(make_user()),
                    params={"review_id": published.id},
                    data={"action": "hide", "reason": "checked by the wire gate"},
                ),
            ),
        ]


@empty_state("POST", "/reviews/{review_id}/moderate")
def _review_moderate_empty(call):
    """A review carrying none of its optional values, moderated with no
    reason: the answer still has to be the whole declared shape, with
    ``body`` the empty string and ``response`` null."""
    review = make_review(_unique("target-"), rating=3)
    return call(
        client_for(make_user()),
        params={"review_id": review.id},
        data={"action": "hide"},
    )


@recipe("POST", "/reviews/{review_id}/response")
def _review_respond(call):
    review = make_review(_unique("target-"), rating=4, body="Solid.")
    return call(
        client_for(make_user()),
        params={"review_id": review.id},
        data={"body": "Thank you — glad it worked out."},
    )


@empty_state("POST", "/reviews/{review_id}/response")
def _review_respond_empty(call):
    """``RespondRequest.body`` defaults to the empty string (dto.py), so an
    owner may reply with nothing. The declared ``ResponseResponse.body`` is a
    REQUIRED string — this asks whether the emptiest legal reply is still a
    string on the wire, on a review that itself carries no body."""
    review = make_review(_unique("target-"), rating=2)
    return call(client_for(make_user()), params={"review_id": review.id}, data={})


# ── the aggregates ───────────────────────────────────────────────────────────


@recipe("GET", "/reviews/aggregate")
def _aggregate(call):
    target_key = _unique("target-")
    make_review(target_key, rating=5)
    make_review(target_key, rating=4)
    return call(anonymous(), query=target_query(target_key))


@empty_state("GET", "/reviews/aggregate")
def _aggregate_empty(call):
    """A subject nobody has reviewed: the declared answer is ``avg`` 0.0 and
    ``count`` 0, both REQUIRED and neither nullable — an aggregate over
    nothing is the state a storefront renders on every new listing."""
    return call(anonymous(), query=target_query(_unique("target-")))


@recipe("POST", "/reviews/aggregates/by-owner")
def _owner_aggregates(call):
    """Two owners asked for, one of whom has published reviews: the map is
    keyed by owner and an owner with nothing is ABSENT rather than zeroed,
    so this also checks that the present key is the one that earned it."""
    owner_key = _unique("owner-")
    unreviewed = _unique("owner-")
    with target_types(owner_key_for=lambda key: owner_key):
        target_key = _unique("target-")
        make_review(target_key, rating=5)
        make_review(target_key, rating=4)
        response = call(
            anonymous(), data={"owner_keys": [owner_key, unreviewed]}
        )

    # "Absent rather than zeroed" is the stated convention and the reason the
    # body is a free-form map at all. A map schema cannot express it, so the
    # gate does.
    body = response.json()
    assert owner_key in body, f"the reviewed owner is missing from the map: {body}"
    assert unreviewed not in body, (
        f"an owner with no published review must be ABSENT, not zeroed: {body}"
    )
    return response


@empty_state("POST", "/reviews/aggregates/by-owner")
def _owner_aggregates_empty(call):
    """Two kinds of empty: no owner keys asked for at all, and owner keys
    nobody has a published review about. Both answer ``{}`` — an owner key
    with no published review is absent from the map rather than present with
    zeros (serializers.py, ``OWNER_AGGREGATES_RESPONSE_SCHEMA``)."""
    return [
        ("no owner keys asked for", call(anonymous(), data={"owner_keys": []})),
        (
            "owner keys nobody has reviewed",
            call(anonymous(), data={"owner_keys": [_unique("owner-")]}),
        ),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# The gate
# ─────────────────────────────────────────────────────────────────────────────


#: Operations whose declared body the wire does not send, with the defect and
#: its owner. ``strict=True``: a fixed entry fails until it is deleted, so a
#: finding can be neither forgotten nor quietly kept.
#:
#: EMPTY, and that is the finding rather than the absence of one: 6 of 6
#: operations answer the shape they promise, in both the populated and the
#: empty state, and ``test_the_gate_is_not_blind`` proves the check reached
#: every one of those bodies. The mechanism stays because the next wave will
#: need it.
KNOWN_MISMATCHES: dict = {}


def test_the_contract_declares_something_to_check():
    assert OPERATIONS, "docs/schema.json declares no JSON responses at all"


def test_every_declared_path_resolves_under_this_urlconf():
    """The suite must be looking where the document describes.

    Five of the first eight libraries this gate was written for had a
    committed contract that nothing had ever driven, because the test urlconf
    mounted somewhere the document does not describe: one mounted a different
    prefix AND one segment short, one mounted the paths bare, one mounted a
    doubled segment, one mounted less than the emission did. In every case
    the operations were "covered" by a file that could not have reached a
    single one of them.

    Reviews is not one of them — ``tests/urls.py`` and ``codegen_urls.py``
    mount the identical ``reviews/`` prefix — and this assertion is what
    keeps saying so. A missing recipe already fails loudly; this fails when
    the MOUNT is wrong, which no per-operation check can see, because when
    the mount is wrong every operation is equally and silently unreachable.
    """
    from django.urls import Resolver404, resolve

    # Resolution cares about the SHAPE of a segment, and a urlconf may use
    # several converters — uuid, int, slug. A path counts as reachable if any
    # one shape resolves: the question here is whether the mount exists, not
    # whether a particular id does.
    candidates = (
        "00000000-0000-4000-8000-000000000000",
        "1",
        "a-slug",
    )

    unreachable = []
    for _method, path, _code, _schema in OPERATIONS:
        for value in candidates:
            try:
                resolve(re.sub(r"\{[^}]+\}", value, path))
                break
            except Resolver404:
                continue
        else:
            unreachable.append(path)

    assert not unreachable, (
        "these declared paths do not resolve under this module's urlconf, so "
        "nothing here can be driving them — the mount is wrong, not the "
        "recipes:\n  " + "\n  ".join(sorted(set(unreachable)))
    )


def test_every_declared_operation_is_driven_or_named_undrivable():
    """No operation is covered by silence, and no entry outlives its operation."""
    declared = {(method, path) for method, path, _code, _schema in OPERATIONS}
    covered = set(RECIPES) | set(UNDRIVABLE)

    missing = sorted(declared - covered)
    assert not missing, (
        "operations with a declared JSON response body and no recipe:\n"
        + "\n".join(f"  {m} {p}" for m, p in missing)
    )
    stale = sorted(covered - declared)
    assert not stale, (
        "recipes/exclusions for operations the contract no longer declares:\n"
        + "\n".join(f"  {m} {p}" for m, p in stale)
    )
    both = sorted(set(RECIPES) & set(UNDRIVABLE))
    assert not both, f"driven AND excluded: {both}"
    for key, reason in UNDRIVABLE.items():
        assert reason and reason.strip(), f"{key} is excluded with no reason"

    stale_expectations = sorted(
        (set(POPULATED_COLLECTIONS) | POPULATED_MAPS | READ_SHAPED_WRITES) - declared
    )
    assert not stale_expectations, (
        f"row/map expectations for undeclared operations: {stale_expectations}"
    )


def test_every_read_is_also_driven_in_its_emptiest_state():
    """A populated answer cannot say what a field holds when there is nothing.

    Every null finding in the first wave of this gate was on the empty state.
    A gate that only ever seeds three rows and asks never sees any of them.

    ``READ_SHAPED_WRITES`` is held to the same bar as a ``GET``: the owner
    aggregate is a POST only because its keys are opaque host strings too long
    for a query string, and its emptiest state — an owner nobody has reviewed
    — is exactly the one a storefront renders most often.
    """
    reads = {
        (method, path)
        for method, path, _code, _schema in OPERATIONS
        if method == "GET"
    } | READ_SHAPED_WRITES
    missing = sorted(reads - set(EMPTY_STATE))
    assert not missing, (
        "reads driven only against a populated database — the state where "
        "every null claim in this gate's history was found is unchecked:\n"
        + "\n".join(f"  {m} {p}" for m, p in missing)
    )
    declared = {(m, p) for m, p, _c, _s in OPERATIONS}
    stale = sorted(set(EMPTY_STATE) - declared)
    assert not stale, f"empty-state recipes for undeclared operations: {stale}"


def test_every_known_mismatch_is_still_declared_and_explained():
    """A recorded defect must name a live operation and carry its reason.

    Without this, an operation that is renamed or removed leaves an entry that
    silences nothing and reads like a known problem forever.
    """
    declared = {(method, path) for method, path, _code, _schema in OPERATIONS}
    for key, reason in KNOWN_MISMATCHES.items():
        assert key in declared, (
            f"{key} is recorded as a known mismatch but the contract no longer "
            "declares it — delete the entry"
        )
        assert reason and reason.strip(), f"{key} is recorded with no reason"


def _labelled(result):
    """A recipe answers with one response, or with labelled branches."""
    if isinstance(result, list):
        return result
    return [("", result)]


def _drive(table, method, path, code, body_schema, *, expect_rows):
    perform = table.get((method, path))
    assert perform is not None, (
        f"{method} {path} declares a response body and has no recipe — an "
        "unchecked operation is a schema nobody proves. Teach RECIPES, or "
        "name it in UNDRIVABLE with a reason."
    )

    for label, response in _labelled(perform(Call(method, path))):
        where = f"{method} {path}" + (f" [{label}]" if label else "")
        assert response.status_code == code, (
            f"{where}: expected the declared {code}, got "
            f"{response.status_code}: {response.content[:400]}"
        )

        body = response.json()
        errors = sorted(
            _validator(body_schema).iter_errors(body), key=lambda e: list(e.path)
        )
        assert not errors, (
            f"{where} answers a body the contract does not describe:\n"
            + "\n".join(f"  at {list(e.path) or '<root>'}: {e.message}" for e in errors[:10])
            + f"\n  body: {json.dumps(body)[:600]}"
        )

        # An empty list validates against any item schema, and an empty object
        # against any additionalProperties map, so a collection must actually
        # carry a row for the check to have looked at anything.
        if expect_rows:
            if isinstance(body, list):
                assert body, f"{where}: the declared list came back empty"
            if (method, path) in POPULATED_MAPS:
                assert isinstance(body, dict) and body, (
                    f"{where}: the declared map came back empty, so nothing "
                    "in it was checked"
                )
            for name in POPULATED_COLLECTIONS.get((method, path), ()):
                assert isinstance(body, dict) and body.get(name), (
                    f"{where}: the declared collection {name!r} came back "
                    "empty, so nothing in it was checked"
                )


@pytest.mark.parametrize(
    "method,path,code,body_schema",
    OPERATIONS,
    ids=[f"{m} {p}" for m, p, _c, _s in OPERATIONS],
)
def test_the_wire_matches_the_declared_response(method, path, code, body_schema, request):
    if (method, path) in UNDRIVABLE:
        pytest.skip(f"excluded by name: {UNDRIVABLE[(method, path)]}")

    if (method, path) in KNOWN_MISMATCHES:
        request.node.add_marker(
            pytest.mark.xfail(
                strict=True,
                reason=f"{method} {path}: {KNOWN_MISMATCHES[(method, path)]}",
            )
        )

    _drive(RECIPES, method, path, code, body_schema, expect_rows=True)


_EMPTY_OPERATIONS = [
    (method, path, code, schema)
    for method, path, code, schema in OPERATIONS
    if (method, path) in EMPTY_STATE
]


@pytest.mark.parametrize(
    "method,path,code,body_schema",
    _EMPTY_OPERATIONS,
    ids=[f"{m} {p}" for m, p, _c, _s in _EMPTY_OPERATIONS],
)
def test_the_wire_matches_the_declared_response_when_there_is_nothing_there(
    method, path, code, body_schema, request
):
    """The same claim, asked in the state where the nulls live."""
    if (method, path) in KNOWN_MISMATCHES:
        request.node.add_marker(
            pytest.mark.xfail(
                strict=True,
                reason=f"{method} {path}: {KNOWN_MISMATCHES[(method, path)]}",
            )
        )

    _drive(EMPTY_STATE, method, path, code, body_schema, expect_rows=False)


def test_the_empty_states_really_produce_the_nulls():
    """The second canary: an empty-state recipe that never reaches a null.

    ``test_the_wire_matches_…_when_there_is_nothing_there`` can be green for
    two reasons — the nullable claims are honest, or the "empty" state was
    never actually empty and no null was ever on the wire, so nothing about
    nullability was tested. This tells them apart: it re-validates the same
    empty-state bodies against the declared schema with ``nullable`` DROPPED,
    which refuses every null. At least one operation must now fail, or the
    empty-state half of this file is decoration.

    ``GET /reviews`` alone is enough to carry it (both page anchors are null
    on a page with nothing behind it), but the check is written over the
    whole table so it keeps working as recipes move.
    """
    reached_a_null = []
    for method, path, code, body_schema in _EMPTY_OPERATIONS:
        strict = _validator(body_schema, convert=_drop_nullable)
        perform = EMPTY_STATE[(method, path)]
        for label, response in _labelled(perform(Call(method, path))):
            assert response.status_code == code, (
                f"{method} {path} [{label}]: expected {code}, got "
                f"{response.status_code}: {response.content[:400]}"
            )
            if next(strict.iter_errors(response.json()), None) is not None:
                reached_a_null.append(f"{method} {path} [{label}]")

    assert reached_a_null, (
        "no empty-state body contained a single null, so the nullable half of "
        "this contract was never asked anything — the 'empty' recipes are not "
        "reaching an empty state"
    )


def test_the_gate_is_not_blind():
    """A canary: swap a declared schema for one the wire cannot satisfy.

    Everything above can be green for two reasons — the claims are honest, or
    the check never looks at the body. This tells them apart by validating a
    real response against ``{"type": "string"}``: every operation here answers
    an object, so every one of them must fail. If any passes, the validation
    in ``_drive`` is not reaching the received body and this whole file proves
    nothing. With ``KNOWN_MISMATCHES`` empty this covers the entire declared
    surface.
    """
    honest = [
        (method, path, code)
        for method, path, code, _schema in OPERATIONS
        if (method, path) not in KNOWN_MISMATCHES and (method, path) not in UNDRIVABLE
    ]
    assert honest, "nothing left to canary"

    survivors = []
    for method, path, code in honest:
        try:
            _drive(RECIPES, method, path, code, {"type": "string"}, expect_rows=False)
        except AssertionError:
            continue
        survivors.append(f"{method} {path}")
    assert not survivors, (
        "these operations passed validation against {'type': 'string'} — the "
        "gate is not looking at the body it received:\n  " + "\n  ".join(survivors)
    )

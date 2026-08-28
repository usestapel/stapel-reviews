"""The guest wall on the review write (ALLOW_ANONYMOUS_WRITES).

A storefront that mints anonymous accounts so a stranger can save a favourite
hands that stranger a real, authenticated session. ``IsAuthenticated`` cannot
tell it from a registered one, so before this switch existed the "sign up to
review" wall lived only in the frontend as a disabled button and the endpoint
underneath took the POST.

The setting is the wall, and it is CLOSED by default: a deployment that mints
no anonymous users has none to reject, so the default costs it nothing.
Reads are untouched in both positions — a guest browsing reviews is the point.
"""
import pytest

pytestmark = pytest.mark.django_db

TARGET = {"target_type": "seller", "target_key": "s1"}
REVIEW_PAYLOAD = {**TARGET, "rating": 5, "body": "great"}
ERR_ANON = "error.403.reviews_anonymous_not_allowed"


def _configure(settings, *, allow_anonymous_writes=None):
    """Register the ``seller`` target type, optionally opening the wall."""
    conf = {"TARGET_TYPES": {"seller": {}}}
    if allow_anonymous_writes is not None:
        conf["ALLOW_ANONYMOUS_WRITES"] = allow_anonymous_writes
    settings.STAPEL_REVIEWS = conf


@pytest.fixture
def anonymous_user(db):
    from django.contrib.auth import get_user_model

    return get_user_model().create_anonymous_user()


@pytest.fixture
def anonymous_client(api_client, anonymous_user):
    """A guest session exactly as the storefront's heart button mints it:
    a real User row with ``is_anonymous=True`` and a valid session."""
    api_client.force_authenticate(user=anonymous_user)
    return api_client


def test_the_guest_is_a_real_authenticated_user(anonymous_user):
    """The premise. Were this False the wall would need no switch."""
    assert anonymous_user.is_authenticated is True
    assert anonymous_user.is_anonymous is True


# --- closed (the default) ---------------------------------------------------


def test_guest_review_is_refused_by_default(settings, anonymous_client):
    _configure(settings)
    resp = anonymous_client.post(
        "/reviews/api/v1/reviews", REVIEW_PAYLOAD, format="json"
    )
    assert resp.status_code == 403, resp.content
    assert resp.data["localizable_error"] == ERR_ANON


def test_guest_review_is_refused_when_the_key_is_absent(settings, anonymous_client):
    """No ``ALLOW_ANONYMOUS_WRITES`` in the host's settings dict at all —
    the closed default has to hold without the operator writing it down."""
    settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}}}
    resp = anonymous_client.post(
        "/reviews/api/v1/reviews", REVIEW_PAYLOAD, format="json"
    )
    assert resp.status_code == 403, resp.content
    assert resp.data["localizable_error"] == ERR_ANON


def test_refused_guest_review_writes_nothing(settings, anonymous_client):
    from stapel_reviews.models import Review

    _configure(settings)
    anonymous_client.post("/reviews/api/v1/reviews", REVIEW_PAYLOAD, format="json")
    assert Review.objects.count() == 0


def test_registered_user_is_allowed_while_closed(settings, auth_client):
    _configure(settings, allow_anonymous_writes=False)
    resp = auth_client.post("/reviews/api/v1/reviews", REVIEW_PAYLOAD, format="json")
    assert resp.status_code == 201, resp.content


# --- open -------------------------------------------------------------------


def test_guest_review_is_allowed_when_opened(settings, anonymous_client):
    _configure(settings, allow_anonymous_writes=True)
    resp = anonymous_client.post(
        "/reviews/api/v1/reviews", REVIEW_PAYLOAD, format="json"
    )
    assert resp.status_code == 201, resp.content
    assert resp.data["rating"] == 5


def test_registered_user_is_allowed_while_open(settings, auth_client):
    """The switch must not catch anybody it was not aimed at."""
    _configure(settings, allow_anonymous_writes=True)
    resp = auth_client.post("/reviews/api/v1/reviews", REVIEW_PAYLOAD, format="json")
    assert resp.status_code == 201, resp.content


# --- reads stay open in both positions --------------------------------------


@pytest.mark.parametrize("allow", [False, True])
def test_guest_can_still_list_reviews(settings, anonymous_client, allow):
    _configure(settings, allow_anonymous_writes=allow)
    resp = anonymous_client.get("/reviews/api/v1/reviews", TARGET)
    assert resp.status_code == 200, resp.content


@pytest.mark.parametrize("allow", [False, True])
def test_guest_can_still_read_the_aggregate(settings, anonymous_client, allow):
    _configure(settings, allow_anonymous_writes=allow)
    resp = anonymous_client.get("/reviews/api/v1/reviews/aggregate", TARGET)
    assert resp.status_code == 200, resp.content

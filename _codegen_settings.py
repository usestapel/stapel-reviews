"""Single-module Django settings for stapel-reviews's harnesses.

Single source of truth for the ``settings.configure(...)`` block shared by:

  - the pytest suite (``conftest.py``) — mounts reviews on its *bare* test
    urlconf (``stapel_reviews.tests.urls`` -> ``reviews/`` -> the module's own
    ``api/reviews`` etc.); and
  - the contract-emission harness (``_codegen.py`` / ``make contract``) — mounts
    reviews on its *canonical* public API prefix (``stapel_reviews.codegen_urls``
    -> ``reviews/`` -> same ``api/*`` paths the module's own ``urls.py`` already
    declares) and enables drf-spectacular, so the emitted ``schema.json`` /
    ``flows.json`` paths are byte-identical to what a host mounting this module
    would serve (contract-pipeline.md §2).

Keeping one copy here means the harness and the tests can never drift in their
``INSTALLED_APPS`` / mock config (contract-pipeline.md §3). Copied from
stapel-calendar's etalon; tailored to this module (no gdpr/social_django/JWT/
Twilio — reviews carries none of that, but it does need the in-process comm bus
+ schema validation the conftest configures).
"""
from __future__ import annotations


def settings_kwargs(
    *,
    root_urlconf: str = "stapel_reviews.tests.urls",
    contract: bool = False,
) -> dict:
    """Return the ``settings.configure(**kwargs)`` for a single-module reviews
    instance.

    ``root_urlconf`` selects the mount: bare (``stapel_reviews.tests.urls``) for
    the test suite, canonical-prefix (``stapel_reviews.codegen_urls``, same
    ``reviews/`` mount) for contract emission — the module's own ``urls.py``
    already bakes ``api/`` into every path, so both resolve to identical paths;
    the codegen one exists only to add drf-spectacular into the mix.

    ``contract=True`` swaps in the *production* ``REST_FRAMEWORK`` (the canonical
    stapel-core config, inlined as plain dotted paths). DRF caches
    ``REST_FRAMEWORK`` on first access, so it must be right at ``configure()``
    time. The test suite keeps DRF's own defaults (``contract=False``).

    ``SPECTACULAR_SETTINGS`` is deliberately not set (drf-spectacular freezes its
    singleton at import); the one knob still forced — ``SCHEMA_PATH_PREFIX`` — is
    patched on the singleton directly by the harness (see ``_codegen._configure``).
    """
    if contract:
        rest_framework = {
            "DEFAULT_AUTHENTICATION_CLASSES": [
                "stapel_core.django.jwt.authentication.JWTCookieAuthentication",
            ],
            "DEFAULT_PERMISSION_CLASSES": [
                "stapel_core.django.api.permissions.IsServiceRequest",
                "stapel_core.django.api.permissions.IsSuperUser",
            ],
            "DEFAULT_RENDERER_CLASSES": [
                "rest_framework.renderers.JSONRenderer",
                "rest_framework.renderers.BrowsableAPIRenderer",
            ],
            "DEFAULT_SCHEMA_CLASS": "stapel_core.django.openapi.schemas.PermissionAwareAutoSchema",
            "EXCEPTION_HANDLER": "stapel_core.django.api.errors.stapel_exception_handler",
        }
    else:
        rest_framework = None

    kwargs = dict(
        SECRET_KEY="test-secret-key-not-for-production",
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
            "django.contrib.admin",
            "django.contrib.messages",
            "stapel_core.django.apps.CommonDjangoConfig",
            "stapel_core.django.users",
            "rest_framework",
            "drf_spectacular",
            "stapel_reviews",
        ],
        AUTH_USER_MODEL="users.User",
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        USE_TZ=True,
        ROOT_URLCONF=root_urlconf,
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            }
        },
        # Synchronous in-process comm with schema validation ON, so the
        # committed contracts in schemas/ are enforced by the tests.
        STAPEL_BUS_BACKEND="stapel_core.bus.backends.memory.MemoryBus",
        STAPEL_COMM={
            "OUTBOX_ENABLED": False,
            "ACTION_TRANSPORT": "inprocess",
            "VALIDATE_SCHEMAS": True,
        },
        MIGRATION_MODULES={
            "users": None,
            "reviews": None,
        },
    )
    if rest_framework is not None:
        kwargs["REST_FRAMEWORK"] = rest_framework
    return kwargs


#: The multi-module common path prefix drf-spectacular auto-detects in a
#: multi-module aggregate. Forced on the drf-spectacular settings singleton by
#: the harness so a single-module instance derives the same style of
#: operationIds. Uniform across all pair-backends.
CODEGEN_SCHEMA_PATH_PREFIX = "/"

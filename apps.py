from django.apps import AppConfig


class ReviewsConfig(AppConfig):
    name = "stapel_reviews"
    label = "reviews"
    verbose_name = "Reviews and ratings"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Import-time side effects: comm functions/actions, system checks,
        # error-key registration. Keep each in its own module.
        from . import actions  # noqa: F401
        from . import checks  # noqa: F401
        from . import errors  # noqa: F401
        from . import functions  # noqa: F401

        # GDPR: register the per-app data handler (monolith in-process mode).
        from stapel_core.gdpr import gdpr_registry

        from .gdpr import ReviewsGDPRProvider

        if not any(p.section == "reviews" for p in gdpr_registry.providers):
            gdpr_registry.register(ReviewsGDPRProvider())

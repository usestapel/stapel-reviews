"""Admin registrations for stapel-reviews (observability; kept minimal)."""
from django.contrib import admin

from .models import Response, Review


class ResponseInline(admin.StackedInline):
    model = Response
    extra = 0
    fields = ("author", "body")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("target_type", "target_key", "author", "rating", "status", "created_at")
    list_filter = ("target_type", "status", "rating")
    search_fields = ("target_type", "target_key")
    date_hierarchy = "created_at"
    inlines = [ResponseInline]


@admin.register(Response)
class ResponseAdmin(admin.ModelAdmin):
    list_display = ("review", "author", "created_at")

from django.urls import include, path

urlpatterns = [
    path("reviews/", include("stapel_reviews.urls")),
]

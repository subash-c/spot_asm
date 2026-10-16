from django.urls import path

from .views import health, places, plan

urlpatterns = [
    path("api/health", health),
    path("api/places", places),
    path("api/plan", plan),
]

from django.urls import path

from .views import finish, health, places, plan, prepare

urlpatterns = [
    path("api/health", health),
    path("api/places", places),
    path("api/plan", plan),
    path("api/prepare", prepare),
    path("api/finish", finish),
]

from django.urls import include, path, re_path

from .frontend import frontend

urlpatterns = [
    path("", include("planner.urls")),
    re_path(r"^(?!api/)(?P<path>.*)$", frontend),
]

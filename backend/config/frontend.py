from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.views.static import serve


def frontend(request, path=""):
    """Serve the built React app. API routes are registered separately."""
    root = Path(settings.FRONTEND_DIST) if settings.FRONTEND_DIST else None
    index = root / "index.html" if root else None
    if index is None or not index.is_file():
        return HttpResponse(
            "SafeRoute UI is not built. In development, open the Vite server on port 5173.",
            status=404,
            content_type="text/plain",
        )
    asset = root / path
    if path and asset.is_file():
        return serve(request, path, document_root=root)
    return serve(request, "index.html", document_root=root)

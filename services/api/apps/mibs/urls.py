from django.urls import path, re_path

from .views import (
    MibDetailView, MibListView, MibReloadView, MibResolveView, MibUploadView,
)

urlpatterns = [
    path("", MibListView.as_view(), name="mibs-list"),
    path("upload/", MibUploadView.as_view(), name="mibs-upload"),
    # OID may contain dots → match greedily.
    re_path(r"^resolve/(?P<oid>[0-9.]+)/?$", MibResolveView.as_view(), name="mibs-resolve"),
    path("<str:name>/reload/", MibReloadView.as_view(), name="mibs-reload"),
    path("<str:name>/", MibDetailView.as_view(), name="mibs-detail"),
]

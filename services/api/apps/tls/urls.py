from django.urls import path

from .views import SSLCSRView, SSLSelfSignedView, SSLStatusView, SSLUploadView

urlpatterns = [
    path("ssl/",             SSLStatusView.as_view(),     name="ssl-status"),
    path("ssl/self-signed/", SSLSelfSignedView.as_view(), name="ssl-self-signed"),
    path("ssl/csr/",         SSLCSRView.as_view(),        name="ssl-csr"),
    path("ssl/upload/",      SSLUploadView.as_view(),     name="ssl-upload"),
]

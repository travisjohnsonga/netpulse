from django.urls import path

from .views import (
    CACertificateDetailView,
    CACertificateListView,
    CACertificateVerifyView,
    SSLCSRView,
    SSLSelfSignedView,
    SSLStatusView,
    SSLUploadView,
)

urlpatterns = [
    path("ssl/",             SSLStatusView.as_view(),     name="ssl-status"),
    path("ssl/self-signed/", SSLSelfSignedView.as_view(), name="ssl-self-signed"),
    path("ssl/csr/",         SSLCSRView.as_view(),        name="ssl-csr"),
    path("ssl/upload/",      SSLUploadView.as_view(),     name="ssl-upload"),
    # Trusted CA certificates
    path("ssl/ca-certs/",            CACertificateListView.as_view(),   name="ssl-ca-certs"),
    path("ssl/ca-certs/<int:pk>/",   CACertificateDetailView.as_view(), name="ssl-ca-cert-detail"),
    path("ssl/ca-certs/<int:pk>/verify/", CACertificateVerifyView.as_view(), name="ssl-ca-cert-verify"),
]

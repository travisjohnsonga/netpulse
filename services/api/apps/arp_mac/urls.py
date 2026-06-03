from django.urls import path

from . import views

urlpatterns = [
    path("devices/<int:device_id>/arp/", views.DeviceARPView.as_view(), name="device-arp"),
    path("devices/<int:device_id>/mac/", views.DeviceMACView.as_view(), name="device-mac"),
    path("devices/<int:device_id>/arp-mac/collect/", views.DeviceARPMACCollectView.as_view(),
         name="device-arp-mac-collect"),
    path("network/search/", views.NetworkSearchView.as_view(), name="network-search"),
    path("network/mac-vendor/<str:mac>/", views.MACVendorView.as_view(), name="mac-vendor"),
]

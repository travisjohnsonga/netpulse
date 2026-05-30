from django.contrib import admin

from .models import MonitoredInterface, TelemetryConfig


@admin.register(TelemetryConfig)
class TelemetryConfigAdmin(admin.ModelAdmin):
    list_display = ("device", "primary_method", "snmp_interval", "gnmi_interval")
    search_fields = ("device__hostname",)


@admin.register(MonitoredInterface)
class MonitoredInterfaceAdmin(admin.ModelAdmin):
    list_display = ("device", "if_name", "if_speed_mbps", "last_status",
                    "collection_method", "lldp_neighbor_hostname")
    list_filter = ("collection_method", "last_status")
    search_fields = ("device__hostname", "if_name", "if_description")

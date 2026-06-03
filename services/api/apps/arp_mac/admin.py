from django.contrib import admin

from .models import ARPEntry, MACEntry, MACVendor


@admin.register(ARPEntry)
class ARPEntryAdmin(admin.ModelAdmin):
    list_display = ("ip_address", "mac_address", "device", "interface", "vlan", "collected_at")
    search_fields = ("ip_address", "mac_address")
    list_filter = ("device",)


@admin.register(MACEntry)
class MACEntryAdmin(admin.ModelAdmin):
    list_display = ("mac_address", "vlan", "interface", "entry_type", "device", "collected_at")
    search_fields = ("mac_address",)
    list_filter = ("device", "vlan", "entry_type")


@admin.register(MACVendor)
class MACVendorAdmin(admin.ModelAdmin):
    list_display = ("oui", "vendor", "updated_at")
    search_fields = ("oui", "vendor")

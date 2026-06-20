from django.urls import path

from .views import (
    FlowDeviceSummaryView,
    FlowQueryView,
    FlowResolveClearCacheView,
    FlowResolveView,
    FlowSankeyView,
    FlowSearchView,
    FlowSummaryView,
    TopTalkersView,
)

urlpatterns = [
    path("", FlowQueryView.as_view(), name="flow-query"),
    path("top-talkers/", TopTalkersView.as_view(), name="flow-top-talkers"),
    path("summary/", FlowSummaryView.as_view(), name="flow-summary"),
    path("device-summary/", FlowDeviceSummaryView.as_view(), name="flow-device-summary"),
    path("sankey/", FlowSankeyView.as_view(), name="flow-sankey"),
    path("search/", FlowSearchView.as_view(), name="flow-search"),
    path("resolve/", FlowResolveView.as_view(), name="flow-resolve"),
    path("resolve/clear-cache/", FlowResolveClearCacheView.as_view(), name="flow-resolve-clear-cache"),
]

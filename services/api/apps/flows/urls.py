from django.urls import path

from .views import FlowQueryView, FlowSearchView, FlowSummaryView, TopTalkersView

urlpatterns = [
    path("", FlowQueryView.as_view(), name="flow-query"),
    path("top-talkers/", TopTalkersView.as_view(), name="flow-top-talkers"),
    path("summary/", FlowSummaryView.as_view(), name="flow-summary"),
    path("search/", FlowSearchView.as_view(), name="flow-search"),
]

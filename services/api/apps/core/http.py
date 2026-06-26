"""HTTP cache-control helpers for secret-bearing responses.

`Cache-Control: no-store` keeps responses that carry credentials/tokens out of
browser, proxy, and disk caches — a data-protection hardening control (ISO 27001
A.8.* / general secret handling). Applied narrowly to endpoints that return
secrets, NOT blanket across the API (that would needlessly defeat normal
revalidation for non-sensitive reads)."""


def add_no_store(response):
    """Mark a single response no-store (and not stored by intermediaries)."""
    response["Cache-Control"] = "no-store"
    return response


class NoStoreResponseMixin:
    """View mixin: every response is `Cache-Control: no-store`. Use on views
    whose responses carry secrets (auth tokens, enrollment tokens, certs).
    Must precede the base view class in the MRO."""

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

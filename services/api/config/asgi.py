import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

# Must call get_asgi_application() before importing channels or any app code
# so the Django AppRegistry is fully populated first.
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from apps.core.routing import websocket_urlpatterns  # noqa: E402
from apps.core.ws_auth import JWTAuthMiddleware  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # JWTAuthMiddleware sets scope["user"] from the token the SPA sends as a
        # WebSocket subprotocol; AuthMiddlewareStack still provides a session
        # user (+ AnonymousUser default). Consumers reject anonymous users.
        "websocket": AuthMiddlewareStack(JWTAuthMiddleware(URLRouter(websocket_urlpatterns))),
    }
)

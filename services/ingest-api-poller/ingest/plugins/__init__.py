from .meraki import MerakiPlugin
from .mist import MistPlugin
from .unifi import UniFiPlugin

PLUGIN_REGISTRY: dict[str, type] = {
    "meraki": MerakiPlugin,
    "mist": MistPlugin,
    "unifi": UniFiPlugin,
}

__all__ = ["MerakiPlugin", "MistPlugin", "UniFiPlugin", "PLUGIN_REGISTRY"]

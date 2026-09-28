"""Use the WaveForms library of another machine over TCP (for example a Windows host from a container).

`ad3-bench-server` runs next to the hardware; `RemoteDwfApi` (or `AnalogDiscovery3(remote=...)`, the
`AD3_REMOTE` variable, or `pytest --ad3-remote`) connects to it. See `server.py` and `wire.py`.
"""

from .client import RemoteDwfApi, RemoteError
from .wire import DEFAULT_PORT, PROTOCOL_VERSION, parse_address

__all__ = ["DEFAULT_PORT", "PROTOCOL_VERSION", "RemoteDwfApi", "RemoteError", "parse_address"]

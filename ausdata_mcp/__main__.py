from .runtime import validate_local_runtime
from .server import server

validate_local_runtime()
server.run(transport="stdio")

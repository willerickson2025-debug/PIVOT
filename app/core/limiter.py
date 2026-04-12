from slowapi import Limiter
from slowapi.util import get_remote_address

# Module-level singleton — imported by main.py (wired to app.state)
# and by routes.py (applied as decorators).
limiter = Limiter(key_func=get_remote_address)

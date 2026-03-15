"""
Shared Flask extensions — import from here to avoid circular imports.
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(get_remote_address, default_limits=["200 per minute"], storage_uri="memory://")

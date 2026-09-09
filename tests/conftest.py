import sys
import os

# Ensure backend root directory is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Disable rate limiter during pytest test runs so test suite requests don't hit 429
try:
    from app.core.rate_limiter import limiter
    limiter.enabled = False
except Exception:
    pass


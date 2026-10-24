"""Access policy for the personal NAS deployment."""
import hmac
from functools import wraps
from flask import request
from src.utils.auth_utils import token_required
from src.services.system_config_service import get_transfer_api_key


def require_private_access():
    if request.endpoint in {'auth.login', 'static'}:
        return None
    # API integrations must explicitly configure a key. An empty key grants no access.
    if request.path.startswith('/api/v1/'):
        expected = get_transfer_api_key()
        supplied = request.headers.get('X-API-Key', '')
        if expected and supplied and hmac.compare_digest(supplied, expected):
            return None
    return token_required(lambda: None)()


def private_access_required(view):
    @wraps(view)
    def guarded(*args, **kwargs):
        denied = require_private_access()
        if denied is not None:
            return denied
        return view(*args, **kwargs)
    return guarded

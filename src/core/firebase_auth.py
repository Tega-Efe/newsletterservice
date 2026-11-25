"""
Server-side verification of Firebase ID tokens for the REST API.

The Angular app sends `Authorization: Bearer <Firebase ID token>`. Client-side
checks (route guards, hidden buttons) can always be bypassed, so every
privileged endpoint calls this module to verify - on the server - that the
token is genuine and belongs to an admin who signed in with a second factor.

Usage in views.py (the decorator goes UNDER @api_view):

    @api_view(['POST'])
    @firebase_admin_required
    def my_view(request):
        uid = request.firebase_claims['uid']

What is checked (mirrors firestore.rules / storage.rules):
  1. Signature, expiry, audience and issuer of the ID token (Google's keys).
  2. The signed custom claim `admin` is exactly True (set only through the
     Admin SDK with scripts/set-admin-claim.ts).
  3. The token was issued from a 2FA sign-in
     (`firebase.sign_in_second_factor`), unless FIREBASE_REQUIRE_MFA = False.
  4. Only when FIREBASE_CREDENTIALS_FILE is set: the token has not been
     revoked and the user is not disabled.

Two verification paths, chosen by whether a service-account key is configured:
  - No key (default): verified with google-auth against Google's public keys.
    No credentials needed. Revocation can't be checked (see settings.py).
  - Key set: verified with the Firebase Admin SDK, which also checks
    revocation / disabled users.

It fails CLOSED: if a required library is missing or misconfigured, the
endpoint answers 503 instead of letting the request through.
"""

import functools
import logging
import threading

from django.conf import settings
from rest_framework.response import Response

logger = logging.getLogger(__name__)

try:
    import firebase_admin
    from firebase_admin import auth as fb_auth
    from firebase_admin import credentials as fb_credentials
    FIREBASE_ADMIN_AVAILABLE = True
except ImportError:  # pragma: no cover
    firebase_admin = fb_auth = fb_credentials = None
    FIREBASE_ADMIN_AVAILABLE = False

try:
    from google.auth import exceptions as g_exceptions
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token as g_id_token
    GOOGLE_AUTH_AVAILABLE = True
except ImportError:  # pragma: no cover
    g_exceptions = g_requests = g_id_token = None
    GOOGLE_AUTH_AVAILABLE = False

_APP_NAME = 'restless-society-api'
_app = None
_transport_request = None
_init_lock = threading.Lock()


class AuthFailure(Exception):
    """Raised internally; carries the HTTP status and a client-safe message."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _project_id() -> str:
    project_id = getattr(settings, 'FIREBASE_PROJECT_ID', '')
    if not project_id:
        logger.error("FIREBASE_PROJECT_ID is not set")
        raise AuthFailure(503, 'Authentication service is not configured')
    return project_id


# --------------------------------------------------------------------------
# Path A: service-account key configured -> Firebase Admin SDK
# --------------------------------------------------------------------------
def _get_app(project_id: str):
    """Create (once) the Firebase Admin app. Only used when a key is set."""
    global _app
    if _app is not None:
        return _app

    with _init_lock:
        if _app is not None:
            return _app

        if not FIREBASE_ADMIN_AVAILABLE:
            logger.error("firebase-admin is not installed (pip install firebase-admin)")
            raise AuthFailure(503, 'Authentication service is not available')

        try:
            cred = fb_credentials.Certificate(settings.FIREBASE_CREDENTIALS_FILE)
            _app = firebase_admin.initialize_app(
                cred, {'projectId': project_id}, name=_APP_NAME
            )
        except Exception:
            logger.exception("Could not initialise Firebase Admin app "
                             "(check FIREBASE_CREDENTIALS_FILE)")
            raise AuthFailure(503, 'Authentication service is not configured')
        return _app


def _verify_with_admin_sdk(id_token: str, project_id: str) -> dict:
    app = _get_app(project_id)
    check_revoked = bool(getattr(settings, 'FIREBASE_CHECK_REVOKED', False))
    try:
        return fb_auth.verify_id_token(id_token, app=app, check_revoked=check_revoked)
    except fb_auth.ExpiredIdTokenError:
        raise AuthFailure(401, 'Session expired, please sign in again')
    except fb_auth.RevokedIdTokenError:
        raise AuthFailure(401, 'Session revoked, please sign in again')
    except fb_auth.UserDisabledError:
        raise AuthFailure(403, 'Account disabled')
    except fb_auth.CertificateFetchError:
        # We couldn't reach Google to get the public keys - not the caller's fault.
        logger.exception("Could not fetch Firebase public certificates")
        raise AuthFailure(503, 'Authentication service temporarily unavailable')
    except (fb_auth.InvalidIdTokenError, ValueError) as exc:
        logger.warning("Rejected ID token: %s", exc)
        raise AuthFailure(401, 'Invalid authentication token')
    except Exception:
        logger.exception("Unexpected error verifying Firebase ID token")
        raise AuthFailure(503, 'Authentication service temporarily unavailable')


# --------------------------------------------------------------------------
# Path B: no key -> google-auth against Google's public signing keys
# --------------------------------------------------------------------------
def _get_transport_request():
    """HTTP transport for fetching Google's public keys, with response caching
    so we don't download them on every API call."""
    global _transport_request
    if _transport_request is not None:
        return _transport_request

    with _init_lock:
        if _transport_request is None:
            import requests
            session = requests.Session()
            try:
                import cachecontrol
                session = cachecontrol.CacheControl(session)
            except ImportError:
                logger.warning("cachecontrol not installed; Google's public keys "
                               "will be re-downloaded on every request")
            _transport_request = g_requests.Request(session=session)
        return _transport_request


def _verify_with_google_auth(id_token: str, project_id: str) -> dict:
    if not GOOGLE_AUTH_AVAILABLE:
        logger.error("google-auth is not installed (pip install firebase-admin)")
        raise AuthFailure(503, 'Authentication service is not available')

    if getattr(settings, 'FIREBASE_CHECK_REVOKED', False):
        logger.warning("FIREBASE_CHECK_REVOKED is on but FIREBASE_CREDENTIALS_FILE "
                       "is not set; revocation cannot be checked")

    try:
        claims = g_id_token.verify_firebase_token(
            id_token, _get_transport_request(), audience=project_id
        )
    except g_exceptions.TransportError:
        logger.exception("Could not fetch Firebase public certificates")
        raise AuthFailure(503, 'Authentication service temporarily unavailable')
    except ValueError as exc:  # bad signature, wrong audience, malformed, expired
        logger.warning("Rejected ID token: %s", exc)
        if 'expired' in str(exc).lower():
            raise AuthFailure(401, 'Session expired, please sign in again')
        raise AuthFailure(401, 'Invalid authentication token')
    except Exception:
        logger.exception("Unexpected error verifying Firebase ID token")
        raise AuthFailure(503, 'Authentication service temporarily unavailable')

    # google-auth checks signature, expiry and audience; add what the Admin SDK
    # would also enforce: the issuer must be OUR project, and there must be a user.
    if not claims or claims.get('iss') != f'https://securetoken.google.com/{project_id}':
        logger.warning("Rejected ID token with unexpected issuer: %s", (claims or {}).get('iss'))
        raise AuthFailure(401, 'Invalid authentication token')
    subject = claims.get('sub')
    if not subject or not isinstance(subject, str) or len(subject) > 128:
        raise AuthFailure(401, 'Invalid authentication token')
    claims['uid'] = subject  # the Admin SDK adds this; keep both paths identical
    return claims


# --------------------------------------------------------------------------
def verify_admin_token(id_token: str) -> dict:
    """Verify `id_token` and return its claims, or raise AuthFailure."""
    project_id = _project_id()

    if getattr(settings, 'FIREBASE_CREDENTIALS_FILE', ''):
        claims = _verify_with_admin_sdk(id_token, project_id)
    else:
        claims = _verify_with_google_auth(id_token, project_id)

    # Must be a real admin. `is True` (not truthiness) so a string like "false"
    # or "yes" in a claim can never pass.
    if claims.get('admin') is not True:
        logger.warning("Rejected non-admin token for uid=%s", claims.get('uid'))
        raise AuthFailure(403, 'Admin access required')

    if getattr(settings, 'FIREBASE_REQUIRE_MFA', True):
        second_factor = (claims.get('firebase') or {}).get('sign_in_second_factor')
        if not second_factor:
            logger.warning("Rejected admin token without 2FA for uid=%s", claims.get('uid'))
            raise AuthFailure(403, 'Two-step verification required')

    return claims


def _deny(status: int, message: str) -> Response:
    response = Response({'error': message}, status=status)
    if status == 401:
        response['WWW-Authenticate'] = 'Bearer'
    return response


def firebase_admin_required(view):
    """Decorator: only run `view` for a verified admin (see module docstring).

    On success, the verified token claims are available as
    `request.firebase_claims`.
    """

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        # CORS preflights are answered by corsheaders before reaching here, but
        # never demand a token for OPTIONS in case one does.
        if request.method == 'OPTIONS':
            request.firebase_claims = {}  # nobody is authenticated on a preflight
            return view(request, *args, **kwargs)

        header = request.headers.get('Authorization', '')
        scheme, _, token = header.partition(' ')
        if scheme.lower() != 'bearer' or not token.strip():
            return _deny(401, 'Authentication required')

        try:
            request.firebase_claims = verify_admin_token(token.strip())
        except AuthFailure as failure:
            return _deny(failure.status, failure.message)

        return view(request, *args, **kwargs)

    return wrapper
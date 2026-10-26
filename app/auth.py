"""Login check for the API (AUTH=supabase). The browser signs in with Supabase Auth and sends its access
token; this module checks it locally against the project's public signing keys (fetched once, cached),
so no request waits on Supabase. Projects still on the legacy shared secret need SUPABASE_JWT_SECRET.

A valid token is not enough: the email must also be on ALLOWED_EMAILS (addresses, or "@domain" for a
whole domain), so an account created through an open sign-up page still can't use the station."""
import jwt
from .config import settings


class AuthError(Exception):
    pass


_jwks = None


def _jwks_client():
    global _jwks
    if _jwks is None:
        _jwks = jwt.PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json", cache_keys=True, lifespan=3600)
    return _jwks


def allowed(email):
    email = (email or "").lower()
    if not email:
        return False
    domain = "@" + email.split("@", 1)[-1]
    return email in settings.allowed_emails or domain in settings.allowed_emails


def verify(token):
    """Returns the signed-in email, or raises AuthError saying why not."""
    if not token:
        raise AuthError("Sign in first")
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        opts = dict(audience="authenticated", issuer=f"{settings.supabase_url}/auth/v1", leeway=30)
        if alg in ("ES256", "RS256", "EdDSA"):
            key = _jwks_client().get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=[alg], **opts)
        elif alg == "HS256" and settings.supabase_jwt_secret:
            claims = jwt.decode(token, settings.supabase_jwt_secret, algorithms=["HS256"], **opts)
        else:
            raise AuthError(f"Unsupported token ({alg})")
    except jwt.ExpiredSignatureError:
        raise AuthError("Your session expired. Sign in again.")
    except jwt.PyJWKClientError as e:
        raise AuthError(f"Could not load the sign-in keys from Supabase: {e}")
    except jwt.InvalidTokenError as e:
        raise AuthError(f"Invalid sign-in: {e}")
    email = claims.get("email")
    if not allowed(email):
        raise AuthError(f"{email or 'This account'} is not allowed to use the pack station")
    return email.lower()

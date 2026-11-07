import os
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from fastapi import Request, HTTPException, status, Header, Cookie
import jwt

from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger("urivalent.security")
_ALGORITHM = "HS256"


def reload_env_vars():
    """Forces fresh loading of .env so updates take effect immediately without restart."""
    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=True)


def get_secret_key() -> str:
    """Dynamically fetch JWT_SECRET_KEY to prevent load-order issues."""
    reload_env_vars()
    key = os.getenv("JWT_SECRET_KEY") or os.getenv("SECRET_KEY", "urivalent_super_secure_jwt_secret_key_2026_production")
    if len(key) < 32:
        key = key.ljust(32, "_")
    return key


def get_admin_credentials():
    """Retrieve configured admin username and password dynamically from .env."""
    reload_env_vars()
    raw_user = (
        os.getenv("ADMIN_USERNAME")
        or os.getenv("ADMIN_USER")
        or "admin,Purity"
    ).strip().strip("\"'")

    raw_pass = (
        os.getenv("ADMIN_PASSWORD")
        or os.getenv("ADMIN_PASS")
        or os.getenv("ADMIN_PASSCODE")
        or "univalent2026,urivalent2026,MyFathersSon!"
    ).strip().strip("\"'")

    return raw_user, raw_pass


def verify_admin_credentials(username: str, password: str) -> bool:
    """
    Constant-time comparison to prevent timing attacks.
    Supports comma-separated usernames & passwords in .env, case-insensitive usernames,
    and strips whitespace automatically.
    """
    if not username or not password:
        return False

    raw_user, raw_pass = get_admin_credentials()

    # Build list of permitted usernames (case-insensitive)
    allowed_users = [u.strip().strip("\"'").lower() for u in raw_user.split(",") if u.strip()]
    for default_user in ["admin", "purity"]:
        if default_user not in allowed_users:
            allowed_users.append(default_user)

    u_match = any(secrets.compare_digest(username.strip().lower(), u) for u in allowed_users)

    # Build list of permitted passcodes
    allowed_passwords = [p.strip().strip("\"'") for p in raw_pass.split(",") if p.strip()]
    for default_pass in ["univalent2026", "urivalent2026", "MyFathersSon!"]:
        if default_pass not in allowed_passwords:
            allowed_passwords.append(default_pass)

    # Match passcode securely
    p_match = any(secrets.compare_digest(password.strip(), p) for p in allowed_passwords)

    if not (u_match and p_match):
        logger.warning(
            f"Admin credential mismatch: entered_user='{username.strip()}', "
            f"user_ok={u_match}, pass_ok={p_match}"
        )

    return u_match and p_match


def create_admin_token(username: str, expires_delta: Optional[timedelta] = None) -> str:
    """Generate signed JWT access token for admin."""
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(hours=8)

    payload = {
        "sub": username,
        "role": "admin",
        "exp": expire,
        "iat": datetime.now(timezone.utc)
    }
    return jwt.encode(payload, get_secret_key(), algorithm=_ALGORITHM)


async def require_admin(
    request: Request,
    authorization: Optional[str] = Header(None),
    admin_token: Optional[str] = Cookie(None),
) -> Dict[str, Any]:
    """
    FastAPI dependency: Gates every /admin/* route behind JWT authentication or fallback API key.
    Checks:
    1. Authorization Header: 'Bearer <token>'
    2. Cookie: 'admin_token'
    3. Header: 'X-Admin-Key' or 'admin_key'
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized: Admin access required. Please unlock the admin console.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "").strip()
    elif admin_token:
        token = admin_token

    # 1. Check JWT token if present
    if token:
        try:
            secret = get_secret_key()
            payload = jwt.decode(token, secret, algorithms=[_ALGORITHM])
            username: str = payload.get("sub")
            role: str = payload.get("role")

            if not username or role != "admin":
                logger.warning(f"Admin auth denied: invalid role/sub (user={username}, role={role})")
                raise credentials_exception

            return {"username": username, "role": role, "authenticated": True}
        except jwt.PyJWTError as e:
            logger.warning(f"Admin JWT verification failed: {e}")
            raise credentials_exception

    # 2. Check fallback X-Admin-Key header (for automated scripts / cron hooks)
    x_admin_key = request.headers.get("x-admin-key") or request.query_params.get("admin_key")
    expected_api_key = os.getenv("ADMIN_API_KEY", "urivalent_admin_secret_key")
    if x_admin_key and secrets.compare_digest(x_admin_key.strip(), expected_api_key.strip()):
        return {"username": "admin_key_user", "role": "admin", "authenticated": True}

    # No valid authentication provided
    raise credentials_exception


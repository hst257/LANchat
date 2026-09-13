import json
import os
from functools import lru_cache


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"
FORCE_HTTPS = env_bool("FORCE_HTTPS", IS_PRODUCTION)
COOKIE_SECURE = env_bool("COOKIE_SECURE", IS_PRODUCTION)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "*" if not IS_PRODUCTION else "")
ALLOWED_ORIGINS = env_list("ALLOWED_ORIGINS")
AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
MEDIA_BACKEND = os.getenv("MEDIA_BACKEND", "local").strip().lower()
S3_BUCKET = os.getenv("S3_BUCKET", "").strip()
S3_KMS_KEY_ID = os.getenv("S3_KMS_KEY_ID", "").strip()
S3_PRESIGNED_URL_TTL_SECONDS = int(os.getenv("S3_PRESIGNED_URL_TTL_SECONDS", "900"))
MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", str(8 * 1024 * 1024)))
MAX_VIDEO_BYTES = int(os.getenv("MAX_VIDEO_BYTES", str(50 * 1024 * 1024)))


@lru_cache(maxsize=16)
def get_secret_string(secret_id: str) -> str:
    if not secret_id:
        raise RuntimeError("A Secrets Manager secret ID is required")
    import boto3

    response = boto3.client("secretsmanager", region_name=AWS_REGION).get_secret_value(
        SecretId=secret_id
    )
    if "SecretString" in response:
        return response["SecretString"]
    import base64

    return base64.b64decode(response["SecretBinary"]).decode("utf-8")


def get_secret_object(secret_id: str) -> dict:
    value = get_secret_string(secret_id)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Secret {secret_id!r} must contain a JSON object") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Secret {secret_id!r} must contain a JSON object")
    return parsed


def validate_production_settings() -> None:
    if MEDIA_BACKEND not in {"local", "s3"}:
        raise RuntimeError("MEDIA_BACKEND must be 'local' or 's3'")
    if not IS_PRODUCTION:
        return
    problems = []
    if FORCE_HTTPS and not COOKIE_SECURE:
        problems.append("COOKIE_SECURE must be enabled when FORCE_HTTPS is enabled")
    if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
        problems.append("ALLOWED_HOSTS must contain the public application hostname")
    allowed_schemes = ("https://",) if FORCE_HTTPS else ("http://", "https://")
    if not ALLOWED_ORIGINS or any(
        not value.startswith(allowed_schemes) for value in ALLOWED_ORIGINS
    ):
        schemes = "https://" if FORCE_HTTPS else "http:// or https://"
        problems.append(f"ALLOWED_ORIGINS must contain at least one {schemes} origin")
    if S3_PRESIGNED_URL_TTL_SECONDS < 60 or S3_PRESIGNED_URL_TTL_SECONDS > 3600:
        problems.append("S3_PRESIGNED_URL_TTL_SECONDS must be between 60 and 3600")
    if MEDIA_BACKEND != "s3":
        problems.append("MEDIA_BACKEND must be s3")
    if not S3_BUCKET:
        problems.append("S3_BUCKET is required")
    if not (os.getenv("DATABASE_URL") or os.getenv("DATABASE_SECRET_ID")):
        problems.append("DATABASE_SECRET_ID (recommended) or DATABASE_URL is required")
    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))

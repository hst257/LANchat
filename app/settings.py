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
CLOUDFRONT_DOMAIN = os.getenv("CLOUDFRONT_DOMAIN", "").strip().removeprefix("https://").rstrip("/")
CLOUDFRONT_PUBLIC_KEY_ID = os.getenv(
    "CLOUDFRONT_PUBLIC_KEY_ID", os.getenv("CLOUDFRONT_KEY_PAIR_ID", "")
).strip()
CLOUDFRONT_PRIVATE_KEY_SECRET_ID = os.getenv(
    "CLOUDFRONT_PRIVATE_KEY_SECRET_ID", ""
).strip()
CLOUDFRONT_URL_TTL_SECONDS = int(os.getenv("CLOUDFRONT_URL_TTL_SECONDS", "900"))
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


def cloudfront_private_key() -> str:
    value = get_secret_string(CLOUDFRONT_PRIVATE_KEY_SECRET_ID)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    if isinstance(parsed, dict) and isinstance(parsed.get("private_key"), str):
        return parsed["private_key"]
    raise RuntimeError("CloudFront signing secret must be a PEM string or contain private_key")


def validate_production_settings() -> None:
    if MEDIA_BACKEND not in {"local", "s3"}:
        raise RuntimeError("MEDIA_BACKEND must be 'local' or 's3'")
    if not IS_PRODUCTION:
        return
    problems = []
    if not FORCE_HTTPS or not COOKIE_SECURE:
        problems.append("FORCE_HTTPS and COOKIE_SECURE must be enabled")
    if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
        problems.append("ALLOWED_HOSTS must contain the public application hostname")
    if not ALLOWED_ORIGINS or any(not value.startswith("https://") for value in ALLOWED_ORIGINS):
        problems.append("ALLOWED_ORIGINS must contain at least one https:// origin")
    if CLOUDFRONT_URL_TTL_SECONDS < 60 or CLOUDFRONT_URL_TTL_SECONDS > 3600:
        problems.append("CLOUDFRONT_URL_TTL_SECONDS must be between 60 and 3600")
    if MEDIA_BACKEND != "s3":
        problems.append("MEDIA_BACKEND must be s3")
    for name, value in {
        "S3_BUCKET": S3_BUCKET,
        "CLOUDFRONT_DOMAIN": CLOUDFRONT_DOMAIN,
        "CLOUDFRONT_PUBLIC_KEY_ID": CLOUDFRONT_PUBLIC_KEY_ID,
        "CLOUDFRONT_PRIVATE_KEY_SECRET_ID": CLOUDFRONT_PRIVATE_KEY_SECRET_ID,
    }.items():
        if not value:
            problems.append(f"{name} is required")
    if not (os.getenv("DATABASE_URL") or os.getenv("DATABASE_SECRET_ID")):
        problems.append("DATABASE_SECRET_ID (recommended) or DATABASE_URL is required")
    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))

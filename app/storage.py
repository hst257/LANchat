import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from . import settings


@lru_cache(maxsize=1)
def _s3_client():
    import boto3

    return boto3.client("s3", region_name=settings.AWS_REGION)


def _object_key(prefix: str, extension: str) -> str:
    return f"{prefix.strip('/')}/{secrets.token_hex(24)}{extension.lower()}"


def _encryption_args() -> dict:
    if settings.S3_KMS_KEY_ID:
        return {
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": settings.S3_KMS_KEY_ID,
            "BucketKeyEnabled": True,
        }
    return {"ServerSideEncryption": "AES256"}


def put_media(
    data: bytes,
    extension: str,
    content_type: str,
    prefix: str,
    local_root: Path,
) -> str:
    key = _object_key(prefix, extension)
    if settings.MEDIA_BACKEND == "s3":
        _s3_client().put_object(
            Bucket=settings.S3_BUCKET,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="private, max-age=31536000, immutable",
            **_encryption_args(),
        )
    else:
        path = local_root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return key


def delete_media(key: str | None, local_root: Path) -> None:
    if not key:
        return
    if settings.MEDIA_BACKEND == "s3":
        _s3_client().delete_object(Bucket=settings.S3_BUCKET, Key=key)
    else:
        (local_root / key).unlink(missing_ok=True)


def copy_media(
    source_key: str,
    content_type: str,
    prefix: str,
    local_root: Path,
) -> str:
    target_key = _object_key(prefix, Path(source_key).suffix)
    if settings.MEDIA_BACKEND == "s3":
        _s3_client().copy_object(
            Bucket=settings.S3_BUCKET,
            Key=target_key,
            CopySource={"Bucket": settings.S3_BUCKET, "Key": source_key},
            ContentType=content_type,
            MetadataDirective="REPLACE",
            CacheControl="private, max-age=31536000, immutable",
            **_encryption_args(),
        )
    else:
        source_path = local_root / source_key
        if not source_path.is_file():
            raise FileNotFoundError(source_key)
        target_path = local_root / target_key
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(source_path.read_bytes())
    return target_key


@lru_cache(maxsize=1)
def _cloudfront_signer():
    from botocore.signers import CloudFrontSigner
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(
        settings.cloudfront_private_key().encode("utf-8"), password=None
    )

    def rsa_signer(message: bytes) -> bytes:
        return key.sign(message, padding.PKCS1v15(), hashes.SHA1())

    return CloudFrontSigner(settings.CLOUDFRONT_PUBLIC_KEY_ID, rsa_signer)


def delivery_url(key: str) -> str:
    if settings.MEDIA_BACKEND != "s3":
        raise RuntimeError("CloudFront delivery URLs are only used with S3 storage")
    unsigned = f"https://{settings.CLOUDFRONT_DOMAIN}/{quote(key, safe='/')}"
    expires = datetime.now(timezone.utc) + timedelta(
        seconds=settings.CLOUDFRONT_URL_TTL_SECONDS
    )
    return _cloudfront_signer().generate_presigned_url(
        unsigned, date_less_than=expires
    )

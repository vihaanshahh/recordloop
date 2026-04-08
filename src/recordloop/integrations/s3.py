"""
S3 video storage with pre-signed URLs.

Handles upload, download, and lifecycle management for recorded videos.
No API server needed — clients upload/download directly via pre-signed URLs.

Requires: pip install recordloop[cloud]
"""

import json
import os
from pathlib import Path
from typing import Optional

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    boto3 = None
    ClientError = None


def _require_boto3():
    if boto3 is None:
        raise ImportError(
            "boto3 is required for S3 storage. "
            "Install with: pip install recordloop[cloud]"
        )


def upload_video(
    video_path: Path,
    bucket: str,
    key: str,
    region: str,
    aws_access_key_id: str,
    aws_secret_access_key: str,
    expiry_days: int = 30,
) -> str:
    """
    Upload a video file to S3 and return a pre-signed URL.

    Args:
        video_path: Path to the local video file
        bucket: S3 bucket name
        key: S3 object key (e.g. "videos/session-123.mp4")
        region: AWS region (e.g. "us-east-1")
        aws_access_key_id: AWS access key ID
        aws_secret_access_key: AWS secret access key
        expiry_days: Days until the pre-signed URL expires (default: 30)

    Returns:
        Pre-signed URL for the uploaded video
    """
    _require_boto3()

    video_path = Path(video_path)
    ext = video_path.suffix.lstrip(".")
    content_type = f"video/{ext}" if ext else "video/mp4"

    s3 = boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )

    s3.upload_file(
        str(video_path),
        bucket,
        key,
        ExtraArgs={"ContentType": content_type},
    )

    expiry_seconds = expiry_days * 86400
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expiry_seconds,
    )

    return url


def get_video_url(
    session_id: str,
    bucket: str,
    region: str,
    aws_access_key_id: str,
    aws_secret_access_key: str,
    expires_in: int = 86400,
) -> str:
    """
    Generate a pre-signed URL for viewing a video.

    Args:
        session_id: Session ID
        bucket: S3 bucket name
        region: AWS region
        aws_access_key_id: AWS access key ID
        aws_secret_access_key: AWS secret access key
        expires_in: URL expiry in seconds (default: 24 hours)

    Returns:
        Pre-signed URL string
    """
    _require_boto3()

    s3 = boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )

    for ext in (".mp4", ".webm"):
        key = f"videos/{session_id}{ext}"
        try:
            s3.head_object(Bucket=bucket, Key=key)
            return s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except ClientError:
            continue

    raise FileNotFoundError(f"No video found for session {session_id}")


def get_upload_url(
    session_id: str,
    bucket: str,
    region: str,
    aws_access_key_id: str,
    aws_secret_access_key: str,
    ext: str = ".mp4",
    expires_in: int = 3600,
) -> dict:
    """
    Generate a pre-signed URL for direct upload (bypasses our server).

    Useful for the GitHub Action runner to upload directly to S3.

    Args:
        session_id: Session ID
        bucket: S3 bucket name
        region: AWS region
        aws_access_key_id: AWS access key ID
        aws_secret_access_key: AWS secret access key
        ext: File extension
        expires_in: URL expiry in seconds

    Returns:
        Dict with "url", "key", and "bucket" for the upload
    """
    _require_boto3()

    s3 = boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )

    key = f"videos/{session_id}{ext}"
    url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ContentType": f"video/{ext.lstrip('.')}",
        },
        ExpiresIn=expires_in,
    )

    return {"url": url, "key": key, "bucket": bucket}


def upload_session_metadata(
    session: dict,
    session_id: str,
    bucket: str,
    region: str,
    aws_access_key_id: str,
    aws_secret_access_key: str,
) -> str:
    """
    Upload session metadata JSON to S3.

    Args:
        session: The session dict
        session_id: Session ID
        bucket: S3 bucket name
        region: AWS region
        aws_access_key_id: AWS access key ID
        aws_secret_access_key: AWS secret access key

    Returns:
        The S3 key
    """
    _require_boto3()

    s3 = boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )

    key = f"sessions/{session_id}.json"
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(session, indent=2, default=str),
        ContentType="application/json",
        Metadata={"session_id": session_id},
    )

    return key


def list_sessions(
    bucket: str,
    region: str,
    aws_access_key_id: str,
    aws_secret_access_key: str,
    prefix: str = "sessions/",
    max_results: int = 100,
) -> list:
    """
    List recent sessions from S3.

    Returns:
        List of dicts with "key", "session_id", "last_modified", "size"
    """
    _require_boto3()

    s3 = boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )

    response = s3.list_objects_v2(
        Bucket=bucket,
        Prefix=prefix,
        MaxKeys=max_results,
    )

    sessions = []
    for obj in response.get("Contents", []):
        key = obj["Key"]
        session_id = key.split("/")[-1].replace(".json", "")
        sessions.append({
            "key": key,
            "session_id": session_id,
            "last_modified": obj["LastModified"].isoformat(),
            "size": obj["Size"],
        })

    return sessions


def setup_lifecycle(
    bucket: str,
    region: str,
    aws_access_key_id: str,
    aws_secret_access_key: str,
    expiry_days: int = 30,
):
    """
    Set up S3 lifecycle policy to auto-delete old videos.

    Args:
        bucket: S3 bucket name
        region: AWS region
        aws_access_key_id: AWS access key ID
        aws_secret_access_key: AWS secret access key
        expiry_days: Days before videos are deleted (default: 30)
    """
    _require_boto3()

    s3 = boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )

    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "video-expiry",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "videos/"},
                    "Expiration": {"Days": expiry_days},
                },
                {
                    "ID": "session-expiry",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "sessions/"},
                    "Expiration": {"Days": expiry_days * 2},
                },
            ]
        },
    )

    print(f"Lifecycle set: videos expire after {expiry_days} days, sessions after {expiry_days * 2} days")


def setup_bucket(
    bucket: str,
    region: str,
    aws_access_key_id: str,
    aws_secret_access_key: str,
    expiry_days: int = 30,
):
    """
    Create the S3 bucket if it doesn't exist, with proper config.
    Call this once during initial setup.

    Args:
        bucket: S3 bucket name
        region: AWS region
        aws_access_key_id: AWS access key ID
        aws_secret_access_key: AWS secret access key
        expiry_days: Days before videos are auto-deleted
    """
    _require_boto3()

    s3 = boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )

    try:
        s3.head_bucket(Bucket=bucket)
        print(f"Bucket {bucket} already exists")
    except ClientError:
        create_args = {"Bucket": bucket}
        if region != "us-east-1":
            create_args["CreateBucketConfiguration"] = {
                "LocationConstraint": region
            }
        s3.create_bucket(**create_args)
        print(f"Created bucket {bucket}")

    # Block public access (videos served via pre-signed URLs only)
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )

    # Set CORS for direct browser uploads
    s3.put_bucket_cors(
        Bucket=bucket,
        CORSConfiguration={
            "CORSRules": [
                {
                    "AllowedHeaders": ["*"],
                    "AllowedMethods": ["PUT", "GET"],
                    "AllowedOrigins": ["*"],
                    "ExposeHeaders": ["ETag"],
                    "MaxAgeSeconds": 3600,
                },
            ]
        },
    )

    setup_lifecycle(bucket, region, aws_access_key_id, aws_secret_access_key, expiry_days)
    print(f"Bucket {bucket} configured")

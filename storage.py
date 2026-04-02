"""
S3 video storage with pre-signed URLs.

Handles upload, download, and lifecycle management for recorded videos.
No API server needed — clients upload/download directly via pre-signed URLs.

Requires: pip install boto3
Config via env vars or .env:
  RECORDLOOP_S3_BUCKET=my-recordloop-videos
  RECORDLOOP_S3_REGION=us-east-1
  AWS_ACCESS_KEY_ID=...
  AWS_SECRET_ACCESS_KEY=...
"""

import os
import json
from pathlib import Path
from typing import Optional


def _get_client():
    """Lazy-load boto3 and create S3 client."""
    try:
        import boto3
    except ImportError:
        raise ImportError("boto3 is required for cloud storage. Install with: pip install boto3")

    region = os.environ.get("RECORDLOOP_S3_REGION", "us-east-1")
    return boto3.client("s3", region_name=region)


def _bucket():
    """Get the configured S3 bucket name."""
    bucket = os.environ.get("RECORDLOOP_S3_BUCKET", "")
    if not bucket:
        raise ValueError(
            "RECORDLOOP_S3_BUCKET not set. "
            "Add it to your .env or set it as an environment variable."
        )
    return bucket


def upload_video(local_path: str, session_id: str, content_type: str = "video/mp4") -> str:
    """
    Upload a video file to S3.

    Args:
        local_path: Path to the local video file
        session_id: Session ID (used as the S3 key prefix)
        content_type: MIME type

    Returns:
        The S3 key where the video was stored
    """
    s3 = _get_client()
    bucket = _bucket()
    path = Path(local_path)
    ext = path.suffix or ".mp4"
    key = f"videos/{session_id}{ext}"

    s3.upload_file(
        str(path),
        bucket,
        key,
        ExtraArgs={
            "ContentType": content_type,
            "Metadata": {"session_id": session_id},
        },
    )

    return key


def get_video_url(session_id: str, expires_in: int = 86400) -> str:
    """
    Generate a pre-signed URL for viewing a video.

    Args:
        session_id: Session ID
        expires_in: URL expiry in seconds (default: 24 hours)

    Returns:
        Pre-signed URL string
    """
    s3 = _get_client()
    bucket = _bucket()

    # Try .mp4 first, then .webm
    for ext in (".mp4", ".webm"):
        key = f"videos/{session_id}{ext}"
        try:
            s3.head_object(Bucket=bucket, Key=key)
            return s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except s3.exceptions.ClientError:
            continue

    raise FileNotFoundError(f"No video found for session {session_id}")


def get_upload_url(session_id: str, ext: str = ".mp4", expires_in: int = 3600) -> dict:
    """
    Generate a pre-signed URL for direct upload (bypasses our server).

    Useful for the GitHub Action runner to upload directly to S3.

    Args:
        session_id: Session ID
        ext: File extension
        expires_in: URL expiry in seconds

    Returns:
        Dict with "url", "key", and "fields" for the upload
    """
    s3 = _get_client()
    bucket = _bucket()
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


def upload_session(session: dict, session_id: str) -> str:
    """
    Upload session metadata JSON to S3.

    Args:
        session: The session dict
        session_id: Session ID

    Returns:
        The S3 key
    """
    s3 = _get_client()
    bucket = _bucket()
    key = f"sessions/{session_id}.json"

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(session, indent=2, default=str),
        ContentType="application/json",
        Metadata={"session_id": session_id},
    )

    return key


def list_sessions(prefix: str = "sessions/", max_results: int = 100) -> list[dict]:
    """
    List recent sessions from S3.

    Returns:
        List of dicts with "key", "session_id", "last_modified", "size"
    """
    s3 = _get_client()
    bucket = _bucket()

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


def setup_lifecycle(expiry_days: int = 30):
    """
    Set up S3 lifecycle policy to auto-delete old videos.

    Args:
        expiry_days: Days before videos are deleted (default: 30)
    """
    s3 = _get_client()
    bucket = _bucket()

    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "recordloop-video-expiry",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "videos/"},
                    "Expiration": {"Days": expiry_days},
                },
                {
                    "ID": "recordloop-session-expiry",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "sessions/"},
                    "Expiration": {"Days": expiry_days * 2},
                },
            ]
        },
    )

    print(f"Lifecycle set: videos expire after {expiry_days} days, sessions after {expiry_days * 2} days")


def setup_bucket():
    """
    Create the S3 bucket if it doesn't exist, with proper config.
    Call this once during initial setup.
    """
    s3 = _get_client()
    bucket = _bucket()
    region = os.environ.get("RECORDLOOP_S3_REGION", "us-east-1")

    try:
        s3.head_bucket(Bucket=bucket)
        print(f"Bucket {bucket} already exists")
    except s3.exceptions.ClientError:
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

    setup_lifecycle()
    print(f"Bucket {bucket} configured for RecordLoop")

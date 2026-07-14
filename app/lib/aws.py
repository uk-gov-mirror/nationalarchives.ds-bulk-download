import json

import boto3
from flask import current_app

from tasks.process import BatchManifest


def get_merlin_files_manifest():
    """
    Returns a list of all files in the Merlin directory.
    """

    s3_endpoint = current_app.config.get("S3_ENDPOINT", None)
    s3_client = boto3.client(
        "s3",
        region_name=current_app.config.get("AWS_DEFAULT_REGION"),
        endpoint_url=s3_endpoint,
    )
    manifest_name = f"{current_app.config.get('S3_EXPORT_PREFIX_MERLIN')}/{current_app.config.get('S3_MANIFEST_NAME')}"
    content_object = s3_client.get_object(
        Bucket=current_app.config.get("S3_EXPORT_BUCKET"), Key=manifest_name
    )
    file_content = content_object.get("Body").read().decode("utf-8")
    json_content = json.loads(file_content)

    manifest = BatchManifest.validate(json_content).model_dump(mode="json")
    manifest["items"] = [
        {
            **item,
            "size": s3_client.head_object(
                Bucket=current_app.config.get("S3_EXPORT_BUCKET"), Key=item["file"]
            ).get("ContentLength", 0),
        }
        for item in manifest["items"]
    ]

    return manifest

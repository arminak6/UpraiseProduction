import json

import boto3

from upraise_ai.core.errors import IngestionError


def aws_session(settings):
    path = settings.aws_credentials_file
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return boto3.Session(
                region_name=data.get("region_name", settings.aws_region),
                aws_access_key_id=data["aws_access_key_id"],
                aws_secret_access_key=data["aws_secret_access_key"],
                aws_session_token=data.get("aws_session_token"),
            )
        except (OSError, ValueError, KeyError):
            raise IngestionError("AWS_CREDENTIAL_FILE_INVALID") from None
    return boto3.Session(region_name=settings.aws_region)

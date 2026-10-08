import hashlib
import json

import boto3

from upraise_ai.core.errors import IngestionError
from upraise_ai.storage.operations import configuration_id, observe


def observe_aws_calls(session, settings, identity):
    """SDK events preserve client behavior; capture only fixed component/status codes."""
    model_components = {}
    for key, model_id in (("bedrock_generation", settings.lesson_generation_model_id),
                          ("bedrock_review", settings.lesson_review_model_id),
                          ("bedrock_embeddings", settings.embedding_model_id)):
        model_components.setdefault(model_id, []).append(key)

    def record(components, state, reason):
        observe(settings, components, state, reason, configuration=identity)

    def before(params, context, **kwargs):
        context["upraise_operations_components"] = model_components.get(params.get("modelId"), [])

    def after(http_response, parsed, context, **kwargs):
        components = context.get("upraise_operations_components", [])
        code = parsed.get("Error", {}).get("Code", "")
        if http_response.status_code < 300:
            record(components, "AVAILABLE", "PROVIDER_CALL_SUCCEEDED")
        elif http_response.status_code >= 500:
            record(components, "UNAVAILABLE", "PROVIDER_SERVICE_ERROR")
        elif code in {"AccessDeniedException", "UnrecognizedClientException", "InvalidSignatureException",
                      "ExpiredTokenException", "InvalidClientTokenId"}:
            record(components, "UNAVAILABLE", "PROVIDER_ACCESS_DENIED")
        elif code in {"ThrottlingException", "TooManyRequestsException"}:
            record(components, "UNAVAILABLE", "PROVIDER_THROTTLED")
        # Validation errors describe a bad request, not a service outage.

    def transport(context, **kwargs):
        record(context.get("upraise_operations_components", []), "UNAVAILABLE", "PROVIDER_CONNECTION_FAILED")

    def polly_before(context, **kwargs):
        context["upraise_operations_components"] = ["polly"]

    for operation in ("InvokeModel", "Converse"):
        session.events.register(f"before-parameter-build.bedrock-runtime.{operation}", before)
        session.events.register(f"after-call.bedrock-runtime.{operation}", after)
        session.events.register(f"after-call-error.bedrock-runtime.{operation}", transport)
    session.events.register("before-parameter-build.polly.SynthesizeSpeech", polly_before)
    session.events.register("after-call.polly.SynthesizeSpeech", after)
    session.events.register("after-call-error.polly.SynthesizeSpeech", transport)
    session._upraise_operations_configuration = identity
    return session


def aws_session(settings):
    path = settings.aws_credentials_file
    if path.is_file():
        try:
            raw = path.read_bytes()
            data = json.loads(raw.decode("utf-8"))
            identity = configuration_id(settings, credential_digest=hashlib.sha256(raw).hexdigest())
            session = boto3.Session(
                region_name=data.get("region_name", settings.aws_region),
                aws_access_key_id=data["aws_access_key_id"],
                aws_secret_access_key=data["aws_secret_access_key"],
                aws_session_token=data.get("aws_session_token"),
            )
        except (OSError, ValueError, KeyError):
            raise IngestionError("AWS_CREDENTIAL_FILE_INVALID") from None
    else:
        session = boto3.Session(region_name=settings.aws_region)
        identity = configuration_id(settings, credential_digest="ambient")
    return observe_aws_calls(session, settings, identity)

from starlette.responses import JSONResponse


class UploadLimitMiddleware:
    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/v1/documents":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "INVALID_CONTENT_LENGTH"}, 400)(scope, receive, send)
        if length > self.max_bytes:
            return await JSONResponse({"detail": "PDF_TOO_LARGE"}, 413)(scope, receive, send)
        total = 0

        async def bounded_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > self.max_bytes:
                from upraise_ai.core.errors import IngestionError

                raise IngestionError("PDF_TOO_LARGE", status=413)
            return message

        await self.app(scope, bounded_receive, send)

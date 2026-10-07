from starlette.responses import JSONResponse


class UploadLimitMiddleware:
    def __init__(self, app, max_bytes, interrupt_max_bytes=6_065_536):
        self.app, self.max_bytes, self.interrupt_max_bytes = app, max_bytes, interrupt_max_bytes

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        is_interrupt = path.startswith("/v1/sessions/") and path.endswith("/interrupts")
        if scope["type"] != "http" or (path != "/v1/documents" and not is_interrupt):
            return await self.app(scope, receive, send)
        limit = self.interrupt_max_bytes if is_interrupt else self.max_bytes
        too_large = "INTERRUPT_AUDIO_TOO_LARGE" if is_interrupt else "PDF_TOO_LARGE"
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "INVALID_CONTENT_LENGTH"}, 400)(scope, receive, send)
        if length > limit:
            return await JSONResponse({"detail": too_large}, 413)(scope, receive, send)
        total = 0

        async def bounded_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > limit:
                from upraise_ai.core.errors import IngestionError

                raise IngestionError(too_large, status=413)
            return message

        await self.app(scope, bounded_receive, send)

from fastapi import HTTPException
from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    """Bound streamed bodies as well as declared Content-Length."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        limit = 8 * 1024 * 1024 if scope["path"] == "/api/assets" else 32 * 1024
        headers = dict(scope["headers"])
        try:
            if int(headers.get(b"content-length", b"0")) > limit:
                return await JSONResponse(
                    {"detail": "上传图片不能超过 8 MB，普通请求不能超过 32 KB"}, 413
                )(scope, receive, send)
        except ValueError:
            return await JSONResponse({"detail": "请求长度无效"}, 400)(scope, receive, send)
        consumed = 0

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > limit:
                raise HTTPException(413, "请求内容超过大小限制")
            return message

        await self.app(scope, bounded_receive, send)

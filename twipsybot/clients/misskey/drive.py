from typing import TYPE_CHECKING, Any

import aiohttp

from ...shared.constants import HTTP_OK
from ...shared.exceptions import APIConnectionError

if TYPE_CHECKING:
    from .api import MisskeyAPI

__all__ = ("MisskeyDrive",)


class MisskeyDrive:
    def __init__(self, api: "MisskeyAPI"):
        self._api = api

    async def show_file(self, file_id: str) -> dict[str, Any]:
        return await self._api.make_request("drive/files/show", {"fileId": file_id})

    async def upload_bytes(
        self, data: bytes, *, name: str, content_type: str = "image/png"
    ) -> dict[str, Any]:
        form = aiohttp.FormData()
        form.add_field("i", self._api.access_token)
        form.add_field("name", name)
        form.add_field("file", data, filename=name, content_type=content_type)
        try:
            session: aiohttp.ClientSession = self._api.session
            url = f"{self._api.instance_url}/api/drive/files/create"
            async with self._api.semaphore, session.post(url, data=form) as response:
                return await self._api._process_response(response, "drive/files/create")
        except (aiohttp.ClientError, OSError) as e:
            raise APIConnectionError() from e

    async def fetch_bytes(self, url: str, *, max_bytes: int | None = None) -> bytes:
        try:
            session: aiohttp.ClientSession = self._api.session
            async with self._api.semaphore, session.get(url) as response:
                if response.status != HTTP_OK:
                    await self._api._process_response(response, "drive/files/download")
                    raise APIConnectionError()
                if max_bytes is None:
                    return await response.read()
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.content.iter_chunked(65536):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("file size exceeds limit")
                    chunks.append(chunk)
                return b"".join(chunks)
        except (aiohttp.ClientError, OSError) as e:
            raise APIConnectionError() from e

    async def download_bytes(
        self, file_id: str, *, thumbnail: bool = False, max_bytes: int | None = None
    ) -> bytes:
        info = await self.show_file(file_id)
        url = info.get("thumbnailUrl") if thumbnail else info.get("url")
        if not url:
            raise APIConnectionError()
        return await self.fetch_bytes(url, max_bytes=max_bytes)

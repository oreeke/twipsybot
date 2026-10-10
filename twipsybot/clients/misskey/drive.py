from typing import TYPE_CHECKING, Any

import aiohttp

from ...shared.constants import FETCH_MAX_BYTES
from ...shared.exceptions import APIConnectionError

if TYPE_CHECKING:
    from .api import MisskeyAPI

__all__ = ("MisskeyDrive",)


class MisskeyDrive:
    def __init__(self, api: "MisskeyAPI"):
        self._api = api

    async def show_file(self, file_id: str) -> dict[str, Any]:
        return await self._api.make_read_request(
            "drive/files/show", {"fileId": file_id}
        )

    async def upload_bytes(
        self, data: bytes, *, name: str, content_type: str = "image/png"
    ) -> dict[str, Any]:
        form = aiohttp.FormData()
        form.add_field("name", name)
        form.add_field("file", data, filename=name, content_type=content_type)
        try:
            session: aiohttp.ClientSession = self._api.session
            url = f"{self._api.instance_url}/api/drive/files/create"
            async with (
                self._api.semaphore,
                session.post(
                    url, data=form, headers=self._api.auth_headers
                ) as response,
            ):
                return await self._api._process_response(response, "drive/files/create")
        except (aiohttp.ClientError, OSError) as e:
            raise APIConnectionError() from e

    async def fetch_bytes(self, url: str, *, max_bytes: int | None = None) -> bytes:
        fetcher = self._api.fetcher
        limit = FETCH_MAX_BYTES if max_bytes is None else max_bytes
        try:
            async with self._api.semaphore, fetcher.open(url) as response:
                if response.status != 200:
                    raise self._api._response_error(
                        response, "drive/files/download", ""
                    )
                return await fetcher.read(response, limit)
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

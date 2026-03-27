from pathlib import Path
from typing import Iterator

from anystore import get_store
from anystore.logic.io import stream
from anystore.types import Uri
from anystore.util import ensure_uri as _ensure_uri

from servicelayer import settings
from servicelayer.archive.virtual import VirtualArchive
from servicelayer.archive.util import (
    checksum,
    ensure_path,
    path_content_hash,
    path_prefix,
)


def get_http_headers():
    if settings.ARCHIVE_API_KEY is None:
        raise RuntimeError("Configure `ARCHIVE_API_KEY` for http archive!")
    return {
        "User-Agent": "servicelayer/anystore",
        "X-Api-Key": settings.ARCHIVE_API_KEY,
    }


def ensure_uri(uri: Uri) -> str:
    uri = _ensure_uri(uri)
    if uri.startswith("http"):
        return f"anystore+{uri}"
    return uri


class AnystoreArchive(VirtualArchive):
    """Archive implementation with anystore as backend. Supports all protocols
    handled via `fsspec` (for some need extra installation) as well as http
    api. Set via `ARCHIVE_URI` and `ARCHIVE_TYPE=anystore`"""

    def __init__(self, base_name: str, uri: Uri | None = None):
        uri = uri or settings.ARCHIVE_URI
        if not uri:
            raise RuntimeError("Configure `ARCHIVE_URI` for anystore archive!")
        super().__init__(base_name)
        uri = ensure_uri(uri)
        if uri.startswith("anystore"):
            self.store = get_store(uri, client_kwargs={"headers": get_http_headers()})
        else:
            self.store = get_store(uri)
        self.endpoint_url = settings.ARCHIVE_ENDPOINT_URL

    def archive_file(
        self,
        file_path: Uri,
        content_hash: str | None = None,
        mime_type: str | None = None,
    ) -> str:
        file_path = ensure_path(file_path)
        if content_hash is None:
            content_hash = checksum(file_path)
        if content_hash is None:
            raise RuntimeError(f"No checksum for `{file_path}`")
        key = self._locate_key(content_hash)
        if key is not None:
            return content_hash
        target = f"{path_prefix(content_hash)}/data"
        with open(file_path, "rb") as i:
            with self.store.open(target, "wb") as o:
                stream(i, o)
        return content_hash

    def load_file(
        self,
        content_hash: str,
        file_name: str | None = None,
        temp_path: str | None = None,
    ) -> Path | None:
        key = self._locate_key(content_hash)
        if key is not None:
            path = self._local_path(content_hash, file_name, temp_path)
            with self.store.open(key, "rb") as i:
                with path.open("wb") as o:
                    stream(i, o)
            return path

    def delete_file(self, content_hash: str | None = None) -> None:
        if content_hash is None:
            return
        prefix = path_prefix(content_hash)
        if prefix is None:
            return
        for key in self.store.iterate_keys(prefix):
            self.store.delete(key)

    def list_files(self, prefix: str | None = None) -> Iterator[str]:
        for key in self.store.iterate_keys(path_prefix(prefix)):
            yield path_content_hash(key)

    def generate_url(
        self,
        content_hash: str,
        file_name: str | None,
        mime_type: str | None = None,
        expire: str | None = None,
    ) -> str | None:
        """
        Callers need to add auth if needed for read access
        """
        if not self.endpoint_url:
            return
        key = self._locate_key(content_hash)
        if key is None:
            return
        url = f"{self.endpoint_url}/{key}"
        if file_name:
            url += f"&filename={file_name}"
        if mime_type:
            url += f"&mimetype={mime_type}"
        return url

    def _locate_key(
        self, content_hash: str | None = None, prefix: str | None = None
    ) -> str | None:
        if prefix is None:
            if content_hash is None:
                return
            prefix = path_prefix(content_hash)
            if prefix is None:
                return
        for key in self.store.iterate_keys(prefix):
            return key

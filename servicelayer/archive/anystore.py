from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

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


def http_backend_config():
    if not settings.ARCHIVE_API_KEY or not settings.ARCHIVE_API_SECRET:
        raise RuntimeError(
            "Configure `ARCHIVE_API_KEY` / `ARCHIVE_API_SECRET` for archive!"
        )
    return {
        "User-Agent": "aleph-servicelayer/anystore",
        "X-Api-Key": settings.ARCHIVE_API_KEY,
        "X-Api-Secret": settings.ARCHIVE_API_SECRET,
    }


# Backend-specific kwarg names for response-header overrides on
# `fsspec.AbstractFileSystem.sign`. Per protocol: (mime_type kwarg,
# content-disposition kwarg).
_SIGN_RESPONSE_KWARGS: dict[str, tuple[str, str]] = {
    "s3": ("ResponseContentType", "ResponseContentDisposition"),
    "s3a": ("ResponseContentType", "ResponseContentDisposition"),
    "gs": ("response_type", "response_disposition"),
    "gcs": ("response_type", "response_disposition"),
    "abfs": ("content_type", "content_disposition"),
    "abfss": ("content_type", "content_disposition"),
    "az": ("content_type", "content_disposition"),
    "anystore+http": ("content_type", "content_disposition"),
    "anystore+https": ("content_type", "content_disposition"),
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

    TIMEOUT = 84600

    def __init__(self, base_name: str, uri: Uri | None = None):
        uri = uri or settings.ARCHIVE_URI
        if not uri:
            raise RuntimeError("Configure `ARCHIVE_URI` for anystore archive!")
        super().__init__(base_name)
        uri = ensure_uri(uri)
        if uri.startswith("anystore"):
            self.store = get_store(
                uri,
                backend_config={"client_kwargs": {"headers": http_backend_config()}},
            )
        else:
            self.store = get_store(uri)

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
        file_name: str | None = None,
        mime_type: str | None = None,
        expire: datetime | None = None,
    ) -> str | None:
        """Generate a signed URL via the underlying fsspec backend (s3, gcs,
        azure, ...). Returns ``None`` if the backend does not implement
        signing (e.g. local file, memory, http) or if the file is missing.
        """
        key = self._locate_key(content_hash)
        if key is None:
            return None
        expires_in = self.TIMEOUT
        if expire is not None:
            delta = expire - datetime.utcnow()
            expires_in = int(delta.total_seconds())
        fs_key = self.store._keys.to_fs_key(key)
        sign_kwargs = self._sign_kwargs(file_name, mime_type)
        try:
            return self.store._fs.sign(fs_key, expiration=expires_in, **sign_kwargs)
        except NotImplementedError:
            return None

    def _sign_kwargs(
        self, file_name: str | None, mime_type: str | None
    ) -> dict[str, Any]:
        """Map response-header overrides to backend-specific kwargs accepted
        by ``fsspec.AbstractFileSystem.sign``. Backends without an entry just
        get ``expiration`` — overrides are silently dropped."""
        protocols = self.store._fs.protocol
        if isinstance(protocols, str):
            protocols = (protocols,)
        kwargs: dict[str, Any] = {}
        for protocol in protocols:
            mapping = _SIGN_RESPONSE_KWARGS.get(protocol)
            if mapping is None:
                continue
            mime_kw, disp_kw = mapping
            if mime_type:
                kwargs[mime_kw] = mime_type
            if file_name:
                kwargs[disp_kw] = f"attachment;filename={file_name}"
            if "anystore" in protocol:
                # add key/secret to kwargs and method
                kwargs["key"] = settings.ARCHIVE_API_PRESIGN_KEY
                kwargs["secret"] = settings.ARCHIVE_API_PRESIGN_SECRET
                # sign method, content disposition and mime
                dispo, mime = kwargs.get(disp_kw, ""), kwargs.get(mime_kw, "")
                kwargs["payload"] = f"GET{mime}" + quote(dispo, safe=";=")
                if settings.ARCHIVE_API_PRESIGN_URL:
                    kwargs["base_url"] = settings.ARCHIVE_API_PRESIGN_URL
            break
        return kwargs

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

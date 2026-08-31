from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from anystore import get_store
from anystore.logic.io import stream
from anystore.types import Uri
from anystore.util import ensure_uri

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
        "User-Agent": "aleph-servicelayer/putfs",
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
    "putfs": ("content_type", "content_disposition"),
}


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
        if uri.startswith("putfs://"):
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
        get ``expiration`` — overrides are silently dropped.

        For the PutFS backend, build the kwargs in the PutFS shape
        (``c``/``d``/``f`` query args, with ``d`` as a disposition keyword and
        ``f`` as a separate filename hint). See
        https://putf.sh/reference/presigned-urls/ for the full design.
        """
        protocols = self.store._fs.protocol
        if isinstance(protocols, str):
            protocols = (protocols,)
        kwargs: dict[str, Any] = {}
        for protocol in protocols:
            mapping = _SIGN_RESPONSE_KWARGS.get(protocol)
            if mapping is None:
                continue
            mime_kw, disp_kw = mapping
            is_putfs = "putfs" in protocol
            if mime_type:
                kwargs[mime_kw] = mime_type
            if file_name:
                if is_putfs:
                    # PutFS-style: disposition is a keyword, filename is a
                    # separate `f` arg that nginx splices into the
                    # Content-Disposition header server-side.
                    kwargs[disp_kw] = "attachment"
                    kwargs["filename"] = file_name
                else:
                    # s3/gcs/azure backends accept the full Content-Disposition
                    # value via their own response-header override kwarg.
                    kwargs[disp_kw] = f"attachment;filename={file_name}"
            if is_putfs:
                # add key/secret to kwargs and method
                kwargs["key"] = settings.ARCHIVE_API_PRESIGN_KEY
                kwargs["secret"] = settings.ARCHIVE_API_PRESIGN_SECRET
                # content type, disposition and filename — must
                # mirror the URL-encoding applied in `PutFSFileSystem.sign` /
                # it's nginx config. Pass `args` as dict in correct order of
                # the sign keys.
                kwargs["args"] = {
                    "c": kwargs.pop(mime_kw, ""),
                    "d": kwargs.pop(disp_kw, ""),
                    "f": kwargs.pop("filename", ""),
                }
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

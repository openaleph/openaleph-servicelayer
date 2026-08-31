import base64
import hashlib
import os
import shutil
import tempfile
import threading
from importlib import reload
from unittest import TestCase
from urllib.parse import parse_qsl, quote, urlsplit

import uvicorn

from servicelayer import settings
from servicelayer.archive import init_archive
from servicelayer.archive.util import checksum, ensure_path

# The PutFS client reads `PUTFS_HTTPS` once, when `putfs.client.fs` is imported
# (which fsspec does lazily, the first time a `putfs://` uri is resolved). The
# test server below speaks plain http, so this has to be in place before any
# putfs import — hence before the one on the next line.
os.environ["PUTFS_HTTPS"] = "0"

from putfs import api as putfs_api  # noqa: E402


class AnystoreArchiveTestMixin:
    """Shared tests for AnystoreArchive backends."""

    def test_basic_archive(self):
        checksum_ = checksum(self.file)
        assert checksum_ is not None, checksum_
        out = self.archive.archive_file(self.file)
        assert checksum_ == out, (checksum_, out)
        out2 = self.archive.archive_file(self.file)
        assert out == out2, (out, out2)

    def test_basic_archive_with_checksum(self):
        checksum_ = "banana"
        out = self.archive.archive_file(self.file, checksum_)
        assert checksum_ == out, (checksum_, out)

    def test_generate_url(self):
        out = self.archive.archive_file(self.file)
        url = self.archive.generate_url(out, file_name=None)
        assert url is None, url

    def test_publish(self):
        assert not self.archive.can_publish

    def test_load_file(self):
        out = self.archive.archive_file(self.file)
        path = self.archive.load_file(out)
        assert path is not None, path
        assert path.is_file(), path

    def test_cleanup_file(self):
        out = self.archive.archive_file(self.file)
        self.archive.cleanup_file(out)
        path = self.archive.load_file(out)
        assert path.is_file(), path

    def test_list_files(self):
        keys = list(self.archive.list_files())
        assert len(keys) == 0, keys
        out = self.archive.archive_file(self.file)
        keys = list(self.archive.list_files())
        assert len(keys) == 1, keys
        keys = list(self.archive.list_files(prefix=out[:4]))
        assert len(keys) == 1, keys
        assert keys[0] == out, keys
        keys = list(self.archive.list_files(prefix="banana"))
        assert len(keys) == 0, keys

    def test_delete_file(self):
        out = self.archive.archive_file(self.file)
        path = self.archive.load_file(out)
        assert path is not None, path
        self.archive.cleanup_file(out)
        self.archive.delete_file(out)
        path = self.archive.load_file(out)
        assert path is None, path


class AnystoreLocalTest(AnystoreArchiveTestMixin, TestCase):
    def setUp(self):
        self.path = ensure_path(tempfile.mkdtemp(prefix="sltest-anystore-local"))
        self.archive = init_archive("anystore", uri=str(self.path))
        self.file = ensure_path(__file__)

    def tearDown(self):
        if self.path.exists():
            shutil.rmtree(self.path)


class PutFSTest(AnystoreArchiveTestMixin, TestCase):
    """Run the archive against a real PutFS server (`putfs.api`, Starlette)
    served by uvicorn on a random port."""

    def setUp(self):
        self.path = ensure_path(tempfile.mkdtemp(prefix="sltest-putfs"))
        # `putfs.api` resolves its storage root from `PUTFS_ROOT` at import
        # time, so point it at this test's temp dir and re-import.
        os.environ["PUTFS_ROOT"] = str(self.path)
        reload(putfs_api)
        config = uvicorn.Config(
            putfs_api.create_app(), host="127.0.0.1", port=0, log_level="warning"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        while not self.server.started:
            pass
        host, port = self.server.servers[0].sockets[0].getsockname()
        uri = f"putfs://{host}:{port}"
        self._original_api_key = settings.ARCHIVE_API_KEY
        self._original_api_secret = settings.ARCHIVE_API_SECRET
        self._original_presign_key = settings.ARCHIVE_API_PRESIGN_KEY
        self._original_presign_secret = settings.ARCHIVE_API_PRESIGN_SECRET
        settings.ARCHIVE_API_KEY = "test-key"
        settings.ARCHIVE_API_SECRET = "test-secret"
        settings.ARCHIVE_API_PRESIGN_KEY = "test-presign-key"
        settings.ARCHIVE_API_PRESIGN_SECRET = "test-presign-secret"
        self.archive = init_archive("anystore", uri=uri)
        self.file = ensure_path(__file__)

    def tearDown(self):
        settings.ARCHIVE_API_KEY = self._original_api_key
        settings.ARCHIVE_API_SECRET = self._original_api_secret
        settings.ARCHIVE_API_PRESIGN_KEY = self._original_presign_key
        settings.ARCHIVE_API_PRESIGN_SECRET = self._original_presign_secret
        os.environ.pop("PUTFS_ROOT", None)
        self.server.should_exit = True
        self.thread.join(timeout=5)
        if self.path.exists():
            shutil.rmtree(self.path)

    # PutFS supports presigned URLs (see
    # https://putf.sh/reference/presigned-urls/), so override the mixin's
    # "URL is None" expectation that only holds for non-signing backends.
    def test_generate_url(self):
        out = self.archive.archive_file(self.file)

        # No file_name / mime_type → URL has only k/e/t.
        url = self.archive.generate_url(out, file_name=None)
        assert url is not None, url
        parts = urlsplit(url)
        assert parts.scheme == "http", parts.scheme
        assert parts.path.startswith("/_/dl/"), parts.path
        args = dict(parse_qsl(parts.query))
        assert set(args) == {"k", "e", "t"}, args
        assert args["k"] == "test-presign-key", args
        assert "c" not in args and "d" not in args and "f" not in args, args

        # With file_name + mime_type → c/d/f populated; token matches the
        # nginx hash of $secure_link_expires&$request_method&$arg_c&$arg_d
        # &$arg_f&$presign_ip&$uri (the shipped `contrib/putfs.nginx.conf`).
        url = self.archive.generate_url(
            out, file_name="report.pdf", mime_type="application/pdf"
        )
        parts = urlsplit(url)
        args = dict(parse_qsl(parts.query))
        assert args["c"] == "application/pdf", args
        assert args["d"] == "attachment", args
        assert args["f"] == "report.pdf", args
        raw = "&".join(
            (
                args["e"],
                "GET",
                quote("application/pdf", safe="/=;*'"),
                quote("attachment", safe="/=;*'"),
                quote("report.pdf", safe="/=;*'"),
                "",  # $presign_ip: not bound to a client ip
                f"{parts.path} test-presign-secret",
            )
        )
        expected = (
            base64.urlsafe_b64encode(hashlib.md5(raw.encode()).digest())
            .decode()
            .rstrip("=")
        )
        assert args["t"] == expected, (args["t"], expected)

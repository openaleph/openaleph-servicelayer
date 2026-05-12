import shutil
import tempfile
import threading
from unittest import TestCase

import uvicorn
from anystore.api import create_app
from anystore.store import get_store

from servicelayer import settings
from servicelayer.archive import init_archive
from servicelayer.archive.util import checksum, ensure_path


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


class AnystoreApiTest(AnystoreArchiveTestMixin, TestCase):
    def setUp(self):
        self.path = ensure_path(tempfile.mkdtemp(prefix="sltest-anystore-api"))
        store = get_store(str(self.path))
        app = create_app(store=store)
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        while not self.server.started:
            pass
        host, port = self.server.servers[0].sockets[0].getsockname()
        uri = f"anystore+http://{host}:{port}"
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
        self.server.should_exit = True
        self.thread.join(timeout=5)
        if self.path.exists():
            shutil.rmtree(self.path)

    # The AnystoreApiTest backend supports presigned URLs (PutFS layout, see
    # https://putf.sh/reference/presigned-urls/), so override the mixin's
    # "URL is None" expectation that only holds for non-signing backends.
    def test_generate_url(self):
        import base64
        import hashlib
        from urllib.parse import quote, urlsplit, parse_qsl

        out = self.archive.archive_file(self.file)

        # No file_name / mime_type → URL has only k/e/t.
        url = self.archive.generate_url(out, file_name=None)
        assert url is not None, url
        parts = urlsplit(url)
        assert parts.path.startswith("/_/dl/")
        args = dict(parse_qsl(parts.query))
        assert set(args) == {"k", "e", "t"}
        assert args["k"] == "test-presign-key"
        assert "c" not in args and "d" not in args and "f" not in args

        # With file_name + mime_type → c/d/f populated; token matches the
        # no-IP nginx hash of $expires$method$arg_c$arg_d$arg_f$uri.
        url = self.archive.generate_url(
            out, file_name="report.pdf", mime_type="application/pdf"
        )
        parts = urlsplit(url)
        args = dict(parse_qsl(parts.query))
        assert args["c"] == "application/pdf"
        assert args["d"] == "attachment"
        assert args["f"] == "report.pdf"
        raw = (
            f"{args['e']}GET"
            f"{quote('application/pdf', safe='/=')}"
            f"{quote('attachment', safe='/=')}"
            f"{quote('report.pdf', safe='/=')}"
            f"{parts.path} test-presign-secret"
        )
        expected = (
            base64.urlsafe_b64encode(hashlib.md5(raw.encode()).digest())
            .decode()
            .rstrip("=")
        )
        assert args["t"] == expected, (args["t"], expected)

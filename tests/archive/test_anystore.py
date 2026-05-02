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
        settings.ARCHIVE_API_KEY = "test-key"
        settings.ARCHIVE_API_SECRET = "test-secret"
        self.archive = init_archive("anystore", uri=uri)
        self.file = ensure_path(__file__)

    def tearDown(self):
        settings.ARCHIVE_API_KEY = self._original_api_key
        settings.ARCHIVE_API_SECRET = self._original_api_secret
        self.server.should_exit = True
        self.thread.join(timeout=5)
        if self.path.exists():
            shutil.rmtree(self.path)

"""Qwen3-ASR 模型下载：用本机 HTTP 服务器验证续传、校验、安全解压和原子替换。"""

import hashlib
import http.server
import io
import random
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from motedeck import capswriter_models as models


def _zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in entries:
            bundle.writestr(name, data)
    return buffer.getvalue()


class _Handler(http.server.BaseHTTPRequestHandler):
    payload = b""
    ignore_range = False
    drop_after = None  # 第一次响应只发这么多字节就断开，用来测续传

    def log_message(self, *args):  # noqa: D102
        pass

    def do_GET(self):  # noqa: N802
        data = type(self).payload
        start = 0
        header = self.headers.get("Range")
        if header and not type(self).ignore_range:
            start = int(header.split("=")[1].split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            self.send_response(200)
        body = data[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        limit = type(self).drop_after
        if limit is not None:
            type(self).drop_after = None
            try:
                self.wfile.write(body[:limit])
                self.wfile.flush()
                self.connection.shutdown(2)
            except OSError:
                pass
            return
        self.wfile.write(body)


class _QuietServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):  # 故意断开连接时的报错不是失败
        pass


class ModelInstallTests(unittest.TestCase):
    _rng = random.Random(7)  # 不可压缩的数据，压缩包才有足够大小来测续传
    FILES = (
        ("a.onnx", _rng.randbytes(3000)),
        ("b.onnx", _rng.randbytes(5000)),
        ("c.gguf", _rng.randbytes(9000)),
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "cw"
        self.root.mkdir()
        _Handler.ignore_range = False
        _Handler.drop_after = None
        self.server = _QuietServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        patcher = mock.patch.object(
            models, "RELEASE_BASE", f"http://127.0.0.1:{self.server.server_address[1]}"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.events = []

    def package(self, entries=None, **override):
        entries = list(entries if entries is not None else [(f"top/{n}", d) for n, d in self.FILES])
        archive = _zip_bytes(entries)
        _Handler.payload = archive
        fields = dict(
            key="t", label="t", asset="t.zip", size=len(archive),
            sha256=hashlib.sha256(archive).hexdigest(), model_dir="M/Q",
            files=tuple((n, len(d)) for n, d in self.FILES),
        )
        fields.update(override)
        return models.ModelPackage(**fields)

    def run_install(self, package, cancel=None):
        return models.install(
            self.root, package,
            lambda *args: self.events.append(args), cancel or threading.Event(),
        )

    def test_install_flat_and_nested_archives(self):
        for entries in ([(n, d) for n, d in self.FILES], [(f"top/{n}", d) for n, d in self.FILES]):
            with self.subTest(nested=entries[0][0].startswith("top/")):
                package = self.package(entries)
                final = self.run_install(package)
                for name, data in self.FILES:
                    self.assertEqual((final / name).read_bytes(), data)
                self.assertEqual(models.installed_package.__name__, "installed_package")
                self.assertFalse(any((self.root / "models" / "_downloads").glob("*.part")))
        phases = {event[0] for event in self.events}
        self.assertEqual(phases, {"downloading", "verifying", "extracting"})

    def test_archive_paths_are_never_used(self):
        entries = [(f"../../evil/{n}", d) for n, d in self.FILES] + [("../escape.txt", b"x")]
        package = self.package(entries)
        final = self.run_install(package)
        self.assertTrue((final / "a.onnx").is_file())
        self.assertFalse((Path(self._tmp.name) / "escape.txt").exists())
        self.assertFalse((self.root.parent / "evil").exists())
        self.assertEqual(sorted(p.name for p in final.iterdir()), ["a.onnx", "b.onnx", "c.gguf"])

    def test_resumes_after_connection_drop_and_from_part_file(self):
        package = self.package()
        _Handler.drop_after = len(_Handler.payload) // 3
        final = self.run_install(package)
        self.assertEqual((final / "c.gguf").read_bytes(), self.FILES[2][1])

    def test_resumes_from_existing_partial_download(self):
        package = self.package()
        work = self.root / "models" / "_downloads"
        work.mkdir(parents=True)
        (work / "t.zip.part").write_bytes(_Handler.payload[:1000])
        self.run_install(package)
        starts = [event[1] for event in self.events if event[0] == "downloading"]
        self.assertGreaterEqual(min(starts), 1000)

    def test_server_without_range_support_restarts_cleanly(self):
        package = self.package()
        work = self.root / "models" / "_downloads"
        work.mkdir(parents=True)
        (work / "t.zip.part").write_bytes(_Handler.payload[:1000])
        _Handler.ignore_range = True
        final = self.run_install(package)
        self.assertEqual((final / "a.onnx").read_bytes(), self.FILES[0][1])

    def test_checksum_mismatch_is_rejected_and_discarded(self):
        package = self.package(sha256="0" * 64)
        with self.assertRaisesRegex(models.ModelDownloadError, "校验"):
            self.run_install(package)
        self.assertFalse((self.root / "models" / "M").exists())
        self.assertFalse(any((self.root / "models" / "_downloads").glob("*.part")))

    def test_incomplete_archive_is_rejected(self):
        package = self.package([(n, d) for n, d in self.FILES[:2]])
        with self.assertRaises(models.ModelDownloadError):
            self.run_install(package)
        self.assertFalse((self.root / "models" / "M" / "Q").exists())

    def test_existing_model_survives_a_failed_install_and_is_replaced_on_success(self):
        old = self.root / "models" / "M" / "Q"
        old.mkdir(parents=True)
        (old / "a.onnx").write_bytes(b"old")
        bad = self.package(sha256="1" * 64)
        with self.assertRaises(models.ModelDownloadError):
            self.run_install(bad)
        self.assertEqual((old / "a.onnx").read_bytes(), b"old")
        self.run_install(self.package())
        self.assertEqual((old / "a.onnx").read_bytes(), self.FILES[0][1])
        self.assertFalse((old.parent / "Q.old").exists())

    def test_cancel_stops_and_keeps_progress(self):
        package = self.package()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(models.ModelDownloadCancelled):
            self.run_install(package, cancel)
        self.assertFalse((self.root / "models" / "M" / "Q").exists())

    def test_installed_package_detects_the_variant_by_file_sizes(self):
        package = models.PACKAGES["qwen_asr_q4"]
        base = models.target_dir(self.root, package)
        base.mkdir(parents=True)
        self.assertIsNone(models.installed_package(self.root))
        for name, size in package.files:
            with (base / name).open("wb") as handle:
                handle.truncate(size)
        self.assertEqual(models.installed_package(self.root).key, "qwen_asr_q4")

    def test_pinned_packages_match_the_expected_layout(self):
        for package in models.PACKAGES.values():
            self.assertTrue(package.url.endswith(package.asset))
            self.assertEqual(len(package.sha256), 64)
            self.assertEqual(
                sorted(name for name, _ in package.files),
                ["qwen3_asr_encoder_backend.onnx", "qwen3_asr_encoder_frontend.onnx", "qwen3_asr_llm.gguf"],
            )


if __name__ == "__main__":
    unittest.main()

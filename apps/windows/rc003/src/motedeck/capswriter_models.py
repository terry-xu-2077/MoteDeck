"""在应用里下载 CapsWriter 的 Qwen3-ASR 模型（断点续传、校验、安全解压）。

模型从 CapsWriter-Offline 官方发布页（``releases/tag/models``）下载，文件大小和
SHA-256 固定在下面的表里：下载完必须对得上才会解压，对不上就丢弃。解压只取
服务实际要读的那几个文件，按文件名落到目标目录，压缩包里的路径一概不采用，
所以不存在路径穿越。已有的模型在新文件完整落盘前不会被动。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

_LOGGER = logging.getLogger(__name__)

RELEASE_BASE = "https://github.com/HaujetZhao/CapsWriter-Offline/releases/download/models"
CHUNK = 1024 * 1024
TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS = 6

PHASE_DOWNLOADING = "downloading"
PHASE_VERIFYING = "verifying"
PHASE_EXTRACTING = "extracting"


class ModelDownloadError(RuntimeError):
    """下载没做成；消息可以直接给用户看。"""


class ModelDownloadCancelled(ModelDownloadError):
    pass


@dataclass(frozen=True)
class ModelPackage:
    key: str
    label: str
    asset: str
    size: int
    sha256: str
    model_dir: str  # 相对 models/ 的目标目录
    files: Tuple[Tuple[str, int], ...]  # (文件名, 大小)：解压必须得到这些

    @property
    def url(self) -> str:
        return f"{RELEASE_BASE}/{self.asset}"


_QWEN_DIR = "Qwen3-ASR/Qwen3-ASR-1.7B"

PACKAGES = {
    "qwen_asr_q4": ModelPackage(
        key="qwen_asr_q4",
        label="标准（q4_k，1.4 GB）",
        asset="Qwen3-ASR-1.7B-q4_k.zip",
        size=1410584449,
        sha256="9b3d2a66a4a26a0404c32085ec838b7c482495a7827919a5aa674de617c2757b",
        model_dir=_QWEN_DIR,
        files=(
            ("qwen3_asr_encoder_backend.onnx", 164740452),
            ("qwen3_asr_encoder_frontend.onnx", 20876699),
            ("qwen3_asr_llm.gguf", 1282434624),
        ),
    ),
    "qwen_asr_q5": ModelPackage(
        key="qwen_asr_q5",
        label="高精度（q5_k，1.95 GB）",
        asset="Qwen3-ASR-1.7B-q5_k.zip",
        size=1951570656,
        sha256="f40040fe62a5ef0c09f8699fdbcb30f18bb8ae2bcd515ed4954e1f62b8b0e88f",
        model_dir=_QWEN_DIR,
        files=(
            ("qwen3_asr_encoder_backend.onnx", 611276553),
            ("qwen3_asr_encoder_frontend.onnx", 24063978),
            ("qwen3_asr_llm.gguf", 1471800896),
        ),
    ),
}

ProgressCallback = Callable[[str, int, int, float], None]  # phase, done, total, bytes/s


def target_dir(root: Path, package: ModelPackage) -> Path:
    return Path(root) / "models" / package.model_dir


def installed_package(root: Path) -> Optional[ModelPackage]:
    """按已落盘文件的大小判断装的是哪一档；对不上任何一档返回 None。"""

    for package in PACKAGES.values():
        base = target_dir(root, package)
        try:
            if all((base / name).stat().st_size == size for name, size in package.files):
                return package
        except OSError:
            continue
    return None


def _sha256_of(path: Path, progress: Optional[ProgressCallback], cancel: Optional[threading.Event]) -> str:
    digest = hashlib.sha256()
    total = path.stat().st_size
    done = 0
    with path.open("rb") as handle:
        while True:
            if cancel is not None and cancel.is_set():
                raise ModelDownloadCancelled("已取消")
            block = handle.read(CHUNK * 4)
            if not block:
                break
            digest.update(block)
            done += len(block)
            if progress:
                progress(PHASE_VERIFYING, done, total, 0.0)
    return digest.hexdigest()


def _download(package: ModelPackage, part: Path, progress: ProgressCallback, cancel: threading.Event) -> None:
    attempt = 0
    while True:
        have = part.stat().st_size if part.exists() else 0
        if have == package.size:
            return
        if have > package.size:
            part.unlink()
            have = 0
        headers = {"User-Agent": "MoteDeck"}
        if have:
            headers["Range"] = f"bytes={have}-"
        request = urllib.request.Request(package.url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                if have and getattr(response, "status", 200) != 206:
                    have = 0  # 服务器不支持续传：从头来
                mode = "ab" if have else "wb"
                started = time.monotonic()
                received = 0
                with part.open(mode) as handle:
                    while True:
                        if cancel.is_set():
                            raise ModelDownloadCancelled("已取消")
                        block = response.read(CHUNK)
                        if not block:
                            break
                        handle.write(block)
                        received += len(block)
                        elapsed = max(time.monotonic() - started, 0.001)
                        progress(PHASE_DOWNLOADING, have + received, package.size, received / elapsed)
            attempt = 0  # 本轮有进展就重置重试计数
        except ModelDownloadCancelled:
            raise
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            attempt += 1
            _LOGGER.info("capswriter model download interrupted: error_type=%s", type(exc).__name__)
            if attempt >= MAX_ATTEMPTS:
                raise ModelDownloadError("网络中断，已保存进度，稍后点击下载可继续") from exc
            if cancel.wait(min(2 * attempt, 10)):
                raise ModelDownloadCancelled("已取消")


def _extract(package: ModelPackage, archive: Path, destination: Path, progress: ProgressCallback, cancel: threading.Event) -> None:
    """只取需要的文件，按文件名写到 destination；不采用压缩包里的任何路径。"""

    wanted = dict(package.files)
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = {Path(info.filename).name: info for info in bundle.infolist() if not info.is_dir()}
            missing = [name for name in wanted if name not in members]
            if missing:
                raise ModelDownloadError("模型压缩包内容不完整")
            total = sum(wanted.values())
            done = 0
            for name, size in package.files:
                info = members[name]
                if info.file_size != size:
                    raise ModelDownloadError("模型压缩包内容与预期不符")
                with bundle.open(info) as source, (destination / name).open("wb") as out:
                    while True:
                        if cancel.is_set():
                            raise ModelDownloadCancelled("已取消")
                        block = source.read(CHUNK)
                        if not block:
                            break
                        out.write(block)
                        done += len(block)
                        progress(PHASE_EXTRACTING, done, total, 0.0)
    except zipfile.BadZipFile as exc:
        raise ModelDownloadError("模型压缩包已损坏") from exc


def install(root: Path, package: ModelPackage, progress: ProgressCallback, cancel: threading.Event) -> Path:
    """下载 → 校验 → 解压 → 原子替换；返回模型目录。调用前必须已停止语音服务。"""

    root = Path(root)
    models = root / "models"
    work = models / "_downloads"
    try:
        work.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ModelDownloadError("无法在 CapsWriter 目录下创建下载目录（没有写入权限？）") from exc
    free = shutil.disk_usage(models).free
    part = work / (package.asset + ".part")
    needed = package.size - (part.stat().st_size if part.exists() else 0) + sum(s for _n, s in package.files)
    if free < needed:
        raise ModelDownloadError(f"磁盘空间不足，还需要约 {needed // (1024 * 1024)} MB")

    _download(package, part, progress, cancel)
    if part.stat().st_size != package.size or _sha256_of(part, progress, cancel) != package.sha256:
        part.unlink(missing_ok=True)
        raise ModelDownloadError("下载的文件校验未通过，已丢弃，请重试")

    staging = work / f"extract-{package.key}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        _extract(package, part, staging, progress, cancel)
        final = target_dir(root, package)
        final.parent.mkdir(parents=True, exist_ok=True)
        backup = final.with_name(final.name + ".old")
        shutil.rmtree(backup, ignore_errors=True)
        if final.exists():
            os.replace(final, backup)
        try:
            os.replace(staging, final)
        except OSError as exc:
            if backup.exists():
                os.replace(backup, final)
            raise ModelDownloadError("无法写入模型目录（文件被占用？请先停止语音服务）") from exc
        shutil.rmtree(backup, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    part.unlink(missing_ok=True)
    return final

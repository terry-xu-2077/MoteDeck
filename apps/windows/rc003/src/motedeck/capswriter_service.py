"""语音模型服务（CapsWriter-Offline）的托管：配置、探测、启停。

CapsWriter 以独立进程运行（它的 server 自带模型进程，推理不能阻塞 MoteDeck）。
MoteDeck 只负责：找到安装目录、读写 ``config_server.py`` 里的模型/端口、启停
``start_server.exe`` / ``start_client.exe``，以及判断服务是否在监听。

安装目录的查找顺序：已保存的设置 -> 随 MoteDeck 打包的 ``capswriter`` 目录
-> 已有的外部安装（首次使用时自动迁移其配置）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from .atomic_files import write_text_atomic as atomic_write_text

_LOGGER = logging.getLogger(__name__)

SETTINGS_FILE = "capswriter.json"
SERVER_EXE = "start_server.exe"
CLIENT_EXE = "start_client.exe"
CONFIG_SERVER = "config_server.py"
DEFAULT_PORT = 6016
KNOWN_EXTERNAL_ROOTS = (r"C:\AIGC_Works\CapsWriter-Offline",)

# model_type -> (显示名, 必需的模型文件相对 models/ 的路径)
MODELS = {
    "qwen_asr": ("Qwen3-ASR 1.7B", "Qwen3-ASR/Qwen3-ASR-1.7B/qwen3_asr_llm.gguf"),
    "fun_asr_nano": ("Fun-ASR-Nano", "Fun-ASR-Nano/Fun-ASR-Nano-GGUF/tokens.txt"),
    "sensevoice": ("SenseVoice-Small", "SenseVoice-Small/Sensevoice-Small-ONNX/tokenizer.bpe.model"),
    "paraformer": (
        "Paraformer",
        "Paraformer/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx/model.onnx",
    ),
}

_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_CONSOLE = 0x00000010
_SW_SHOWMINNOACTIVE = 7


@dataclass
class CapsWriterSettings:
    install_root: str = ""
    auto_start: bool = False
    start_client: bool = True
    migrated: bool = False
    voice_key_created: bool = False


def app_dir() -> Path:
    """MoteDeck 自己所在的目录：便携版里 ``capswriter`` 文件夹就放在 exe 旁边。"""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def settings_path(config_root: Path) -> Path:
    return Path(config_root) / SETTINGS_FILE


def load_settings(config_root: Path) -> CapsWriterSettings:
    try:
        raw = json.loads(settings_path(config_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CapsWriterSettings()
    if not isinstance(raw, dict):
        return CapsWriterSettings()
    base = CapsWriterSettings()
    return CapsWriterSettings(
        install_root=str(raw.get("install_root", base.install_root) or ""),
        auto_start=bool(raw.get("auto_start", base.auto_start)),
        start_client=bool(raw.get("start_client", base.start_client)),
        migrated=bool(raw.get("migrated", base.migrated)),
        voice_key_created=bool(raw.get("voice_key_created", base.voice_key_created)),
    )


def save_settings(config_root: Path, settings: CapsWriterSettings) -> None:
    atomic_write_text(
        settings_path(config_root),
        json.dumps(asdict(settings), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def is_install_root(path: Path) -> bool:
    return (Path(path) / SERVER_EXE).is_file() and (Path(path) / CONFIG_SERVER).is_file()


def candidate_roots(settings: CapsWriterSettings, app_dir: Optional[Path] = None) -> List[Path]:
    roots: List[Path] = []
    if settings.install_root:
        roots.append(Path(settings.install_root))
    if app_dir is not None:
        # 便携版根目录是「MoteDeck x.exe + app\ + capswriter\」，真正的程序在 app\ 里，
        # 所以 exe 所在目录和它的上一级都要找。
        roots.append(Path(app_dir) / "capswriter")
        roots.append(Path(app_dir).parent / "capswriter")
    roots.extend(Path(item) for item in KNOWN_EXTERNAL_ROOTS)
    return roots


def find_install_root(settings: CapsWriterSettings, app_dir: Optional[Path] = None) -> Optional[Path]:
    for root in candidate_roots(settings, app_dir):
        if is_install_root(root):
            return root
    return None


def _read_config_value(root: Path, name: str) -> Optional[str]:
    try:
        text = (Path(root) / CONFIG_SERVER).read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(rf"^\s*{re.escape(name)}\s*=\s*['\"]?([^'\"#\r\n]+?)['\"]?\s*(?:#.*)?$", text, re.M)
    return match.group(1).strip() if match else None


def read_model_type(root: Path) -> str:
    value = _read_config_value(root, "model_type") or ""
    return value if value in MODELS else "qwen_asr"


def read_port(root: Path) -> int:
    try:
        return int(_read_config_value(root, "port") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def write_model_type(root: Path, model_type: str) -> None:
    """只改 ``model_type = '...'`` 这一行，保留文件其余内容（含用户注释）。"""

    if model_type not in MODELS:
        raise ValueError(f"未知的语音模型：{model_type}")
    path = Path(root) / CONFIG_SERVER
    text = path.read_text(encoding="utf-8")
    new_text, count = re.subn(
        r"^(\s*model_type\s*=\s*)['\"][^'\"]*['\"]",
        lambda m: f"{m.group(1)}'{model_type}'",
        text,
        count=1,
        flags=re.M,
    )
    if count != 1:
        raise ValueError("config_server.py 中找不到 model_type")
    atomic_write_text(path, new_text)


def write_port(root: Path, port: int) -> None:
    """只改 ``port = '...'`` 这一行（服务端和客户端的端口要一起改）。"""

    if not 1 <= int(port) <= 65535:
        raise ValueError("端口必须在 1-65535 之间")
    for name in (CONFIG_SERVER, "config_client.py"):
        path = Path(root) / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        new_text, count = re.subn(
            r"^(\s*port\s*=\s*)['\"][^'\"]*['\"]",
            lambda m: f"{m.group(1)}'{int(port)}'",
            text,
            count=1,
            flags=re.M,
        )
        if count == 1:
            atomic_write_text(path, new_text)


def model_installed(root: Path, model_type: str) -> bool:
    entry = MODELS.get(model_type)
    return bool(entry) and (Path(root) / "models" / entry[1]).is_file()


def installed_models(root: Path) -> List[str]:
    return [key for key in MODELS if model_installed(root, key)]


def port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _powershell(script: str, timeout: float = 15.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=_CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def running_pids(root: Path, names: Iterable[str] = (SERVER_EXE, CLIENT_EXE)) -> List[int]:
    """属于该安装目录的 CapsWriter 进程（按可执行文件路径区分，不会误杀别处的同名程序）。"""

    if sys.platform != "win32":
        return []
    paths = [str(Path(root) / name) for name in names]
    conditions = " -or ".join(f"$_.ExecutablePath -eq {_ps_quote(p)}" for p in paths)
    script = (
        "Get-CimInstance Win32_Process | Where-Object { " + conditions + " } "
        "| ForEach-Object { $_.ProcessId }"
    )
    try:
        result = _powershell(script)
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(line) for line in result.stdout.split() if line.strip().isdigit()]


def start_services(root: Path, with_client: bool = True) -> None:
    root = Path(root)
    if not is_install_root(root):
        raise FileNotFoundError("没有找到 CapsWriter 安装目录")
    # CapsWriter 是控制台程序（rich 输出 + 托盘），没有控制台会在加载后退出，
    # 所以和它自己的启动脚本一样：新控制台窗口、最小化、不抢焦点。
    flags = _CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = _SW_SHOWMINNOACTIVE
    server_up = port_listening(read_port(root)) or bool(running_pids(root, (SERVER_EXE,)))
    if not server_up:
        subprocess.Popen([str(root / SERVER_EXE)], cwd=str(root), creationflags=flags, startupinfo=startupinfo, close_fds=True)
        _LOGGER.info("CapsWriter server started from %s", root)
    if with_client and (root / CLIENT_EXE).is_file() and not _client_running(root):
        subprocess.Popen([str(root / CLIENT_EXE)], cwd=str(root), creationflags=flags, startupinfo=startupinfo, close_fds=True)
        _LOGGER.info("CapsWriter client started from %s", root)


def _client_running(root: Path) -> bool:
    return bool(running_pids(root, (CLIENT_EXE,)))


def stop_services(root: Path) -> int:
    """结束该目录下的 server/client 进程，返回结束的进程数。"""

    pids = running_pids(root)
    if not pids:
        return 0
    try:
        _powershell("Stop-Process -Force -Id " + ",".join(str(p) for p in pids))
    except (OSError, subprocess.SubprocessError) as exc:
        _LOGGER.warning("stopping CapsWriter failed: %s", exc)
        return 0
    return len(pids)


def migrate_existing(config_root: Path, app_dir: Optional[Path] = None) -> CapsWriterSettings:
    """首次使用：把已有的 CapsWriter 安装目录登记进设置，模型和 config/热词/LLM 角色原地沿用。"""

    settings = load_settings(config_root)
    if settings.migrated and (not settings.install_root or is_install_root(Path(settings.install_root))):
        return settings
    root = find_install_root(settings, app_dir)
    if root is not None:
        settings.install_root = str(root)
        # 已有安装正在用就保持其运行状态，MoteDeck 之后接管启停。
        settings.auto_start = settings.auto_start or bool(running_pids(root, (SERVER_EXE,)))
    settings.migrated = True
    # 只在内存里登记：没有用户改动就不往配置目录写文件（设置窗口在只读/恢复
    # 模式下也不能多出文件）；之后用户改任何一项设置时才会一起落盘。
    return settings


@dataclass
class ServiceStatus:
    root: str
    found: bool
    running: bool
    model_type: str
    model_label: str
    model_installed: bool
    port: int
    installed_models: List[str]


def status(settings: CapsWriterSettings, app_dir: Optional[Path] = None) -> ServiceStatus:
    root = find_install_root(settings, app_dir)
    if root is None:
        return ServiceStatus("", False, False, "qwen_asr", MODELS["qwen_asr"][0], False, DEFAULT_PORT, [])
    model = read_model_type(root)
    port = read_port(root)
    return ServiceStatus(
        root=str(root),
        found=True,
        running=port_listening(port),
        model_type=model,
        model_label=MODELS[model][0],
        model_installed=model_installed(root, model),
        port=port,
        installed_models=installed_models(root),
    )

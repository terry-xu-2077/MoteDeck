"""把一段 16kHz 单声道 PCM 送给本机 CapsWriter-Offline 服务，取回识别文字。

协议（见 vendor/capswriter/core/protocol.py）：客户端经 WebSocket（子协议
``binary``）发 JSON ``AudioMessage``，音频为 base64 的 float32 采样；最后发一条
``is_final=True`` 的空数据消息，服务端回 ``is_final=True`` 的 ``RecognitionMessage``。
"""

from __future__ import annotations

import array
import base64
import json
import logging
import time
import uuid
from typing import Callable, Optional

from . import capswriter_service as svc

_LOGGER = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHUNK_SECONDS = 5
RESULT_TIMEOUT_SECONDS = 60.0
CONNECT_TIMEOUT_SECONDS = 3.0
TRASH_PUNCTUATION = "，。,."  # 与 CapsWriter 客户端默认的 trash_punc 一致


class CapsWriterAsrError(RuntimeError):
    """识别没做成：服务没启动、连不上、或没听清。"""


def pcm_to_float32_base64(pcm: bytes) -> str:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    floats = array.array("f", (value / 32768.0 for value in samples))
    return base64.b64encode(floats.tobytes()).decode("ascii")


def _message(task_id: str, started: float, data: str, final: bool) -> str:
    return json.dumps(
        {
            "task_id": task_id,
            "source": "mic",
            "data": data,
            "is_final": final,
            "time_start": started,
            "seg_duration": 60,
            "seg_overlap": 4,
            "context": "",
            "language": "auto",
        },
        ensure_ascii=False,
    )


def clean_text(text: str) -> str:
    """去掉末尾标点，和 CapsWriter 客户端的 trash_punc 行为一致。"""

    return str(text or "").strip().rstrip(TRASH_PUNCTUATION).strip()


def transcribe(pcm: bytes, host: str, port: int) -> str:
    if not pcm:
        raise CapsWriterAsrError("没有录到声音")
    try:
        from websockets.sync.client import connect
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise CapsWriterAsrError("缺少 websockets，无法连接语音服务") from exc
    uri = f"ws://{host}:{port}"
    try:
        connection = connect(
            uri,
            subprotocols=["binary"],
            open_timeout=CONNECT_TIMEOUT_SECONDS,
            max_size=None,
            proxy=None,
        )
    except OSError as exc:
        raise CapsWriterAsrError("语音服务未启动，请在设备页启动语音模型服务") from exc
    except Exception as exc:  # noqa: BLE001 - 握手失败一并归为连不上
        raise CapsWriterAsrError("连接语音服务失败") from exc
    task_id = str(uuid.uuid1())
    started = time.time()
    step = SAMPLE_RATE * 2 * CHUNK_SECONDS
    with connection:
        for offset in range(0, len(pcm), step):
            connection.send(
                _message(task_id, started, pcm_to_float32_base64(pcm[offset : offset + step]), False)
            )
        connection.send(_message(task_id, started, "", True))
        deadline = time.monotonic() + RESULT_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CapsWriterAsrError("语音服务识别超时")
            try:
                raw = connection.recv(timeout=remaining)
            except TimeoutError as exc:
                raise CapsWriterAsrError("语音服务识别超时") from exc
            except Exception as exc:  # noqa: BLE001 - 连接中途断开
                raise CapsWriterAsrError("语音服务连接中断") from exc
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if data.get("task_id") != task_id or not data.get("is_final"):
                continue
            text = str(data.get("text", "") or "").strip()
            if not text:
                raise CapsWriterAsrError("没有听清内容")
            return text


class CapsWriterSpeechClient:
    """与 ``LocalSpeechClient`` 同接口：给 ``LocalAsrRunner`` 当识别后端。"""

    def __init__(self, endpoint: Callable[[], Optional[tuple]]) -> None:
        self._endpoint = endpoint

    def transcribe(self, pcm: bytes) -> str:
        target = self._endpoint()
        if target is None:
            raise CapsWriterAsrError("没有找到 CapsWriter，请在设备页指定目录")
        return transcribe(pcm, target[0], target[1])

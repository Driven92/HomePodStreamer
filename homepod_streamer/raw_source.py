# -*- coding: utf-8 -*-
"""绕过 pyatv 的 miniaudio 解码，把裸 PCM 直接交给 RAOP 发送器。

背景：
- pyatv 的 stream_file 默认走 open_source -> miniaudio.stream_any 把输入解码成 PCM，
  再交给 RAOP 发送。对"无限长的实时流"miniaudio 会 init 解码器失败
  （DecodeError: failed to init decoder），导致推流还没开始就退出。
- 而 AirPlay v2 的 RAOP 实际走的是 ChaCha20 加密的裸 PCM（见 airplayv2.send_audio_packet），
  并不需要 ALAC 编解码，因此完全没必要经过 miniaudio。

这里提供一个自定义 AudioSource，直接从 asyncio.StreamReader 读取 WASAPI 环回采集到的
小端 s16 PCM，并复用 pyatv 自己的 _to_audio_samples 字节序处理，确保与文件推流路径一致。
"""
import asyncio
import logging
import sys

import pyatv
from pyatv.protocols.raop.audio_source import (
    AudioSource,
    FRAMES_PER_PACKET,
    _to_audio_samples,
)
from pyatv.support.metadata import EMPTY_METADATA

_LOGGER = logging.getLogger("homepod_streamer.raw")

# 保存原始 open_source，非流式源（文件 / HTTP）仍可回退
_original_open_source = pyatv.protocols.raop.open_source


class RawPcmStreamSource(AudioSource):
    """从一个 asyncio.StreamReader 读取裸 PCM（小端 s16）并直接交给 RAOP。"""

    def __init__(self, reader, sample_rate, channels, sample_size, metadata=EMPTY_METADATA):
        self.reader = reader
        self._sample_rate = sample_rate
        self._channels = channels
        self._sample_size = sample_size
        self._metadata = metadata

    async def readframes(self, nframes):
        # 注意：这个方法每 8ms 就被调用一次（352 帧/包），不要在这里打日志。
        nbytes = nframes * self._sample_size * self._channels
        chunks = []
        remaining = nbytes
        while remaining > 0:
            data = await self.reader.read(remaining)
            if not data:
                break
            chunks.append(data)
            remaining -= len(data)
        if not chunks:
            return AudioSource.NO_FRAMES
        data = b"".join(chunks)
        # 复用 pyatv 的字节序处理，保证与文件推流完全一致
        return _to_audio_samples(data)

    async def get_metadata(self):
        return self._metadata

    @property
    def sample_rate(self):
        return self._sample_rate

    @property
    def channels(self):
        return self._channels

    @property
    def sample_size(self):
        return self._sample_size

    @property
    def duration(self):
        return 0

    async def close(self):
        pass


async def open_source_patched(source, sample_rate, channels, sample_size):
    """替换 pyatv 的 open_source：StreamReader 走裸 PCM，其余回退原始实现。"""
    if isinstance(source, asyncio.StreamReader):
        return RawPcmStreamSource(source, sample_rate, channels, sample_size)
    return await _original_open_source(source, sample_rate, channels, sample_size)

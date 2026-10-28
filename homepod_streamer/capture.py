# -*- coding: utf-8 -*-
"""Windows 系统声音环回采集（基于 soundcard / WASAPI loopback）。

- soundcard 直接用 CFFI 操作 WASAPI，正确处理 WAVE_FORMAT_EXTENSIBLE，
  并返回 numpy float32 数组（已按请求采样率做重采样）。
- 本机默认混音是 48 kHz / 2ch / 32-bit 浮点，soundcard 会重采样到目标采样率（默认 44100），
  与 pyatv RAOP 发送端硬编码的 44100 对齐，避免 RTP 时钟错乱。
- 整个采集在 _run 线程内完成，生成器只从线程安全队列取字节，跨线程安全。
"""
import logging
import queue
import threading

import numpy as np
import soundcard as sc

_LOGGER = logging.getLogger("homepod_streamer.capture")


class WasapiLoopbackCapture:
    """从默认渲染设备的 WASAPI 环回抓取系统音频，输出 16-bit 小端 PCM 字节流。"""

    def __init__(self, samplerate: int = 44100, channels: int = 2, blocksize: int = 512):
        self.target_sr = samplerate          # 输出采样率（固定 44100，匹配 RAOP）
        self.channels = channels
        # soundcard 会把 WASAPI 客户端缓冲区设成 blocksize 对应的时长，
        # 所以这个值直接就是采集侧的一段固定延迟：2048 帧≈46ms，512 帧≈12ms。
        self.blocksize = blocksize
        self.q: "queue.Queue[bytes]" = queue.Queue(maxsize=8)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # 解析默认扬声器对应的环回采集设备
        spk = sc.default_speaker()
        self._mic = sc.get_microphone(id=spk.id, include_loopback=True)
        # 采集来源的名字，界面里显示出来：抓错设备是"没声音"最常见的原因之一
        # （比如系统默认输出被切到 HDMI / 数字输出，而声音实际在另一块声卡上）
        self.source_name = self._mic.name
        _LOGGER.info("loopback source: %s", self._mic.name)

    def _run(self):
        try:
            with self._mic.recorder(
                samplerate=self.target_sr,
                channels=self.channels,
                blocksize=self.blocksize,
            ) as rec:
                while not self._stop.is_set():
                    data = rec.record(numframes=self.blocksize)
                    # data: (frames, ch) float32，范围约 [-1, 1]
                    pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype(np.int16)
                    raw = pcm.tobytes()      # 小端 int16，交错
                    if not raw:
                        continue
                    try:
                        self.q.put_nowait(raw)
                    except queue.Full:
                        # 下游一时跟不上时丢**最旧**的一块而不是最新的：
                        # 丢最新的会让我们永久落后于现实时间，延迟越堆越大。
                        try:
                            self.q.get_nowait()
                            self.q.put_nowait(raw)
                        except (queue.Empty, queue.Full):
                            pass
        except Exception:
            _LOGGER.exception("loopback capture error")
        finally:
            self._stop.set()

    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def generator(self):
        """生成器：持续产出 16-bit 小端 PCM 字节块，直到 stop()。"""
        self.start()
        try:
            while not self._stop.is_set():
                try:
                    data = self.q.get(timeout=0.5)
                    yield data
                except queue.Empty:
                    # 没声音播放时缓冲区可能空，继续等（保持连接）
                    continue
        finally:
            self.stop()


def file_pcm_generator(path: str, chunk: int = 8192):
    """测试用：从一个文件（裸 PCM 或 WAV）持续读取并循环产出。"""
    with open(path, "rb") as f:
        while True:
            data = f.read(chunk)
            if not data:
                f.seek(0)
                data = f.read(chunk)
                if not data:
                    break
            yield data


def silence_generator(samplerate: int = 44100, channels: int = 2, step: int = 8192):
    """测试用：产出静音 PCM（用于验证链路而不发出声音）。"""
    frame_bytes = samplerate * channels * 2
    total = 0
    chunk = b"\x00" * step
    while total < frame_bytes * 30:  # 30 秒静音后结束
        yield chunk
        total += step


# 有声测试音的旋律（C5-E5-G5-E5 上行再回落，听感温和，不像报警音）
_TEST_NOTES = (523.25, 659.25, 783.99, 659.25, 523.25)


def tone_generator(
    samplerate: int = 44100,
    channels: int = 2,
    amplitude: float = 0.22,
    blocksize: int = 512,
):
    """有声测试用：循环播放一段柔和的提示音，直到调用方停止。

    与真实采集一样**按实时速率**产出（每块 512 帧约 11.6ms），这样它送到
    HomePod 的行为与系统声音完全一致——如果测试音能正常响起，说明从 RAOP
    握手到音箱解码的整条链路都是通的，剩下的怀疑范围就只有"采集到的是什么"。
    """
    import time

    sr = samplerate
    note_len, gap_len = 0.40, 0.10
    parts = []
    for freq in _TEST_NOTES:
        n = int(sr * note_len)
        t = np.arange(n, dtype=np.float32) / sr
        # 20ms 淡入 + 120ms 淡出，避免爆音（RAOP 端不做任何渐变处理）
        env = np.minimum(t / 0.02, (note_len - t) / 0.12)
        env = np.clip(env, 0.0, 1.0)
        parts.append(amplitude * env * np.sin(2 * np.pi * freq * t))
        parts.append(np.zeros(int(sr * gap_len), dtype=np.float32))

    wave = np.concatenate(parts).astype(np.float32)
    mono = (np.clip(wave, -1.0, 1.0) * 32767.0).astype(np.int16)
    buf = np.repeat(mono[:, None], channels, axis=1).tobytes()  # 交错 int16 小端

    bytes_per_sec = sr * channels * 2
    block_bytes = blocksize * channels * 2
    pos = 0
    sent = 0
    start = time.monotonic()
    while True:
        if pos + block_bytes > len(buf):
            pos = 0
        chunk = buf[pos:pos + block_bytes]
        pos += block_bytes
        sent += len(chunk)
        delay = start + sent / bytes_per_sec - time.monotonic()
        if delay > 0:
            time.sleep(delay)  # 按实时速率产出，避免灌爆音箱缓冲
        yield chunk

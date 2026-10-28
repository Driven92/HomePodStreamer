# -*- coding: utf-8 -*-
"""HomePod 发现与 RAOP 推流引擎。

核心：用 pyatv（仍在维护、支持新固件）和 HomePod 建立 AirPlay/RAOP 会话，
再把系统音频（裸 PCM 生成器）经 asyncio.StreamReader 喂给它的 stream_file 接口，
由 pyatv 完成编码与 RTP 发送。TuneBlade / AirParrot 卡住的握手层，pyatv 能正常通过。

关键修复：StreamReader 的"喂数据"动作必须在守护事件循环内部执行。
早期用单独线程 `call_soon_threadsafe(reader.feed_data, ...)` 跨线程喂，在
Windows ProactorEventLoop 下不可靠——reader 收不到数据，readframes 永久阻塞，
一个 RTP 音频包都发不出去。现改为在循环内用协程拉取生成器（next 放线程池，
不阻塞循环），直接 `reader.feed_data`，彻底消除跨线程同步问题。
"""
import asyncio
import logging
import threading

import pyatv
from pyatv.interface import MediaMetadata

_LOGGER = logging.getLogger("homepod_streamer")

# pyatv 的 stream_file 默认用 miniaudio 解码输入，对无限长实时流会 init 失败。
# 这里替换为自定义裸 PCM 源：直接从 StreamReader 读 WASAPI 环回的 s16 PCM。
import pyatv.protocols.raop as _raop_pkg
from .raw_source import open_source_patched

_raop_pkg.open_source = open_source_patched

# pyatv 在打开音频源时会强制读流取元数据（计算时长），对"无限长"的实时流会一直读到
# EOF 导致超时、推流中断。而我们已在 stream_file 用 MediaMetadata 自填元数据，因此这里
# 把元数据读取替换为直接返回空元数据，跳过这次网络读取。
import pyatv.protocols.raop.audio_source as _raop_audio_source
from pyatv.support.metadata import EMPTY_METADATA


async def _skip_metadata(_buffer):  # noqa: ANN001
    return EMPTY_METADATA


_raop_audio_source.get_buffered_io_metadata = _skip_metadata


# ---------------------------------------------------------------------------
# 延迟控制
# ---------------------------------------------------------------------------
# pyatv 把 RAOP 的输出缓冲硬编码为 latency = 22050 + sample_rate = 66150 帧
# （44100 Hz 下正好 1.5 秒）。这个值会通过 sync 包告诉音箱"此刻应该播到哪一帧"，
# 是整条链路里最大的一块固定延迟。HomePod 在 SETUP 里接受 latencyMin=11025
# （0.25 秒），所以可以大幅下调；它的 SETUP 响应里 arrivalToRenderLatencyMs 只有
# 70ms，说明设备自身几乎不额外缓冲，缓冲量完全由我们给的 latency 决定。
from pyatv.protocols.raop.protocols import StreamContext  # noqa: E402
from pyatv.protocols.raop.stream_client import StreamClient  # noqa: E402

# 设备声明可接受的下限/上限（帧），超出范围会被忽略或导致异常
LATENCY_MIN_FRAMES = 11025   # 0.25s
LATENCY_MAX_FRAMES = 88200   # 2.0s

DEFAULT_LATENCY_S = 0.5

# GUI / CLI 共用的预设（显示名, 秒）
LATENCY_PRESETS = (
    ("极低 · 0.25s", 0.25),
    ("低 · 0.4s", 0.4),
    ("平衡 · 0.5s", 0.5),
    ("稳定 · 0.8s", 0.8),
    ("保守 · 1.2s", 1.2),
)

_latency_seconds = DEFAULT_LATENCY_S


def configure_latency(seconds) -> float:
    """设置 RAOP 输出缓冲（秒），并返回实际生效值。

    越小延迟越低，但过小时一旦采集/网络抖动就会断续。局域网内 0.4~0.5 秒比较稳妥。
    该值在**下一次开始推流**时生效（每次推流都会重新计算 latency）。
    """
    global _latency_seconds
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return _latency_seconds
    _latency_seconds = max(0.25, min(1.5, value))
    return _latency_seconds


def current_latency() -> float:
    return _latency_seconds


def _latency_frames(sample_rate: int) -> int:
    frames = int(sample_rate * _latency_seconds)
    return max(LATENCY_MIN_FRAMES, min(LATENCY_MAX_FRAMES, frames))


# pyatv 每次开始推流都会在 send_audio 里调用 context.reset() 重置 latency，
# 所以只能在 reset 上挂补丁，实例级赋值会被覆盖。
if not getattr(StreamContext.reset, "_hps_patched", False):
    _orig_context_reset = StreamContext.reset

    def _reset_with_latency(self):  # noqa: ANN001
        _orig_context_reset(self)
        self.latency = _latency_frames(self.sample_rate)

    _reset_with_latency._hps_patched = True
    StreamContext.reset = _reset_with_latency


if not getattr(StreamClient._stream_data, "_hps_patched", False):
    _orig_stream_data = StreamClient._stream_data

    async def _stream_data_from_live(self, source, transport):  # noqa: ANN001
        """开播前丢掉握手期间积压的音频，避免延迟被永久性叠加。

        连接 + SETUP 通常要 1~3 秒，而采集在 start_stream 时就已开始，这段时间的
        音频全堆在 StreamReader 里。等 RECORD 之后一次性灌给音箱，这些"旧声音"
        就变成额外的缓冲——而且因为 sync 映射是以 start_ts 为锚点的，不会自己
        消化掉，会一直挂着。开播前清空一次，直接从"现在"开始播。
        """
        reader = getattr(source, "reader", None)
        dropped = 0
        if reader is not None:
            buf = getattr(reader, "_buffer", None)
            if buf:
                frame_size = getattr(self.context, "frame_size", 4) or 4
                drop = len(buf) - (len(buf) % frame_size)
                if drop > 0:
                    del buf[:drop]
                    dropped = drop
        if dropped:
            bytes_per_sec = self.context.sample_rate * self.context.frame_size
            _LOGGER.info(
                "开播前丢弃积压音频 %.2fs（%d 字节），避免延迟叠加",
                dropped / bytes_per_sec,
                dropped,
            )
        return await _orig_stream_data(self, source, transport)

    _stream_data_from_live._hps_patched = True
    StreamClient._stream_data = _stream_data_from_live


class HomePodStreamer:
    # 缓冲区安全阀（带迟滞，避免频繁误裁造成可听见的断续）：
    # 只有排队音频超过 MAX_BUFFERED_S 才动手，且一次裁回到 TARGET_BUFFERED_S。
    # 正常情况下排队量是毫秒级，远达不到阈值；它主要防的是"采集时钟比发送端
    # 略快，长跑几十分钟后延迟慢慢变大"这种情况。
    MAX_BUFFERED_S = 1.0
    TARGET_BUFFERED_S = 0.5

    def __init__(self, latency: float | None = None, on_error=None, on_connected=None):
        if latency is not None:
            configure_latency(latency)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._task: asyncio.Future | None = None
        self._feed_task: asyncio.Future | None = None
        self._atv = None
        self._capture = None
        self._stop_event = threading.Event()
        self._volume: float | None = None  # 目标音量(0~100)，连接建立后自动应用
        self._trimmed_bytes = 0            # 因超出上限被丢弃的字节数（诊断用）
        # 回调：连接成功 / 推流出错。GUI 通过它们把异步失败反映到界面上，
        # 否则 pyatv.connect 抛异常时界面会一直停在"正在投送"却完全没有声音。
        self._on_error = on_error
        self._on_connected = on_connected

    def set_callbacks(self, on_error=None, on_connected=None):
        """设置状态回调。回调可能在守护事件循环线程里触发，需自行转投 GUI 线程。"""
        if on_error is not None:
            self._on_error = on_error
        if on_connected is not None:
            self._on_connected = on_connected

    @staticmethod
    def _fire(callback, *args):
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("状态回调执行失败")

    # ---- 发现 ----
    async def _scan_async(self, hosts=None, timeout=5):
        atvs = await pyatv.scan(self._loop, hosts=hosts, timeout=timeout)
        devices = []
        for a in atvs:
            services = {s.protocol.name for s in a.services}
            if "RAOP" in services or "AirPlay" in services:
                devices.append({"name": a.name, "address": str(a.address), "conf": a})
        return devices

    def scan(self, hosts=None, timeout=5):
        future = asyncio.run_coroutine_threadsafe(self._scan_async(hosts, timeout), self._loop)
        return future.result(timeout=timeout + 5)

    # ---- 推流 ----
    async def _make_reader(self):
        # 必须在守护事件循环线程内创建 StreamReader，使其绑定到该 loop，
        # 否则在 loop 内 read() 会报 "Future attached to a different loop"。
        return asyncio.StreamReader()

    def _trim_reader(self, reader, sample_rate, channels, sample_bytes):
        """把 reader 里排队的旧音频裁掉，只保留最近 MAX_BUFFERED_S 秒。

        这是长跑时的安全阀：采集端是声卡时钟、发送端是 monotonic 时钟，
        两者存在 ppm 级偏差；若采集略快，缓冲区会缓慢增长、延迟跟着变大。
        裁掉最旧的数据即可把延迟钉死在一个上限内。
        """
        buf = getattr(reader, "_buffer", None)
        if not buf:
            return
        frame_size = channels * sample_bytes
        bytes_per_sec = sample_rate * frame_size
        if len(buf) <= int(self.MAX_BUFFERED_S * bytes_per_sec):
            return                          # 未到阈值，什么都不做
        keep = int(self.TARGET_BUFFERED_S * bytes_per_sec)
        excess = len(buf) - keep
        excess -= excess % frame_size      # 保持整帧对齐，避免左右声道错位
        if excess <= 0:
            return
        del buf[:excess]
        self._trimmed_bytes += excess

    async def _feed_reader(self, reader, gen, sample_rate=44100, channels=2, sample_bytes=2):
        """在守护事件循环内部把生成器的 PCM 喂给 reader。

        生成器的 next() 可能阻塞（例如环回采集在 queue 上等待最多 0.5s），
        故放到默认线程池执行，避免阻塞事件循环；返回后直接在本循环内
        feed_data —— 不再依赖跨线程 call_soon_threadsafe，可靠。
        """
        _LOGGER.debug("feed_reader START (loop=%s)", self._loop)
        fed = 0
        self._trimmed_bytes = 0
        # 用哨兵代替 StopIteration：把 next(gen) 放进线程池时，StopIteration 无法
        # 被塞进 Future（PEP 479），会变成难以排查的 RuntimeError，故用默认值。
        _sentinel = object()
        while not self._stop_event.is_set():
            try:
                chunk = await self._loop.run_in_executor(None, next, gen, _sentinel)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("feed_reader next() failed")
                break
            if chunk is _sentinel:
                _LOGGER.debug("feed_reader generator exhausted")
                break
            if not chunk:
                continue
            reader.feed_data(chunk)
            fed += len(chunk)
            self._trim_reader(reader, sample_rate, channels, sample_bytes)
            # 让出控制权，避免静音/高速源把缓冲区无限撑大
            await asyncio.sleep(0)
        reader.feed_eof()
        _LOGGER.debug(
            "feed_reader done, total fed=%d bytes (trimmed=%d)",
            fed,
            self._trimmed_bytes,
        )

    async def _stream_async(self, conf, reader, sr, ch):
        import traceback

        name = getattr(conf, "name", "设备")
        # 连接必须单独兜住异常：它抢在 stream_file 之前，一旦抛错（例如依赖版本
        # 不兼容、设备被别的设备占用、握手被拒），后续日志一行都不会有，界面却
        # 因为 start_stream 是异步的而显示"正在投送"——表现就是"点了没声音也没报错"。
        try:
            atv = await pyatv.connect(conf, self._loop)
        except asyncio.CancelledError:
            _LOGGER.info("连接已取消")
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("连接 %s 失败", name)
            self._fire(self._on_error, f"无法连接 {name}：{exc}")
            return

        self._atv = atv
        # 连接建立后，把用户设定的目标音量应用一次（保证即使设置早于连接也生效）
        if self._volume is not None:
            try:
                await atv.audio.set_volume(self._volume)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("apply initial volume %s failed", self._volume)
        self._fire(self._on_connected)
        try:
            md = MediaMetadata(title="PC 系统音频", artist="WorkBuddy Streamer", album="Live")
            _LOGGER.info("stream_file starting")
            await atv.stream.stream_file(reader, metadata=md)
            _LOGGER.info("stream_file finished normally")
        except asyncio.CancelledError:
            _LOGGER.info("stream_file cancelled")
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("stream_file failed")
            traceback.print_exc()
            self._fire(self._on_error, f"推流中断：{exc}")
        finally:
            atv.close()
            self._atv = None

    def start_stream(self, conf, source, sr=44100, ch=2, capture=None):
        """启动推流（非阻塞）。

        source: 裸 PCM 生成器（持续产出 16-bit 小端 PCM）。
        capture: 与生成器对应的采集对象（用于停止时一并关闭）。
        """
        if self._task and not self._task.done():
            raise RuntimeError("已有推流进行中，请先停止。")
        self._stop_event.clear()
        self._capture = capture

        # 在守护事件循环内创建 StreamReader，使其绑定到该 loop
        reader = asyncio.run_coroutine_threadsafe(self._make_reader(), self._loop).result()

        # 在守护事件循环内启动 feeder 协程（避免跨线程 feed 不可靠）
        self._feed_task = asyncio.run_coroutine_threadsafe(
            self._feed_reader(reader, source, sr, ch, 2), self._loop
        )

        _LOGGER.info("使用输出缓冲 %.2fs（延迟的主要构成项）", _latency_seconds)
        self._task = asyncio.run_coroutine_threadsafe(
            self._stream_async(conf, reader, sr, ch), self._loop
        )
        return self._task

    def stop_stream(self):
        """停止推流。

        会短暂等待推流任务真正结束，这样上层的"改设置→重连"不会撞上
        "已有推流进行中"的检查。
        """
        self._stop_event.set()
        # 先掐喂数据任务：否则它可能正卡在 next(gen) 上，而 start_stream 里
        # 紧接着就把 stop_event 清空了，老任务会被"复活"继续灌旧的 reader。
        feed, self._feed_task = self._feed_task, None
        if feed is not None and not feed.done():
            feed.cancel()
        if self._capture is not None:
            try:
                self._capture.stop()
            except Exception:
                pass
            self._capture = None
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                task.result(timeout=3)
            except Exception:  # noqa: BLE001  取消/收尾异常都无需上抛
                pass

    # ---- 音量 ----
    async def _set_volume_async(self, vol):
        if self._atv is not None:
            await self._atv.audio.set_volume(vol)

    def set_volume(self, vol: float):
        """设置音量。

        vol 为 **百分比 0~100**（注意：pyatv 的 ``set_volume`` 期望 0~100，
        内部再换算成 -30~0 dBFS；早期误传 0~1 会导致永远接近静音、只能“调小”）。
        非阻塞：投递到守护事件循环后立即返回，避免拖动滑块时卡住界面。
        """
        try:
            vol = max(0.0, min(100.0, float(vol)))
        except (TypeError, ValueError):
            return
        self._volume = vol
        if self._atv is None:
            return  # 尚未连接，连接建立后由 _stream_async 自动应用
        fut = asyncio.run_coroutine_threadsafe(self._set_volume_async(vol), self._loop)

        def _done(f):
            try:
                f.result()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("set_volume(%s) failed: %s", vol, exc)

        fut.add_done_callback(_done)

    def close(self):
        self.stop_stream()

        # 先把循环里残留的任务（pyatv 的连接/心跳/HTTP 会话）取消并等它们收尾，
        # 否则直接 stop 循环会抛 "Task was destroyed but it is pending"、
        # "Unclosed client session"、"Event loop is closed" 等噪音。
        async def _shutdown():
            current = asyncio.current_task()
            pending = [t for t in asyncio.all_tasks() if t is not current]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        try:
            asyncio.run_coroutine_threadsafe(_shutdown(), self._loop).result(timeout=3)
        except Exception:  # noqa: BLE001
            pass

        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=2)

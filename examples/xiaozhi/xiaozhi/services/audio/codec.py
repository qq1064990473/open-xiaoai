import logging
import queue
import sys
import time

import numpy as np
import opuslib
import pyaudio

from xiaozhi.services.audio.stream import MyAudio
from xiaozhi.services.protocols.typing import AudioConfig
from xiaozhi.xiaoai import XiaoAI

logger = logging.getLogger("AudioCodec")


class AudioCodec:
    """音频编解码器类，处理音频的录制和播放"""

    def __init__(self):
        """初始化音频编解码器"""
        self.audio = None
        self.input_stream = None
        self.output_stream = None
        self.opus_encoder = None
        self.opus_decoder = None
        self.audio_decode_queue = queue.Queue()
        self._is_closing = False

        self._initialize_audio()

    def _initialize_audio(self):
        """初始化音频设备和编解码器"""
        try:
            self.audio = MyAudio() if XiaoAI.mode == "xiaoai" else pyaudio.PyAudio()

            # 初始化音频输入流
            self.input_stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=AudioConfig.CHANNELS,
                rate=AudioConfig.SAMPLE_RATE,
                input=True,
                frames_per_buffer=AudioConfig.FRAME_SIZE,
            )

            # 初始化音频输出流
            self.output_stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=AudioConfig.CHANNELS,
                rate=AudioConfig.SAMPLE_RATE,
                output=True,
                frames_per_buffer=AudioConfig.FRAME_SIZE,
            )

            # 初始化Opus编码器
            self.opus_encoder = opuslib.Encoder(
                fs=AudioConfig.SAMPLE_RATE,
                channels=AudioConfig.CHANNELS,
                application=opuslib.APPLICATION_AUDIO,
            )

            # 初始化Opus解码器
            self.opus_decoder = opuslib.Decoder(
                fs=AudioConfig.SAMPLE_RATE, channels=AudioConfig.CHANNELS
            )

            logger.info("音频设备和编解码器初始化成功")
        except Exception as e:
            logger.error(f"初始化音频设备失败: {e}")
            raise

    def read_audio(self):
        """读取音频输入数据并编码"""
        try:
            data = self.input_stream.read(
                AudioConfig.FRAME_SIZE, exception_on_overflow=False
            )
            if not data:
                return None
            return self.opus_encoder.encode(data, AudioConfig.FRAME_SIZE)
        except Exception as e:
            logger.error(f"读取音频输入时出错: {e}")
            return None

    def write_audio(self, opus_data):
        """将编码的音频数据添加到播放队列"""
        self.audio_decode_queue.put(opus_data)

    def play_audio(self):
        """处理并播放队列中的音频数据"""
        try:
            # 批量处理多个音频包以减少处理延迟
            batch_size = min(10, self.audio_decode_queue.qsize())
            if batch_size == 0:
                return False

            # 创建缓冲区存储解码后的数据
            buffer = bytearray()

            for _ in range(batch_size):
                if self.audio_decode_queue.empty():
                    break

                opus_data = self.audio_decode_queue.get_nowait()
                try:
                    pcm_data = self.opus_decoder.decode(
                        opus_data, AudioConfig.FRAME_SIZE, decode_fec=False
                    )
                    buffer.extend(pcm_data)
                except Exception as e:
                    logger.error(f"解码音频数据时出错: {e}")

            # 只有在有数据时才处理和播放
            if len(buffer) > 0:
                # 转换为numpy数组
                pcm_array = np.frombuffer(buffer, dtype=np.int16)

                # 播放音频
                try:
                    if self.output_stream and self.output_stream.is_active():
                        self.output_stream.write(pcm_array.tobytes())
                        return True
                    else:
                        # MAC 特定：如果流不活跃，尝试重新初始化
                        self._reinitialize_output_stream()
                        if self.output_stream and self.output_stream.is_active():
                            self.output_stream.write(pcm_array.tobytes())
                            return True
                except OSError as e:
                    if "Stream closed" in str(e) or "Internal PortAudio error" in str(
                        e
                    ):
                        logger.error(f"播放音频时出错: {e}")
                        self._reinitialize_output_stream()
                    else:
                        logger.error(f"播放音频时出错: {e}")
        except queue.Empty:
            pass
        except Exception as e:
            logger.error(f"播放音频时出错: {e}")
            self._reinitialize_output_stream()

        return False

    def has_pending_audio(self):
        """检查是否还有待播放的音频数据"""
        return not self.audio_decode_queue.empty()

    def wait_for_audio_complete(self, timeout=5.0):
        # 等待音频队列清空
        attempt = 0
        max_attempts = 15
        while not self.audio_decode_queue.empty() and attempt < max_attempts:
            time.sleep(0.1)
            attempt += 1

        # 在关闭前清空任何剩余数据
        while not self.audio_decode_queue.empty():
            try:
                self.audio_decode_queue.get_nowait()
            except queue.Empty:
                break

    def clear_audio_queue(self):
        """清空音频队列"""
        while not self.audio_decode_queue.empty():
            try:
                self.audio_decode_queue.get_nowait()
            except queue.Empty:
                break

    def start_streams(self):
        """启动音频流"""
        if not self.input_stream.is_active():
            self.input_stream.start_stream()
        if not self.output_stream.is_active():
            self.output_stream.start_stream()

    def stop_streams(self):
        """停止音频流"""
        if self.input_stream and self.input_stream.is_active():
            self.input_stream.stop_stream()
        if self.output_stream and self.output_stream.is_active():
            self.output_stream.stop_stream()

    def _reinitialize_output_stream(self):
        """重新初始化音频输出流"""
        if self._is_closing:  # 如果正在关闭，不要重新初始化
            return

        try:
            if self.output_stream:
                try:
                    if self.output_stream.is_active():
                        self.output_stream.stop_stream()
                    self.output_stream.close()
                except Exception:
                    # logger.warning(f"关闭旧输出流时出错: {e}")
                    pass

            # 在 MAC 上添加短暂延迟
            if sys.platform in ("darwin", "linux"):
                time.sleep(0.1)

            self.output_stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=AudioConfig.CHANNELS,
                rate=AudioConfig.SAMPLE_RATE,
                output=True,
                frames_per_buffer=AudioConfig.FRAME_SIZE,
            )
            logger.info("音频输出流重新初始化成功")
        except Exception as e:
            logger.error(f"重新初始化音频输出流失败: {e}")
            raise

    def close(self):
        """关闭音频编解码器，确保资源正确释放"""
        if self._is_closing:  # 防止重复关闭
            return

        self._is_closing = True
        logger.info("开始关闭音频编解码器...")

        try:
            # 等待并清理剩余音频数据
            self.wait_for_audio_complete()

            # 关闭输入流
            if self.input_stream:
                logger.debug("正在关闭输入流...")
                try:
                    if self.input_stream.is_active():
                        self.input_stream.stop_stream()
                    self.input_stream.close()
                except Exception as e:
                    logger.error(f"关闭输入流时出错: {e}")
                self.input_stream = None

            # 关闭输出流
            if self.output_stream:
                logger.debug("正在关闭输出流...")
                try:
                    if self.output_stream.is_active():
                        self.output_stream.stop_stream()
                    self.output_stream.close()
                except Exception as e:
                    logger.error(f"关闭输出流时出错: {e}")
                self.output_stream = None

            # 关闭 PyAudio 实例
            if self.audio:
                logger.debug("正在终止 PyAudio...")
                try:
                    self.audio.terminate()
                except Exception as e:
                    logger.error(f"终止 PyAudio 时出错: {e}")
                self.audio = None

            # 清理编解码器
            self.opus_encoder = None
            self.opus_decoder = None

            logger.info("音频编解码器关闭完成")
        except Exception as e:
            logger.error(f"关闭音频编解码器时发生错误: {e}")
        finally:
            self._is_closing = False

    def __del__(self):
        """析构函数，确保资源被释放"""
        self.close()

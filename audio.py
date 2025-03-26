import serial
import serial.tools.list_ports
import sounddevice as sd
import numpy as np
import threading
import queue
import time
import re
import sys
import logging
import struct
from PyQt5.QtCore import QObject, pyqtSignal, QTimer

# 配置日志记录
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("PCM_Audio")

# PCM 音频参数 (SIM7600CE-T 使用的标准 PCM 格式)
# 按照文档说明: "USB audio PCM data format is 8K sample rate, 16 bit linear"
# 可以通过AT+CPCMFRM=1设置为16K采样率，但请确保模块和音频处理使用相同的采样率
PCM_SAMPLE_RATE = 8000  # 8kHz (默认模式，可通过AT+CPCMFRM=1设置为16kHz)
PCM_CHANNELS = 1        # 单声道
PCM_DTYPE = np.int16    # 16-bit 线性PCM
CHUNK_SIZE = 160        # 每次读取的样本数 (20ms @ 8kHz，更小的块大小可降低延迟)
BUFFER_SIZE = 8         # 增加缓冲区大小，提高音频稳定性

class PCMAudio(QObject):
    status_changed = pyqtSignal(str)  # 状态变化信号

    def __init__(self):
        super().__init__()
        self.audio_port = None
        self.audio_thread = None
        self.play_thread = None
        self.record_thread = None
        self.terminating = False  # 新增终止标志
        self.is_running = False
        self.call_active = False
        self.port_name = None  # 存储当前使用的端口名称
        self.shutdown_requested = False  # 替代QTimer的关闭请求标志

        # 音频数据队列
        self.play_queue = queue.Queue(maxsize=BUFFER_SIZE)  # 播放队列
        self.record_queue = queue.Queue(maxsize=BUFFER_SIZE)  # 录音队列

        # 音频流
        self.output_stream = None
        self.input_stream = None

    def find_audio_port(self):
        """查找SIM7600CE的Audio端口 (通常是Audio 9001端口)"""
        logger.info("正在查找SIM7600CE Audio端口...")
        self.status_changed.emit("正在查找音频端口...")

        ports = list(serial.tools.list_ports.comports())
        for port in ports:
            # 检查描述或设备ID中是否包含"Audio"和"9001"
            if ('audio' in port.description.lower() or
                'audio' in port.device.lower() or
                '9001' in port.description):
                logger.info(f"找到疑似音频端口: {port.device} - {port.description}")
                self.status_changed.emit(f"找到音频端口: {port.device}")
                return port.device

        logger.warning("未找到SIM7600CE音频端口! 请确保设备已连接且驱动已安装。")
        self.status_changed.emit("未找到音频端口, 通话将没有音频")
        return None

    def open_audio_port(self, port=None):
        """打开SIM7600CE的Audio端口"""
        # 重置终止标志
        self.terminating = False

        # 先关闭之前可能打开的端口
        if self.audio_port and self.audio_port.is_open:
            try:
                self.audio_port.close()
                logger.info("关闭先前打开的音频端口")
            except Exception as e:
                logger.error(f"关闭先前端口时出错: {str(e)}")
            self.audio_port = None

        if port:
            audio_port_name = port
        else:
            audio_port_name = self.find_audio_port()

        if not audio_port_name:
            logger.error("无法打开音频端口: 未找到端口")
            self.status_changed.emit("无法打开音频端口")
            return False

        try:
            # 使用更高的波特率921600以确保音频数据传输顺畅
            self.audio_port = serial.Serial(
                port=audio_port_name,
                baudrate=921600,  # 提高波特率到921600
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1,  # 非阻塞读取
                rtscts=True,    # 启用硬件流控制
                write_timeout=0.5  # 设置写入超时
            )
            self.port_name = audio_port_name  # 存储端口名称
            logger.info(f"成功打开音频端口: {audio_port_name}, 波特率: 921600")
            self.status_changed.emit(f"音频端口已打开: {audio_port_name}")

            # 清空可能已有的数据
            self.audio_port.reset_input_buffer()
            self.audio_port.reset_output_buffer()

            return True
        except Exception as e:
            logger.error(f"打开音频端口失败: {str(e)}")
            self.status_changed.emit(f"打开音频端口失败: {str(e)[:50]}")
            return False

    def start_audio_processing(self):
        """启动音频处理"""
        if not self.audio_port:
            logger.error("未打开音频端口，无法启动音频处理")
            self.status_changed.emit("未打开音频端口，无法启动音频处理")
            return False

        if self.is_running:
            logger.warning("音频处理已经在运行")
            return True

        # 重置终止标志
        self.terminating = False

        # 初始化音频设备
        try:
            # 获取默认设备信息
            devices = sd.query_devices()
            default_output = sd.default.device[1]
            default_input = sd.default.device[0]

            logger.info(f"使用默认输出设备: {devices[default_output]['name']}")
            logger.info(f"使用默认输入设备: {devices[default_input]['name']}")
            self.status_changed.emit(f"使用音频设备: {devices[default_output]['name']}")

            # 清空旧数据
            if self.audio_port and self.audio_port.is_open:
                self.audio_port.reset_input_buffer()
                self.audio_port.reset_output_buffer()

            # 清空队列
            self._clear_audio_queues()

            # 打开音频流
            self.output_stream = sd.OutputStream(
                samplerate=PCM_SAMPLE_RATE,
                channels=PCM_CHANNELS,
                dtype=PCM_DTYPE,
                callback=self._audio_output_callback,
                blocksize=CHUNK_SIZE,
                latency='low'  # 设置低延迟
            )

            self.input_stream = sd.InputStream(
                samplerate=PCM_SAMPLE_RATE,
                channels=PCM_CHANNELS,
                dtype=PCM_DTYPE,
                callback=self._audio_input_callback,
                blocksize=CHUNK_SIZE,
                latency='low'  # 设置低延迟
            )

            # 启动音频流
            self.output_stream.start()
            self.input_stream.start()

            # 设置运行标志
            self.is_running = True

            # 启动处理线程
            self.audio_thread = threading.Thread(target=self._audio_port_thread, daemon=True)
            self.audio_thread.name = "PCMAudioPortThread"
            self.audio_thread.start()

            # 启动播放线程
            self.play_thread = threading.Thread(target=self._play_thread, daemon=True)
            self.play_thread.name = "PCMAudioPlayThread"
            self.play_thread.start()

            # 启动录音线程
            self.record_thread = threading.Thread(target=self._record_thread, daemon=True)
            self.record_thread.name = "PCMAudioRecordThread"
            self.record_thread.start()

            logger.info("音频处理已启动")
            self.status_changed.emit("音频处理已启动")
            return True

        except Exception as e:
            logger.error(f"启动音频处理失败: {str(e)}")
            self.status_changed.emit(f"启动音频处理失败: {str(e)[:50]}")
            self._cleanup_resources()
            return False

    def _clear_audio_queues(self):
        """清空音频队列"""
        # 清空播放队列
        while not self.play_queue.empty():
            try:
                self.play_queue.get_nowait()
            except Exception as e:
                logger.error(f"清空播放队列出错: {str(e)}")
                break

        # 清空录音队列
        while not self.record_queue.empty():
            try:
                self.record_queue.get_nowait()
            except Exception as e:
                logger.error(f"清空录音队列出错: {str(e)}")
                break

        logger.info("已清空音频队列")

    def _cleanup_resources(self):
        """清理所有资源（在关闭或错误时调用）"""
        # 停止和关闭音频流
        if self.output_stream:
            try:
                self.output_stream.stop()
                self.output_stream.close()
            except Exception as e:
                logger.error(f"关闭输出流出错: {str(e)}")
            self.output_stream = None

        if self.input_stream:
            try:
                self.input_stream.stop()
                self.input_stream.close()
            except Exception as e:
                logger.error(f"关闭输入流出错: {str(e)}")
            self.input_stream = None

        # 清空队列
        self._clear_audio_queues()

        # 关闭音频端口
        if self.audio_port and self.audio_port.is_open:
            try:
                self.audio_port.reset_input_buffer()
                self.audio_port.reset_output_buffer()
                self.audio_port.close()
                logger.info(f"已关闭音频端口: {self.port_name}")
            except Exception as e:
                logger.error(f"关闭音频端口出错: {str(e)}")
            self.audio_port = None

        # 重置状态
        self.is_running = False
        self.call_active = False

    def stop_audio_processing(self):
        """停止音频处理"""
        if not self.is_running:
            logger.info("音频处理已经停止，无需再次停止")
            return

        logger.info("正在停止音频处理...")
        self.status_changed.emit("正在停止音频处理...")

        # 设置终止标志，通知所有线程停止
        self.terminating = True
        self.call_active = False
        self.is_running = False
        self.shutdown_requested = False  # 取消可能的关闭请求

        # 立即清理所有资源，不再等待线程正常结束
        self._cleanup_resources()

        # 只在资源清理后尝试关闭线程
        # 等待线程结束 - 使用更短的超时以防止阻塞
        threads_to_wait = []

        if self.audio_thread and self.audio_thread.is_alive():
            logger.info("等待音频端口线程结束...")
            threads_to_wait.append(('音频端口线程', self.audio_thread))

        if self.play_thread and self.play_thread.is_alive():
            logger.info("等待播放线程结束...")
            threads_to_wait.append(('播放线程', self.play_thread))

        if self.record_thread and self.record_thread.is_alive():
            logger.info("等待录音线程结束...")
            threads_to_wait.append(('录音线程', self.record_thread))

        # 等待所有线程结束，每个线程最多等待0.5秒
        for thread_name, thread in threads_to_wait:
            thread.join(timeout=0.5)
            if thread.is_alive():
                logger.warning(f"{thread_name}未能正常结束")

        # 重置线程变量
        self.audio_thread = None
        self.play_thread = None
        self.record_thread = None

        logger.info("音频处理已停止")
        self.status_changed.emit("音频处理已停止")

    def set_call_active(self, active):
        """设置通话状态"""
        prev_state = self.call_active
        self.call_active = active

        if prev_state != active:  # 只有状态改变时才记录和通知
            logger.info(f"通话状态: {'活动' if active else '非活动'}")
            self.status_changed.emit(f"通话音频状态: {'活动' if active else '非活动'}")

            if active:
                # 当状态从非活动变为活动时，清空缓冲区
                if self.audio_port and self.audio_port.is_open:
                    try:
                        self.audio_port.reset_input_buffer()
                        self.audio_port.reset_output_buffer()
                    except Exception as e:
                        logger.error(f"重置音频缓冲区出错: {str(e)}")

                # 清空音频队列
                self._clear_audio_queues()
            else:
                # 状态从活动变为非活动时，开始直接关闭处理，不使用延迟机制
                logger.info("通话状态变为非活动，准备关闭音频处理")
                self.shutdown_requested = True  # 设置关闭请求标志，用于代替QTimer

                # 启动单独的关闭线程，避免在当前线程中执行可能阻塞的操作
                shutdown_thread = threading.Thread(target=self._delayed_shutdown_thread, daemon=True)
                shutdown_thread.start()

    def _delayed_shutdown_thread(self):
        """在单独线程中执行延迟关闭，避免阻塞主线程"""
        try:
            # 等待短暂时间，确保所有挂起的操作都有时间完成
            time.sleep(0.5)

            # 检查是否仍然需要关闭
            if not self.call_active and self.shutdown_requested:
                logger.info("执行延迟关闭音频处理")
                # 执行停止处理，但不在线程中调用self.stop_audio_processing，而是发送信号
                self.status_changed.emit("音频处理关闭中...")

                # 设置所有状态标志
                self.terminating = True

                # 等待安全时间后强制清理资源
                time.sleep(0.5)
                logger.info("关闭音频流和端口")

                # 关闭和清理资源
                if self.output_stream:
                    try:
                        self.output_stream.stop()
                        self.output_stream.close()
                        self.output_stream = None
                    except Exception as e:
                        logger.error(f"关闭输出流出错: {str(e)}")

                if self.input_stream:
                    try:
                        self.input_stream.stop()
                        self.input_stream.close()
                        self.input_stream = None
                    except Exception as e:
                        logger.error(f"关闭输入流出错: {str(e)}")

                # 重置状态
                self.is_running = False
                self.shutdown_requested = False
                logger.info("延迟关闭完成")
                self.status_changed.emit("音频处理已关闭")
        except Exception as e:
            logger.error(f"延迟关闭线程出错: {str(e)}")

    def _audio_output_callback(self, outdata, frames, time, status):
        """音频输出回调（从队列获取PCM数据并输出到扬声器）"""
        if status:
            logger.warning(f"音频输出状态: {status}")

        if not self.call_active or self.terminating:
            # 如果没有通话或正在终止，输出静音
            outdata.fill(0)
            return

        try:
            if not self.play_queue.empty():
                # 从队列获取PCM数据
                data = self.play_queue.get_nowait()
                # 确保数据长度匹配
                if len(data) < frames:
                    # 数据不足，补零
                    padding = np.zeros((frames - len(data), PCM_CHANNELS), dtype=PCM_DTYPE)
                    data = np.vstack((data, padding))
                elif len(data) > frames:
                    # 数据过多，截断
                    data = data[:frames]
                # 复制到输出缓冲区
                outdata[:] = data
            else:
                # 队列为空，输出静音
                outdata.fill(0)
        except Exception as e:
            logger.error(f"音频输出错误: {str(e)}")
            outdata.fill(0)

    def _audio_input_callback(self, indata, frames, time, status):
        """音频输入回调（从麦克风获取PCM数据并发送到队列）"""
        if status:
            logger.warning(f"音频输入状态: {status}")

        if not self.call_active or self.terminating:
            # 如果没有通话或正在终止，不处理输入
            return

        try:
            # 将麦克风数据放入录音队列
            if not self.record_queue.full():
                self.record_queue.put_nowait(indata.copy())
        except Exception as e:
            logger.error(f"音频输入错误: {str(e)}")

    def _audio_port_thread(self):
        """音频端口处理线程（读取PCM数据 - 模块到扬声器）"""
        # PCM数据解析缓冲区
        buffer = bytearray()
        bytes_per_frame = CHUNK_SIZE * PCM_CHANNELS * 2  # 16-bit = 2 bytes
        last_log_time = 0
        frame_count = 0
        last_buffer_check_time = 0
        processed_frames_total = 0
        last_data_received_time = time.time()
        silent_frames_count = 0
        frame_sync_attempts = 0
        recovered_frames = 0

        # 设置调试计数器
        debug_frame_counter = 0
        debug_signal_detection = False
        signal_level_history = []
        max_signal_level = 0

        # 设置基准音量和增益值 - 增加接收增益确保清晰听到对方声音
        base_gain = 5.0  # 更高的基准增益，确保足够听到对方声音
        noise_threshold = 30  # 降低噪声阈值以确保不会过滤掉有效信号

        # 设置静音帧阈值，超过该数量未收到有效数据帧时发出警告
        SILENT_FRAMES_THRESHOLD = 50

        logger.info("音频端口处理线程已启动")
        logger.info(f"PCM参数: 采样率={PCM_SAMPLE_RATE}Hz, 通道数={PCM_CHANNELS}, 每帧字节数={bytes_per_frame}")
        logger.info(f"音频输出设置: 基准增益={base_gain}x，噪声阈值={noise_threshold}")

        # 发送模式测试数据
        try:
            # 向模块发送一些测试数据，验证发送通道
            if self.audio_port and self.audio_port.is_open:
                test_data = np.zeros((CHUNK_SIZE, PCM_CHANNELS), dtype=np.int16)
                test_data[:10, 0] = 16000  # 前10个样本设置为16000，形成短脉冲
                test_bytes = test_data.tobytes()
                self.audio_port.write(test_bytes)
                logger.info(f"已发送测试音频数据: {len(test_bytes)}字节")
        except Exception as e:
            logger.error(f"发送测试数据出错: {str(e)}")

        while self.is_running and not self.terminating:
            try:
                if not self.audio_port or not self.audio_port.is_open:
                    time.sleep(0.1)
                    continue

                # 如果不在通话状态，快速检查并继续循环
                if not self.call_active:
                    # 清空缓冲区并睡眠
                    if buffer:
                        buffer = bytearray()
                    time.sleep(0.1)
                    continue

                # 读取串口数据
                try:
                    available = self.audio_port.in_waiting
                    if available > 0:
                        # 读取所有可用数据
                        data = self.audio_port.read(available)
                        if data:
                            # 更新最后接收数据时间
                            last_data_received_time = time.time()
                            silent_frames_count = 0  # 重置静音帧计数

                            # 添加到缓冲区
                            buffer.extend(data)

                            # 每1000帧记录一次调试信息
                            debug_frame_counter += 1
                            if debug_frame_counter >= 1000:
                                # 记录详细状态信息
                                logger.info(f"[读取] 音频缓冲区: {len(buffer)}字节, 可用数据: {available}字节")
                                logger.info(f"[读取] 已处理总帧数: {processed_frames_total}, 缓冲区状态: {len(buffer)/bytes_per_frame:.1f}帧")
                                if signal_level_history:
                                    avg_level = sum(signal_level_history) / len(signal_level_history)
                                    logger.info(f"[读取] 平均信号电平: {avg_level:.2f}, 最大信号电平: {max_signal_level:.2f}")
                                    if avg_level > 0:
                                        logger.info(f"[读取] 检测到音频信号，增益设置为{base_gain}x")
                                debug_frame_counter = 0
                    else:
                        # 长时间未收到数据，可能需要检查通信状态
                        current_time = time.time()
                        if current_time - last_data_received_time > 0.5:  # 半秒未收到数据
                            silent_frames_count += 1
                            if silent_frames_count > SILENT_FRAMES_THRESHOLD and self.call_active:
                                logger.warning("[读取] 长时间未收到音频数据，检查通信状态")
                                silent_frames_count = 0  # 重置计数，避免重复警告

                                # 尝试重置串口缓冲区，重新启动数据流
                                if self.audio_port and self.audio_port.is_open:
                                    try:
                                        # 先发送一些数据，可能帮助触发接收
                                        test_data = np.zeros((CHUNK_SIZE, PCM_CHANNELS), dtype=np.int16)
                                        test_data[:10, 0] = 16000  # 前10个样本设置为16000
                                        test_bytes = test_data.tobytes()
                                        self.audio_port.write(test_bytes)

                                        # 重置输入缓冲区
                                        self.audio_port.reset_input_buffer()
                                        logger.info("[读取] 已重置音频输入缓冲区并发送测试数据")
                                    except Exception as e:
                                        logger.error(f"[读取] 重置音频缓冲区出错: {str(e)}")

                    # 定期检查缓冲区大小，避免缓冲区无限增长
                    current_time = time.time()
                    if current_time - last_buffer_check_time > 1.0:  # 每秒检查一次
                        # 检查缓冲区大小
                        buffer_size = len(buffer)

                        # 如果缓冲区不是帧大小的整数倍，尝试帧同步
                        remainder = buffer_size % bytes_per_frame
                        if remainder != 0 and buffer_size > bytes_per_frame:
                            # 尝试通过查找头部模式同步帧
                            frame_sync_attempts += 1
                            if frame_sync_attempts % 10 == 0:  # 每10次尝试记录一次
                                logger.warning(f"[读取] 帧同步尝试: {frame_sync_attempts}次, 缓冲区大小: {buffer_size}字节, 余数: {remainder}字节")

                            # 丢弃余数字节或补齐帧
                            if remainder < bytes_per_frame / 2:
                                # 余数小于半帧，丢弃余数
                                buffer = buffer[:-remainder]
                            else:
                                # 余数大于半帧，补齐为完整帧（用0填充）
                                padding_size = bytes_per_frame - remainder
                                buffer.extend(bytes(padding_size))
                                recovered_frames += 1

                        buffer_frames = len(buffer) / bytes_per_frame
                        if buffer_size > bytes_per_frame * 30:  # 如果缓冲区积累太多数据
                            logger.warning(f"[读取] 缓冲区积累过多数据 ({buffer_size} 字节, {buffer_frames:.1f} 帧), 保留最后10帧数据")
                            # 只保留最后部分数据
                            buffer = buffer[-bytes_per_frame * 10:]

                        # 如果音频缓冲区长时间为空并且通话活动，记录警告
                        if buffer_size == 0 and self.call_active and processed_frames_total > 0:
                            logger.warning("[读取] 音频缓冲区为空但通话仍在进行，可能缺少音频数据")

                        last_buffer_check_time = current_time

                    # 当缓冲区数据足够时处理
                    processed = 0
                    while len(buffer) >= bytes_per_frame and self.call_active and not self.terminating:
                        # 提取一帧数据
                        frame_data = buffer[:bytes_per_frame]
                        buffer = buffer[bytes_per_frame:]
                        processed += 1
                        processed_frames_total += 1

                        try:
                            # 将SIM7600CE的PCM数据转换为音频数据
                            pcm_data = np.frombuffer(frame_data, dtype=np.int16).reshape(-1, PCM_CHANNELS)

                            # 计算信号电平用于自动增益和检测有效信号
                            signal_level = np.abs(pcm_data).mean()

                            # 在首次接收到高于阈值的信号时记录
                            if signal_level > noise_threshold and not signal_level_history:
                                logger.info(f"[读取] 首次检测到信号: 电平={signal_level:.2f}")

                            # 过滤掉异常值，确保数据有效
                            if signal_level < 32000:  # 有效PCM数据不应超过此值
                                # 更新信号历史
                                signal_level_history.append(signal_level)
                                if len(signal_level_history) > 50:  # 保留50帧的历史
                                    signal_level_history.pop(0)

                                # 记录最大信号电平（用于调试）
                                if signal_level > max_signal_level:
                                    max_signal_level = signal_level
                                    if not debug_signal_detection and signal_level > 100:
                                        logger.info(f"[读取] 检测到新的最大信号电平: {max_signal_level:.2f}")
                                        debug_signal_detection = True

                                # 噪声消除 - 如果信号电平低于噪声阈值，视为噪声
                                if signal_level < noise_threshold:
                                    # 对于非常低的信号（噪声），应用非常小的增益
                                    # 但仍保留一部分，以保持连续性
                                    pcm_data = pcm_data * 0.05  # 保留5%的信号
                                else:
                                    # 为了确保足够的音量，使用较高的基准增益
                                    # 让对方的声音更加清晰
                                    pcm_data = np.clip(pcm_data * base_gain, -32700, 32700).astype(np.int16)

                                # 放入播放队列
                                if not self.play_queue.full() and not self.terminating:
                                    self.play_queue.put_nowait(pcm_data)
                                    frame_count += 1
                            else:
                                # 信号电平异常，可能是帧同步问题
                                logger.warning(f"[读取] 异常信号电平: {signal_level}, 可能帧同步问题")

                            # 每隔一段时间记录一次性能日志
                            current_time = time.time()
                            if current_time - last_log_time > 5.0:  # 每5秒记录一次
                                avg_signal = np.mean(signal_level_history) if signal_level_history else 0
                                logger.info(f"[读取] 已处理 {frame_count} 帧PCM数据，平均信号电平: {avg_signal:.2f}")
                                last_log_time = current_time
                                frame_count = 0

                        except Exception as e:
                            logger.error(f"[读取] 处理PCM数据帧错误: {str(e)}")
                            # 出错时清空缓冲区，避免继续处理错误数据
                            buffer = bytearray()
                            break
                except Exception as e:
                    logger.error(f"[读取] 读取音频端口数据出错: {str(e)}")
                    time.sleep(0.1)

                # 如果当前没有更多数据可读，短暂休眠避免CPU占用
                if available == 0:
                    time.sleep(0.01)  # 10ms延迟，提供更好的响应性
                else:
                    # 有数据处理时使用更短的延迟
                    time.sleep(0.001)

            except Exception as e:
                logger.error(f"[读取] 音频端口处理错误: {str(e)}")
                time.sleep(0.1)

        # 线程结束前清空缓冲区及统计数据
        buffer = bytearray()
        signal_level_history = []
        logger.info(f"[读取] 音频端口处理线程已退出，总处理帧数: {processed_frames_total}, 恢复帧: {recovered_frames}")

    def _play_thread(self):
        """播放线程（处理PCM数据队列）"""
        logger.info("播放线程已启动")

        while self.is_running and not self.terminating:
            try:
                # 线程主要工作在回调中完成，这里只需要保持线程运行
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"播放线程错误: {str(e)}")
                time.sleep(0.1)

        logger.info("播放线程已退出")

    def _record_thread(self):
        """录音线程（发送PCM数据到串口 - 麦克风到模块）"""
        logger.info("[发送] 录音线程已启动")

        # 记录最近的数据包大小，用于调试
        packet_sizes = []
        last_log_time = 0
        sent_packets_count = 0
        total_bytes_sent = 0
        last_packet_sent_time = time.time()

        # 引入随机数生成器用于加入测试音频
        np.random.seed()

        # 采样率和块大小
        sample_rate = PCM_SAMPLE_RATE  # 8kHz
        chunk_size = CHUNK_SIZE  # 160个样本，即20ms@8kHz

        # 初始化降噪参数
        noise_floor = 80  # 噪声阈值 - 降低以确保捕获更多人声
        voice_gain = 4.0   # 人声增益 - 增加以确保声音传输清晰
        noise_gate_enabled = True  # 启用噪声门控

        logger.info(f"[发送] 麦克风设置: 启用噪声门控={noise_gate_enabled}, 噪声阈值={noise_floor}, 人声增益={voice_gain}x")

        # 创建测试音频信号（1kHz正弦波）用于向模块发送
        test_audio_enabled = False  # 默认关闭测试音频
        test_tone_freq = 1000  # 1kHz
        test_tone_samples = np.arange(chunk_size)
        test_tone = (16000 * np.sin(2 * np.pi * test_tone_freq * test_tone_samples / sample_rate)).astype(np.int16)
        test_tone = test_tone.reshape(-1, PCM_CHANNELS)

        # 强制发送计时器，确保即使麦克风无输入，仍定期发送数据包
        force_send_interval = 0.020  # 20ms，确保平滑音频
        zero_frame = np.zeros((chunk_size, PCM_CHANNELS), dtype=np.int16)

        # 加入启动时的初始测试音频
        try:
            if self.audio_port and self.audio_port.is_open:
                # 发送测试音频波形序列
                for i in range(5):  # 发送5帧测试音频
                    self.audio_port.write(test_tone.tobytes())
                    sent_packets_count += 1
                    time.sleep(0.02)  # 20ms间隔
                logger.info(f"[发送] 已发送初始测试音频: 5帧")
        except Exception as e:
            logger.error(f"[发送] 发送初始测试音频出错: {str(e)}")

        while self.is_running and not self.terminating:
            try:
                if not self.call_active or not self.audio_port or not self.audio_port.is_open or self.terminating:
                    time.sleep(0.1)
                    continue

                current_time = time.time()

                # 是否应该强制发送（超过定期发送间隔）
                should_force_send = (current_time - last_packet_sent_time) > force_send_interval

                # 从录音队列获取数据
                try:
                    # 使用短超时，避免长时间阻塞
                    pcm_data = None
                    try:
                        pcm_data = self.record_queue.get(timeout=0.01)
                    except queue.Empty:
                        # 队列为空，如果需要强制发送则生成静音帧
                        if should_force_send:
                            if test_audio_enabled:
                                # 使用测试音频而不是静音
                                pcm_data = test_tone.copy()
                                logger.debug("[发送] 生成测试音频帧发送")
                            else:
                                # 使用静音帧
                                pcm_data = zero_frame.copy()
                                logger.debug("[发送] 生成静音帧发送")
                        else:
                            continue

                    # 如果还没有数据，跳过当前循环
                    if pcm_data is None:
                        continue

                    # 计算当前音量级别
                    volume_level = np.abs(pcm_data).mean()

                    # 偶尔发送测试音频以确保通信通道开放
                    if sent_packets_count % 1000 == 0:  # 每1000个包发送一次测试音频
                        # 临时替换为测试音频
                        pcm_data = test_tone.copy()
                        logger.info(f"[发送] 发送测试音频帧: #{sent_packets_count}")

                    # 应用噪声门控和增益处理
                    if noise_gate_enabled:
                        if volume_level < noise_floor:
                            # 低于阈值的信号视为背景噪音，强烈抑制但不完全消除
                            # 这有助于减少背景噪音传输到对方
                            pcm_data = pcm_data * 0.02  # 只保留2%原始信号
                        else:
                            # 高于阈值的信号应用更高增益提升人声清晰度
                            # 确保声音传输到对方足够清晰
                            pcm_data = np.clip(pcm_data * voice_gain, -32700, 32700).astype(np.int16)
                    else:
                        # 如果不启用噪声门控，仍然应用增益
                        pcm_data = np.clip(pcm_data * voice_gain, -32700, 32700).astype(np.int16)

                    # 将PCM数据转换为字节发送到串口（确保使用小端字节序）
                    bytes_data = pcm_data.astype(np.int16).tobytes()

                    # 更新发送计时
                    last_packet_sent_time = current_time

                    # 记录数据包大小用于调试
                    packet_sizes.append(len(bytes_data))
                    if len(packet_sizes) > 20:
                        packet_sizes.pop(0)

                    # 每5秒记录一次发送数据统计
                    if current_time - last_log_time > 5.0:
                        if packet_sizes:
                            avg_size = sum(packet_sizes) / len(packet_sizes)
                            logger.info(f"[发送] 音频发送: 平均数据包大小 {avg_size:.2f} 字节, 已发送 {sent_packets_count} 个数据包 ({total_bytes_sent/1024:.2f} KB)")
                        last_log_time = current_time

                    # 检查连接和终止状态
                    if self.audio_port and self.audio_port.is_open and not self.terminating:
                        # 确保立即发送数据
                        bytes_sent = self.audio_port.write(bytes_data)
                        sent_packets_count += 1
                        total_bytes_sent += bytes_sent

                        # 调试：检查发送的字节数
                        if bytes_sent != len(bytes_data):
                            logger.warning(f"[发送] 音频数据发送不完整: {bytes_sent}/{len(bytes_data)}字节")

                        # 确保数据立即发送
                        self.audio_port.flush()

                except Exception as e:
                    logger.error(f"[发送] 发送PCM数据错误: {str(e)}")
                    time.sleep(0.01)

            except Exception as e:
                logger.error(f"[发送] 录音线程错误: {str(e)}")
                time.sleep(0.1)

        # 清理记录数据
        packet_sizes = []
        logger.info(f"[发送] 录音线程已退出，总发送数据包: {sent_packets_count}, 总发送字节: {total_bytes_sent/1024:.2f} KB")

# 单独测试功能
if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    import sys

    app = QApplication(sys.argv)

    # 测试音频功能
    audio = PCMAudio()

    port = audio.find_audio_port()
    if port:
        print(f"找到音频端口: {port}")
        if audio.open_audio_port(port):
            print("成功打开音频端口")
            audio.start_audio_processing()
            print("按Enter键模拟通话开始...")
            input()
            audio.set_call_active(True)
            print("通话已开始，现在可以说话...按Enter键结束通话")
            input()
            audio.set_call_active(False)
            print("通话已结束")
            # 等待延迟关闭完成
            time.sleep(4)
            sys.exit(0)
    else:
        print("未找到音频端口")
        sys.exit(1)
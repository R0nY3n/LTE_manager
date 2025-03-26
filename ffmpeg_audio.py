import os
import subprocess
import threading
import time
import serial
import serial.tools.list_ports
import logging
import tempfile
from PyQt5.QtCore import QObject, pyqtSignal

# 配置日志记录
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FFmpeg_Audio")

# PCM 音频参数
PCM_SAMPLE_RATE = 8000  # 8kHz (默认模式，可通过AT+CPCMFRM=1设置为16kHz)
PCM_CHANNELS = 1        # 单声道
FFMPEG_PATH = "D:\\ffmpeg\\ffmpeg.exe"  # FFmpeg可执行文件路径
FFPLAY_PATH = "D:\\ffmpeg\\ffplay.exe"  # FFPlay可执行文件路径

class FFmpegAudio(QObject):
    status_changed = pyqtSignal(str)  # 状态变化信号

    def __init__(self):
        super().__init__()
        self.audio_port = None
        self.port_name = None
        self.is_running = False
        self.call_active = False
        self.terminating = False

        # FFmpeg进程
        self.ffmpeg_input_process = None  # 从串口读取到扬声器
        self.ffmpeg_output_process = None  # 从麦克风到串口

        # 管理线程
        self.monitor_thread = None

        # 临时文件
        self.temp_dir = tempfile.mkdtemp()
        logger.info(f"创建临时目录: {self.temp_dir}")

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

    def _cleanup_resources(self):
        """清理所有资源（在关闭或错误时调用）"""
        logger.info("清理音频资源...")

        # 停止FFmpeg进程
        try:
            if self.ffmpeg_input_process:
                logger.info("正在停止FFmpeg输入进程...")
                self.ffmpeg_input_process.terminate()
                self.ffmpeg_input_process.wait(timeout=1)
                self.ffmpeg_input_process = None
        except Exception as e:
            logger.error(f"停止FFmpeg输入进程出错: {str(e)}")

        try:
            if self.ffmpeg_output_process:
                logger.info("正在停止FFmpeg输出进程...")
                self.ffmpeg_output_process.terminate()
                self.ffmpeg_output_process.wait(timeout=1)
                self.ffmpeg_output_process = None
        except Exception as e:
            logger.error(f"停止FFmpeg输出进程出错: {str(e)}")

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
        logger.info("音频资源清理完成")

    def stop_audio_processing(self):
        """停止音频处理"""
        if not self.is_running:
            logger.info("音频处理已经停止，无需再次停止")
            return

        logger.info("正在停止音频处理...")
        self.status_changed.emit("正在停止音频处理...")

        # 设置终止标志
        self.terminating = True
        self.call_active = False
        self.is_running = False

        # 清理资源
        self._cleanup_resources()

        # 等待监控线程结束
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=1)

        self.monitor_thread = None

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
                # 当状态从非活动变为活动时，确保FFmpeg进程在运行
                self._ensure_ffmpeg_running()
            else:
                # 状态从活动变为非活动时，停止FFmpeg进程
                logger.info("通话状态变为非活动，准备关闭音频处理")
                # 启动单独的关闭线程，避免在当前线程中执行可能阻塞的操作
                threading.Thread(target=self._delayed_shutdown, daemon=True).start()

    def _delayed_shutdown(self):
        """延迟关闭处理"""
        try:
            # 等待短暂时间确保所有挂起的操作完成
            time.sleep(0.5)
            if not self.call_active and not self.terminating:
                logger.info("执行延迟关闭音频处理")
                self._cleanup_resources()
        except Exception as e:
            logger.error(f"延迟关闭出错: {str(e)}")

    def _ensure_ffmpeg_running(self):
        """确保FFmpeg进程在运行"""
        if not self.is_running or not self.audio_port or not self.audio_port.is_open:
            logger.warning("音频处理未启动或端口未打开，无法确保FFmpeg运行")
            return

        try:
            # 检查并启动FFmpeg进程
            if not self.ffmpeg_input_process or self.ffmpeg_input_process.poll() is not None:
                self._start_ffmpeg_input()

            if not self.ffmpeg_output_process or self.ffmpeg_output_process.poll() is not None:
                self._start_ffmpeg_output()

        except Exception as e:
            logger.error(f"确保FFmpeg运行时出错: {str(e)}")

    def _start_ffmpeg_input(self):
        """启动从串口到扬声器的FFmpeg进程"""
        if not self.audio_port or not self.audio_port.is_open:
            logger.error("音频端口未打开，无法启动FFmpeg输入进程")
            return False

        try:
            # 配置FFmpeg命令行
            # 从串口读取PCM数据并播放到扬声器
            input_pipe_path = os.path.join(self.temp_dir, "input_pipe.pcm")

            # 检查并确保管道创建
            if os.path.exists(input_pipe_path):
                try:
                    os.remove(input_pipe_path)
                except:
                    pass

            # 创建命令行
            cmd = [
                FFPLAY_PATH,
                "-f", "s16le",         # 16位有符号整数，小端序
                "-ar", str(PCM_SAMPLE_RATE),  # 采样率
                "-ac", str(PCM_CHANNELS),      # 通道数
                "-i", "pipe:0",        # 从标准输入读取
                "-loglevel", "warning",  # 只显示警告和错误
                "-af", "volume=5",     # 增加音量
                "-nodisp"              # 不显示视频窗口
            ]

            logger.info(f"启动FFmpeg输入进程: {' '.join(cmd)}")

            # 启动FFmpeg进程
            self.ffmpeg_input_process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0  # 无缓冲
            )

            # 启动从串口读取数据的线程
            threading.Thread(
                target=self._read_from_serial_to_ffmpeg,
                daemon=True
            ).start()

            logger.info("FFmpeg输入进程启动成功")
            return True
        except Exception as e:
            logger.error(f"启动FFmpeg输入进程失败: {str(e)}")
            return False

    def _read_from_serial_to_ffmpeg(self):
        """从串口读取数据并发送到FFmpeg"""
        buffer_size = 320  # 每次读取的字节数 (160个16位样本)

        # 用于统计的变量
        bytes_read = 0
        last_log_time = time.time()
        frames_sent = 0

        logger.info("[读取] 开始从串口读取PCM数据到FFmpeg")

        while self.is_running and self.call_active and not self.terminating:
            try:
                if not self.audio_port or not self.audio_port.is_open:
                    time.sleep(0.1)
                    continue

                if not self.ffmpeg_input_process or self.ffmpeg_input_process.poll() is not None:
                    logger.warning("[读取] FFmpeg输入进程已结束，停止读取")
                    break

                # 读取数据
                if self.audio_port.in_waiting > 0:
                    data = self.audio_port.read(min(buffer_size, self.audio_port.in_waiting))
                    if data:
                        bytes_read += len(data)
                        frames_sent += 1

                        # 发送到FFmpeg
                        try:
                            self.ffmpeg_input_process.stdin.write(data)
                            self.ffmpeg_input_process.stdin.flush()
                        except Exception as e:
                            logger.error(f"[读取] 发送数据到FFmpeg出错: {str(e)}")
                            break

                # 输出统计信息
                current_time = time.time()
                if current_time - last_log_time > 5.0:  # 每5秒记录一次
                    logger.info(f"[读取] 已读取 {bytes_read/1024:.2f} KB PCM数据，发送 {frames_sent} 帧")
                    last_log_time = current_time

                # 检查是否有长时间未收到数据
                if self.audio_port.in_waiting == 0:
                    time.sleep(0.01)  # 短暂休眠

            except Exception as e:
                logger.error(f"[读取] 从串口读取数据出错: {str(e)}")
                time.sleep(0.1)

        # 关闭FFmpeg输入
        try:
            if self.ffmpeg_input_process and self.ffmpeg_input_process.stdin:
                self.ffmpeg_input_process.stdin.close()
        except:
            pass

        logger.info(f"[读取] 停止从串口读取，总计读取 {bytes_read/1024:.2f} KB PCM数据")

    def _start_ffmpeg_output(self):
        """启动从麦克风到串口的FFmpeg进程"""
        if not self.audio_port or not self.audio_port.is_open:
            logger.error("音频端口未打开，无法启动FFmpeg输出进程")
            return False

        try:
            # 配置FFmpeg命令行
            # 从麦克风录制PCM数据并发送到串口
            cmd = [
                FFMPEG_PATH,
                "-f", "dshow",              # DirectShow输入
                "-i", "audio=@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\\wave_{DFDF5B7D-7597-4E7C-84D6-CFF1F7379E35}",  # 默认麦克风
                "-ar", str(PCM_SAMPLE_RATE),  # 采样率
                "-ac", str(PCM_CHANNELS),      # 通道数
                "-loglevel", "warning",      # 只显示警告和错误
                "-af", "volume=4,highpass=f=200,lowpass=f=3000,compand=0.3:0.8:-90/-60:-60/-40:-40/-30:-20/-20:0/-10:0.2",  # 音频处理
                "-f", "s16le",              # 16位有符号整数，小端序
                "pipe:1"                    # 输出到标准输出
            ]

            logger.info(f"启动FFmpeg输出进程: {' '.join(cmd)}")

            # 启动FFmpeg进程
            self.ffmpeg_output_process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0  # 无缓冲
            )

            # 启动将FFmpeg输出写入串口的线程
            threading.Thread(
                target=self._write_from_ffmpeg_to_serial,
                daemon=True
            ).start()

            logger.info("FFmpeg输出进程启动成功")
            return True
        except Exception as e:
            logger.error(f"启动FFmpeg输出进程失败: {str(e)}")
            return False

    def _write_from_ffmpeg_to_serial(self):
        """从FFmpeg读取数据并写入串口"""
        buffer_size = 320  # 每次读取/写入的字节数

        # 用于统计的变量
        bytes_written = 0
        last_log_time = time.time()
        frames_sent = 0

        logger.info("[发送] 开始从FFmpeg发送PCM数据到串口")

        while self.is_running and self.call_active and not self.terminating:
            try:
                if not self.audio_port or not self.audio_port.is_open:
                    time.sleep(0.1)
                    continue

                if not self.ffmpeg_output_process or self.ffmpeg_output_process.poll() is not None:
                    logger.warning("[发送] FFmpeg输出进程已结束，停止写入")
                    break

                # 读取数据
                data = self.ffmpeg_output_process.stdout.read(buffer_size)
                if data:
                    # 写入串口
                    self.audio_port.write(data)
                    self.audio_port.flush()
                    bytes_written += len(data)
                    frames_sent += 1

                # 输出统计信息
                current_time = time.time()
                if current_time - last_log_time > 5.0:  # 每5秒记录一次
                    logger.info(f"[发送] 已发送 {bytes_written/1024:.2f} KB PCM数据，发送 {frames_sent} 帧")
                    last_log_time = current_time

                # 短暂休眠，避免CPU占用过高
                time.sleep(0.01)

            except Exception as e:
                logger.error(f"[发送] 写入数据到串口出错: {str(e)}")
                time.sleep(0.1)

        logger.info(f"[发送] 停止写入串口，总计发送 {bytes_written/1024:.2f} KB PCM数据")

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

        try:
            # 检查FFmpeg是否存在
            if not os.path.exists(FFMPEG_PATH) or not os.path.exists(FFPLAY_PATH):
                logger.error(f"找不到FFmpeg可执行文件: {FFMPEG_PATH} 或 {FFPLAY_PATH}")
                self.status_changed.emit("找不到FFmpeg可执行文件")
                return False

            # 设置运行标志
            self.is_running = True

            # 启动监控线程
            self.monitor_thread = threading.Thread(target=self._monitor_thread, daemon=True)
            self.monitor_thread.name = "FFmpegMonitorThread"
            self.monitor_thread.start()

            logger.info("音频处理已启动")
            self.status_changed.emit("音频处理已启动")
            return True

        except Exception as e:
            logger.error(f"启动音频处理失败: {str(e)}")
            self.status_changed.emit(f"启动音频处理失败: {str(e)[:50]}")
            self._cleanup_resources()
            return False

    def _monitor_thread(self):
        """监控线程，确保FFmpeg进程正常运行"""
        logger.info("启动FFmpeg监控线程")

        while self.is_running and not self.terminating:
            try:
                # 如果通话活动且FFmpeg进程需要启动
                if self.call_active:
                    self._ensure_ffmpeg_running()

                # 检查进程状态
                if self.ffmpeg_input_process and self.ffmpeg_input_process.poll() is not None:
                    logger.warning(f"FFmpeg输入进程已退出，状态码: {self.ffmpeg_input_process.poll()}")
                    if self.call_active and not self.terminating:
                        logger.info("尝试重启FFmpeg输入进程")
                        self._start_ffmpeg_input()

                if self.ffmpeg_output_process and self.ffmpeg_output_process.poll() is not None:
                    logger.warning(f"FFmpeg输出进程已退出，状态码: {self.ffmpeg_output_process.poll()}")
                    if self.call_active and not self.terminating:
                        logger.info("尝试重启FFmpeg输出进程")
                        self._start_ffmpeg_output()

                # 短暂休眠
                time.sleep(1.0)

            except Exception as e:
                logger.error(f"监控线程出错: {str(e)}")
                time.sleep(1.0)

        logger.info("FFmpeg监控线程退出")

# 单独测试功能
if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    import sys

    app = QApplication(sys.argv)

    # 测试FFmpeg音频功能
    audio = FFmpegAudio()

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
import sys
import os
import time
import threading
import serial
import re
import platform
import queue
import binascii
from datetime import datetime
from PyQt5.QtCore import QObject, pyqtSignal, QDateTime, QTimer
from sms_utils import text_to_ucs2, ucs2_to_text, is_chinese_text, format_phone_number

class LTEManager(QObject):
    # Signals
    sms_received = pyqtSignal(str, str, str)  # sender, timestamp, message
    call_received = pyqtSignal(str)  # caller number
    call_ended = pyqtSignal(str)  # duration
    status_changed = pyqtSignal(str)  # status message
    dtmf_received = pyqtSignal(str)  # DTMF tone
    pcm_audio_status = pyqtSignal(bool)  # PCM audio registration status (True=registered, False=unregistered)

    def __init__(self):
        super().__init__()
        self.connected = False
        self.running = False
        self.at_serial = None
        self.nmea_serial = None
        self.read_thread = None
        self.command_queue = []
        self.command_event = threading.Event()
        self.command_lock = threading.Lock()
        self.response_buffer = []
        self.last_response_time = 0

        # 通话状态属性
        self.in_call = False
        self.call_number = ""
        self.call_connected = False  # 标记通话是否已经接通（区分来电振铃和通话接通）
        self.call_notification_sent = False  # 标记是否已发送来电通知
        self.pcm_registered = False

        # 模块信息属性
        self.manufacturer = ""
        self.model = ""
        self.imei = ""
        self.firmware = ""
        self.phone_number = ""
        self.carrier = ""
        self.network_type = ""
        self.signal_strength = ""

        # SMS 相关变量
        self.waiting_for_sms_content = False
        self.pending_sms_sender = None
        self.pending_sms_timestamp = None
        self.concat_sms_parts = {}  # 用于存储长短信的各个部分
        self.concat_sms_timeout = 30  # 长短信合并超时时间（秒）

        # 兼容性处理：模拟队列以便兼容旧代码
        self.response_queue = queue.Queue()

        # 通话状态定义
        self.call_states = {
            0: "正在进行",   # active
            1: "保持",      # hold
            2: "拨号中",    # dialing (MO)
            3: "振铃中",    # alerting (MO)
            4: "来电中",    # incoming (MT)
            5: "等待中"     # waiting (MT)
        }

        self.cleanup_timer = QTimer()
        self.cleanup_timer.timeout.connect(self._cleanup_old_sms_parts)
        self.cleanup_timer.start(300000)  # 每5分钟清理一次

        # 添加信息缓存，用于减少AT命令频率
        self.info_cache = {
            'carrier': {'value': None, 'timestamp': 0, 'valid_time': 300},  # 运营商信息缓存5分钟
            'phone_number': {'value': None, 'timestamp': 0, 'valid_time': 3600},  # 电话号码缓存1小时
            'network': {'value': None, 'timestamp': 0, 'valid_time': 60},  # 网络信息缓存1分钟
            'signal': {'value': None, 'timestamp': 0, 'valid_time': 10},  # 信号强度缓存10秒
            'module_info': {'value': None, 'timestamp': 0, 'valid_time': 3600}  # 模块信息缓存1小时
        }

        # 设置日志文件
        self.setup_log_file()

    def setup_log_file(self):
        """设置AT命令日志文件"""
        try:
            # 在用户主目录下的.LTE文件夹创建日志文件
            user_home = os.path.expanduser('~')
            lte_dir = os.path.join(user_home, '.LTE')
            if not os.path.exists(lte_dir):
                os.makedirs(lte_dir)

            # 使用日期作为文件名
            current_date = datetime.now().strftime("%Y-%m-%d")
            self.log_file_path = os.path.join(lte_dir, f"at_commands_{current_date}.log")

            # 打开日志文件，使用追加模式
            self.log_file = open(self.log_file_path, 'a', encoding='utf-8')

            # 写入日志文件头
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.log_file.write(f"\n\n{'='*50}\n")
            self.log_file.write(f"LTE Manager AT交互日志 - 会话开始: {timestamp}\n")
            self.log_file.write(f"{'='*50}\n\n")
            self.log_file.flush()

            print(f"AT命令日志文件已创建: {self.log_file_path}")
            return True
        except Exception as e:
            print(f"创建AT命令日志文件失败: {str(e)}")
            self.log_file = None
            return False

    def log_at_command(self, is_command, content):
        """记录AT命令或响应到日志文件"""
        try:
            if not hasattr(self, 'log_file') or self.log_file is None:
                # 如果日志文件不存在，尝试重新创建
                self.setup_log_file()
                if not hasattr(self, 'log_file') or self.log_file is None:
                    # 如果创建失败，仅打印到控制台
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    prefix = ">>>" if is_command else "<<<"
                    print(f"[{timestamp}] {prefix} {content}")
                    return  # 放弃写入日志

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

            if is_command:
                log_entry = f"[{timestamp}] >>> {content}\n"
            else:
                log_entry = f"[{timestamp}] <<< {content}\n"

            self.log_file.write(log_entry)
            self.log_file.flush()
        except Exception as e:
            print(f"写入AT命令日志失败: {str(e)}")
            try:
                # 尝试重新创建日志
                self.setup_log_file()
            except:
                # 如果仍然失败，放弃
                pass

    def close_log_file(self):
        """关闭AT命令日志文件"""
        if self.log_file:
            try:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.log_file.write(f"\n{'='*50}\n")
                self.log_file.write(f"LTE Manager AT交互日志 - 会话结束: {timestamp}\n")
                self.log_file.write(f"{'='*50}\n")
                self.log_file.close()
                self.log_file = None
                print(f"AT命令日志文件已关闭: {self.log_file_path}")
            except Exception as e:
                print(f"关闭AT命令日志文件失败: {str(e)}")

    def _reset_cache(self, specific_key=None):
        """重置缓存，可以重置特定键或所有缓存"""
        if specific_key:
            if specific_key in self.info_cache:
                self.info_cache[specific_key]['value'] = None
                self.info_cache[specific_key]['timestamp'] = 0
        else:
            # 重置所有缓存
            for key in self.info_cache:
                self.info_cache[key]['value'] = None
                self.info_cache[key]['timestamp'] = 0

        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 设备信息缓存已重置")

    def connect(self, at_port, at_baudrate=115200, nmea_port="", nmea_baudrate=9600):
        """Connect to the LTE module"""
        if self.connected:
            return False

        try:
            # 记录连接参数
            self.log_at_command(False, f"[INFO] 连接到LTE模块 - AT端口: {at_port} 波特率: {at_baudrate}")
            if nmea_port:
                self.log_at_command(False, f"[INFO] NMEA端口: {nmea_port} 波特率: {nmea_baudrate}")

            # Connect to AT port
            self.at_serial = serial.Serial(
                port=at_port,
                baudrate=at_baudrate,
                timeout=1
            )

            # Set DTR/RTS
            self.at_serial.setDTR(True)
            self.at_serial.setRTS(True)

            # Connect to NMEA port (if specified)
            if nmea_port:
                self.nmea_serial = serial.Serial(
                    port=nmea_port,
                    baudrate=nmea_baudrate,
                    timeout=1
                )

            # Initialize running flag and start read thread
            self.running = True
            self.read_thread = threading.Thread(target=self._read_serial)
            self.read_thread.daemon = True
            self.read_thread.start()

            # 重置所有缓存，确保获取最新信息
            self._reset_cache()

            # Initialize module
            if not self._initialize_module():
                self.log_at_command(False, f"[ERROR] 初始化模块失败")
                self.status_changed.emit("Failed to initialize module")
                self.disconnect()  # 确保断开连接，释放资源
                return False

            # 等待一段时间以确保模块初始化完成
            time.sleep(1)

            # 获取模块基本信息
            try:
                self._update_carrier_info()
            except Exception as e:
                self.log_at_command(False, f"[WARNING] 获取运营商信息失败: {str(e)}")
                print(f"获取运营商信息失败: {str(e)}")

            try:
                self._update_phone_number()
            except Exception as e:
                self.log_at_command(False, f"[WARNING] 获取电话号码失败: {str(e)}")
                print(f"获取电话号码失败: {str(e)}")

            try:
                self._update_signal_strength()
            except Exception as e:
                self.log_at_command(False, f"[WARNING] 获取信号强度失败: {str(e)}")
                print(f"获取信号强度失败: {str(e)}")

            # Update status
            self.connected = True
            self.status_changed.emit("Connected to LTE module")

            # 记录连接信息到日志
            self.log_at_command(False, f"[INFO] 已连接到LTE模块")

            # 连接成功后日志中记录基本信息
            self.log_at_command(False, f"[INFO] 运营商: {self.carrier if hasattr(self, 'carrier') and self.carrier else '未知'}")
            self.log_at_command(False, f"[INFO] 网络状态: {self.network_type if hasattr(self, 'network_type') and self.network_type else '未知'}")
            self.log_at_command(False, f"[INFO] 电话号码: {self.phone_number if hasattr(self, 'phone_number') and self.phone_number else '未知'}")
            self.log_at_command(False, f"[INFO] 信号强度: {self.signal_strength if hasattr(self, 'signal_strength') and self.signal_strength else '未知'}")

            return True
        except Exception as e:
            self.log_at_command(False, f"[ERROR] 连接失败: {str(e)}")
            self.status_changed.emit(f"Connection error: {str(e)}")

            # 确保所有资源被释放
            try:
                if hasattr(self, 'at_serial') and self.at_serial and self.at_serial.is_open:
                    self.at_serial.close()
                if hasattr(self, 'nmea_serial') and self.nmea_serial and self.nmea_serial.is_open:
                    self.nmea_serial.close()
                self.running = False
                self.connected = False
            except Exception as cleanup_error:
                self.log_at_command(False, f"[ERROR] 清理连接资源时出错: {str(cleanup_error)}")

            return False

    def disconnect(self):
        """Disconnect from the LTE module"""
        if not self.connected:
            return False

        try:
            # 记录断开连接信息到日志
            self.log_at_command(False, "[INFO] 正在断开与LTE模块的连接")

            # 重置所有缓存，确保下次连接时获取最新信息
            self._reset_cache()

            # 确保所有通话已结束
            if self.in_call:
                self.end_call()

            # 确保PCM音频已取消注册
            if self.pcm_registered:
                self._ensure_pcm_audio_unregistered()

            # Stop running flag (will stop read thread)
            self.running = False

            # Close AT serial
            if self.at_serial and self.at_serial.is_open:
                try:
                    # 正常关闭串口
                    self.at_serial.close()
                except Exception as e:
                    self.log_at_command(False, f"[ERROR] 关闭AT串口出错: {str(e)}")

            # Close NMEA serial
            if self.nmea_serial and self.nmea_serial.is_open:
                try:
                    self.nmea_serial.close()
                except Exception as e:
                    self.log_at_command(False, f"[ERROR] 关闭NMEA串口出错: {str(e)}")

            # Wait for read thread to end
            if self.read_thread and self.read_thread.is_alive():
                self.read_thread.join(timeout=2.0)

            # Update status
            self.connected = False
            self.in_call = False
            self.call_number = ""
            self.pcm_registered = False
            self.status_changed.emit("Disconnected from LTE module")

            # Stop cleanup timer
            self.cleanup_timer.stop()

            # 关闭日志文件
            self.close_log_file()

            return True
        except Exception as e:
            self.status_changed.emit(f"Disconnection error: {str(e)}")
            # 记录断开连接错误
            if self.log_file:
                self.log_at_command(False, f"[ERROR] 断开连接出错: {str(e)}")

            # 尝试关闭日志文件，即使发生错误
            self.close_log_file()

            return False

    def is_connected(self):
        """Check if connected to the LTE module"""
        return self.connected

    def send_at_command(self, command, timeout=5.0, expect_response=True):
        """Send AT command to the module and wait for response"""
        if not self.connected or not self.at_serial:
            if self.log_file:
                self.log_at_command(True, f"{command} (未发送 - 模块未连接)")
            return None

        with self.command_lock:
            # 记录发送的AT命令
            self.log_at_command(True, command)

            # 清空响应缓冲区
            self.response_buffer.clear()
            self.last_response_time = time.time()

            # Send command
            cmd = command.strip()
            if not cmd.endswith('\r'):
                cmd += '\r'

            self.at_serial.write(cmd.encode())

            if not expect_response:
                self.log_at_command(False, "(无需等待响应)")
                return None

            # Wait for response
            start_time = time.time()
            response = []
            command_echo_received = False
            response_text = ""

            while time.time() - start_time < timeout:
                # 检查缓冲区是否有新响应
                if self.response_buffer:
                    # 获取并移除第一个响应
                    line = self.response_buffer.pop(0)

                    # 跳过命令回显行
                    if not command_echo_received and line.strip() == command.strip():
                        command_echo_received = True
                        continue

                    response.append(line)

                    # Check if response is complete (ends with OK or ERROR)
                    if line.strip() in ["OK", "ERROR"] or "ERROR" in line:
                        break

                # 短暂等待新响应
                time.sleep(0.05)

                # 检查最后响应时间，如果超过一定时间没有新响应且已收到内容，可以提前结束
                if response and time.time() - self.last_response_time > 0.5:
                    break

            # 构建完整响应文本
            response_text = '\n'.join(response)

            # 如果超时且没有收到任何响应，记录到日志
            if not response:
                self.log_at_command(False, f"(命令响应超时，等待时间: {timeout}秒)")

            return response_text

    def _read_serial(self):
        """Read data from serial port in a separate thread"""
        buffer = ""

        while self.running:
            if not self.at_serial or not self.at_serial.is_open:
                time.sleep(0.1)
                continue

            try:
                # Read data from serial port
                data = self.at_serial.read(self.at_serial.in_waiting or 1)
                if data:
                    text = data.decode('utf-8', errors='replace')
                    buffer += text

                    # Process complete lines
                    while '\r\n' in buffer:
                        line, buffer = buffer.split('\r\n', 1)
                        line = line.strip()

                        if not line:
                            continue

                        # 记录所有接收到的完整行，不论是否是命令回显
                        # 这确保了所有AT交互都被记录，包括模块的主动上报
                        if not line.startswith("AT"):  # 不是命令回显
                            self.log_at_command(False, line)

                        # Process unsolicited responses
                        self._process_unsolicited(line)

                        # 同时使用缓冲区列表和队列来存储响应，确保兼容性
                        self.response_buffer.append(line)
                        self.response_queue.put(line)  # 向旧的队列接口发送数据
                        self.last_response_time = time.time()
            except Exception as e:
                print(f"Serial read error: {str(e)}")
                # 记录读取错误
                if self.log_file:
                    self.log_at_command(False, f"[ERROR] 读取串口数据出错: {str(e)}")
                time.sleep(0.1)

    def _process_unsolicited(self, line):
        """Process unsolicited responses from the module"""
        # Incoming call
        if line.startswith("RING"):
            self.status_changed.emit("Incoming call")
            self.in_call = True
            # 设置为未接听状态
            self.call_connected = False
            # Reset notification flag on new RING
            self.call_notification_sent = False


        # Caller ID
        elif "+CLIP:" in line:
            match = re.search(r'\+CLIP: "([^"]+)"', line)
            if match:
                number = match.group(1)
                self.call_number = number

                # Only emit the signal if we haven't sent a notification for this call yet
                if not self.call_notification_sent and self.in_call:
                    self.call_received.emit(number)
                    self.call_notification_sent = True
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Call notification sent for {number}")

        # Call ended
        elif "NO CARRIER" in line:
            self.in_call = False
            self.call_connected = False
            self.call_notification_sent = False  # Reset the flag when call ends
            self.status_changed.emit("Call ended")

            # 记录通话结束日志，方便调试
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Call ended, NO CARRIER detected")

            # 通话结束时取消PCM音频注册
            self._ensure_pcm_audio_unregistered()

            # 发送通话结束信号
            self.call_ended.emit("Call ended")

        # Voice call begin - 这是通话实际建立的时间点
        elif "VOICE CALL: BEGIN" in line:
            # 设置通话状态为活动
            self.in_call = True
            # 设置为已接通状态
            self.call_connected = True

            # 记录日志
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 通话已建立 (VOICE CALL: BEGIN)")
            self.status_changed.emit("Call in progress")

            # 先确保任何可能存在的PCM注册已取消
            self._unregister_pcm_audio()

            # 短暂延迟后再注册PCM音频，确保模块已稳定
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 延迟100ms后注册PCM音频")
            time.sleep(0.1)  # 先延迟一小段时间

            # 开始注册PCM音频
            self._register_pcm_audio()

        # Voice call end
        elif "VOICE CALL: END:" in line:
            self.in_call = False
            self.call_connected = False
            match = re.search(r'VOICE CALL: END: (\d+)', line)
            duration = "0"
            if match:
                duration = match.group(1)
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Call ended, duration: {duration}")
            else:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Call ended, no duration info")

            # 记录详细日志，包括通话持续时间
            call_minutes = int(duration) // 60
            call_seconds = int(duration) % 60
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 通话结束，持续时间: {call_minutes}分{call_seconds}秒")

            # 首先取消PCM音频注册，然后才发送通话结束信号
            # 这样可以确保PCM音频在通话结束信号处理前已经被取消
            if self._ensure_pcm_audio_unregistered():
                # 在成功取消注册后发送信号
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频已取消注册，发送通话结束信号")
                # 使用threading.Timer代替QTimer，避免线程问题
                threading.Timer(0.2, lambda: self.call_ended.emit(duration)).start()
            else:
                # 即使取消注册失败，也要发送通话结束信号
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频取消注册失败，仍发送通话结束信号")
                # 使用threading.Timer代替QTimer，避免线程问题
                threading.Timer(0.2, lambda: self.call_ended.emit(duration)).start()

        # Missed call
        elif "MISSED_CALL:" in line:
            match = re.search(r'MISSED_CALL: ([^\r\n]+)', line)
            if match:
                missed_info = match.group(1)
                self.status_changed.emit(f"Missed call: {missed_info}")

                # Extract phone number from missed call info
                # Format is typically "HH:MMAM/PM PHONENUMBER"
                parts = missed_info.strip().split()
                if len(parts) >= 2:
                    missed_number = parts[-1]  # Last part should be the phone number
                    # Signal call ended to stop ringtone
                    self.call_ended.emit("Missed")
                    # Also emit missed call signal with the number
                    self.call_number = missed_number
                    self.status_changed.emit(f"Missed call from {missed_number}")

        # SMS received (direct content mode)
        elif line.startswith("+CMT:"):
            # 检查是否为长短信
            if self._is_concatenated_sms(line):
                self._handle_concatenated_sms(line)
            else:
                # 处理普通短信
                self._handle_regular_sms(line)

        elif self.waiting_for_sms_content:
            # This is the SMS content line
            self.waiting_for_sms_content = False
            message = line

            # Check if the content is in UCS2 format (hex string)
            if all(c in "0123456789ABCDEFabcdef" for c in line.replace(" ", "")):
                try:
                    # 检查是否为长短信的一部分
                    if self._is_part_of_concatenated_sms(line):
                        # 处理长短信部分
                        self._process_concatenated_sms_part(self.pending_sms_sender, self.pending_sms_timestamp, line)
                    else:
                        # 普通UCS2短信，直接解码
                        message = ucs2_to_text(line)
                        self.status_changed.emit("Decoded UCS2 message")

                        # 发送完整消息
                        self.sms_received.emit(
                            self.pending_sms_sender,
                            self.pending_sms_timestamp,
                            message
                        )
                except Exception as e:
                    self.status_changed.emit(f"Failed to decode UCS2: {str(e)}")
                    # Keep original if decoding fails
                    message = line

                    # 发送原始消息
                    self.sms_received.emit(
                        self.pending_sms_sender,
                        self.pending_sms_timestamp,
                        message
                    )
            else:
                # 非UCS2编码，直接发送
                self.sms_received.emit(
                    self.pending_sms_sender,
                    self.pending_sms_timestamp,
                    message
                )

            # Clear pending SMS data
            self.pending_sms_sender = None
            self.pending_sms_timestamp = None

        # SMS received (index mode)
        elif line.startswith("+CMTI:"):
            match = re.search(r'\+CMTI: "([^"]+)",(\d+)', line)
            if match:
                storage, index = match.group(1), match.group(2)
                self.status_changed.emit(f"New SMS at index {index}")
                # Fetch SMS content
                self._fetch_sms(storage, index)

        # DTMF tone received
        elif "+RXDTMF:" in line:
            match = re.search(r'\+RXDTMF: (\d)', line)
            if match:
                tone = match.group(1)
                self.dtmf_received.emit(tone)

        # SMS full
        elif "+SMS FULL" in line:
            self.status_changed.emit("SMS storage full. Please delete some messages.")

    def _is_concatenated_sms(self, header_line):
        """检查是否为长短信"""
        # 检查是否包含长短信特征
        # 对于UCS2编码的长短信，通常有特定的格式标识
        if ",145," in header_line and ",0,8," in header_line:
            return True
        return False

    def _handle_regular_sms(self, header_line):
        """处理普通短信"""
        # Parse SMS header
        header_match = re.search(r'\+CMT: "([^"]*)",[^,]*,"([^"]*)"', header_line)
        if header_match:
            sender = header_match.group(1)
            timestamp = header_match.group(2)

            # Check if sender is in UCS2 format (starts with 00)
            if sender.startswith("00"):
                try:
                    sender = ucs2_to_text(sender)
                except:
                    pass  # Keep original if decoding fails

            self.pending_sms_sender = sender
            self.pending_sms_timestamp = timestamp
            self.status_changed.emit(f"SMS received from {sender}")
        else:
            self.pending_sms_sender = "Unknown"
            self.pending_sms_timestamp = QDateTime.currentDateTime().toString("yy/MM/dd,hh:mm:ss")
            self.status_changed.emit("SMS received")

        # Next line will contain the SMS content
        self.waiting_for_sms_content = True

    def _handle_concatenated_sms(self, header_line):
        """处理长短信"""
        try:
            # 解析长短信头部
            # 格式: +CMT: "sender","","timestamp",145,36,0,8,"service_center",145,64
            parts = header_line.split(',')
            if len(parts) < 10:
                # 格式不符合预期，作为普通短信处理
                self._handle_regular_sms(header_line)
                return

            # 提取发送者和时间戳
            sender = parts[0].replace('+CMT: ', '').strip('"')
            timestamp = parts[2].strip('"')

            # 检查发送者是否为UCS2格式
            if sender.startswith("00"):
                try:
                    sender = ucs2_to_text(sender)
                except:
                    pass  # 解码失败时保留原始格式

            # 设置等待内容标志
            self.pending_sms_sender = sender
            self.pending_sms_timestamp = timestamp
            self.waiting_for_sms_content = True

            # 记录长短信信息，下一行将包含内容
            self.status_changed.emit(f"Concatenated SMS part received from {sender}")
        except Exception as e:
            self.status_changed.emit(f"Error parsing concatenated SMS header: {str(e)}")
            # 出错时尝试作为普通短信处理
            self._handle_regular_sms(header_line)

    def _process_concatenated_sms_part(self, sender, timestamp, content):
        """处理长短信的一部分"""
        try:
            # 移除空格
            content = content.replace(" ", "")

            # 打印原始内容用于调试
            self.status_changed.emit(f"长短信原始内容: {content[:50]}...")

            # 解码内容
            try:
                decoded_content = ucs2_to_text(content)
                self.status_changed.emit(f"解码后内容: {decoded_content[:50]}...")
            except Exception as decode_error:
                self.status_changed.emit(f"UCS2解码错误: {str(decode_error)}")
                # 尝试不同的解码方法
                try:
                    # 尝试直接从十六进制转换为字节，然后解码
                    hex_bytes = binascii.unhexlify(content)
                    decoded_content = hex_bytes.decode('utf-16-be', errors='replace')
                    self.status_changed.emit(f"替代解码方法成功: {decoded_content[:50]}...")
                except Exception as alt_error:
                    self.status_changed.emit(f"替代解码方法错误: {str(alt_error)}")
                    # 如果解码失败，使用原始内容
                    decoded_content = content
                    self.sms_received.emit(
                        self.pending_sms_sender,
                        self.pending_sms_timestamp,
                        f"[解码失败] {content[:100]}..."
                    )
                    return

            # 检查特殊格式，尝试直接提取有效负载
            if "62117ED94F6053D14E86957F6587672C" in content:
                # 基于固定标记提取后面的有效内容
                # 格式可能是：标记 + 消息前缀 + 003A(冒号) + URL编码
                parts = content.split("003A", 1)  # 003A是冒号的UCS2编码
                if len(parts) > 1 and parts[1]:
                    # 只解码URL部分
                    try:
                        url_part = ucs2_to_text("003A" + parts[1])  # 加回冒号
                        self.status_changed.emit(f"提取的URL部分: {url_part}")

                        # 直接提取URL
                        url_match = re.search(r':(https?://[^\s]+)', url_part)
                        url = url_match.group(1) if url_match else url_part

                        # 使用前缀 + URL格式
                        prefix = "消息"  # 默认前缀

                        # 使用发送者和时间戳的前10个字符作为唯一标识符
                        sms_id = f"{sender}_{timestamp[:10]}"

                        # 如果是新的长短信，初始化存储
                        if sms_id not in self.concat_sms_parts:
                            self.concat_sms_parts[sms_id] = {
                                'sender': sender,
                                'timestamp': timestamp,
                                'parts': [],
                                'urls': [],
                                'received_time': time.time(),
                                'prefix': prefix
                            }

                        # 存储这一部分
                        if url and url not in self.concat_sms_parts[sms_id]['urls']:
                            self.concat_sms_parts[sms_id]['urls'].append(url)
                            self.concat_sms_parts[sms_id]['parts'].append(url_part)

                            # 更新接收时间
                            self.concat_sms_parts[sms_id]['received_time'] = time.time()

                            # 记录日志
                            part_num = len(self.concat_sms_parts[sms_id]['parts'])
                            self.status_changed.emit(f"接收到长短信的第{part_num}部分，来自{sender}")

                            # 检查是否需要合并
                            QTimer.singleShot(2000, lambda: self._check_and_merge_sms(sms_id))
                            return
                    except Exception as url_error:
                        self.status_changed.emit(f"URL提取错误: {str(url_error)}")

            # 常规处理逻辑（如果特殊处理失败）
            # 提取URL部分
            url_match = re.search(r'(https?://[^\s]+)', decoded_content)
            url = url_match.group(1) if url_match else ""

            # 提取消息前缀部分（冒号前的内容）
            prefix = ""
            if ":" in decoded_content:
                prefix = decoded_content.split(":", 1)[0].strip()
            else:
                prefix = "消息"  # 默认前缀

            # 使用发送者和时间戳的前10个字符作为唯一标识符
            sms_id = f"{sender}_{timestamp[:10]}"

            # 如果是新的长短信，初始化存储
            if sms_id not in self.concat_sms_parts:
                self.concat_sms_parts[sms_id] = {
                    'sender': sender,
                    'timestamp': timestamp,
                    'parts': [],
                    'urls': [],
                    'received_time': time.time(),
                    'prefix': prefix
                }

            # 存储这一部分
            if url and url not in self.concat_sms_parts[sms_id]['urls']:
                self.concat_sms_parts[sms_id]['urls'].append(url)
                self.concat_sms_parts[sms_id]['parts'].append(decoded_content)

                # 更新接收时间
                self.concat_sms_parts[sms_id]['received_time'] = time.time()

                # 记录日志
                part_num = len(self.concat_sms_parts[sms_id]['parts'])
                self.status_changed.emit(f"接收到长短信的第{part_num}部分，来自{sender}")

                # 检查是否需要合并
                QTimer.singleShot(2000, lambda: self._check_and_merge_sms(sms_id))
            else:
                # 没有URL或已经存在的URL
                # 尝试直接发送完整内容
                self.status_changed.emit(f"无法提取URL或URL重复，尝试直接发送内容")
                self.sms_received.emit(
                    sender,
                    timestamp,
                    decoded_content
                )

        except Exception as e:
            self.status_changed.emit(f"长短信处理错误: {str(e)}")
            # 出错时尝试作为普通短信处理
            try:
                decoded = ucs2_to_text(content) if all(c in "0123456789ABCDEFabcdef" for c in content) else content
                self.sms_received.emit(
                    self.pending_sms_sender,
                    self.pending_sms_timestamp,
                    decoded
                )
            except:
                # 如果解码也失败，发送原始内容
                self.sms_received.emit(
                    self.pending_sms_sender,
                    self.pending_sms_timestamp,
                    content
                )

    def _check_and_merge_sms(self, sms_id):
        """检查并合并长短信"""
        if sms_id not in self.concat_sms_parts:
            return

        sms_info = self.concat_sms_parts[sms_id]

        # 检查是否已经过了足够的时间
        current_time = time.time()
        if current_time - sms_info['received_time'] < 2:  # 2秒内收到的部分
            # 还不到合并的时间，可能还有更多部分
            return

        # 合并所有部分
        merged_content = self._merge_sms_parts(sms_id)

        # 发送完整消息
        self.sms_received.emit(
            sms_info['sender'],
            sms_info['timestamp'],
            merged_content
        )

        # 清理已处理的长短信
        del self.concat_sms_parts[sms_id]

        # 记录日志
        self.status_changed.emit(f"Concatenated SMS fully received from {sms_info['sender']}")

    def _merge_sms_parts(self, sms_id):
        """合并长短信的所有部分"""
        if sms_id not in self.concat_sms_parts:
            return ""

        sms_info = self.concat_sms_parts[sms_id]

        # 如果只有一个部分，直接返回
        if len(sms_info['parts']) == 1:
            return sms_info['parts'][0]

        # 合并所有部分
        prefix = sms_info.get('prefix', '')
        urls = sms_info.get('urls', [])

        if urls:
            # 只返回URL列表，每行一个
            if prefix:
                merged_content = f"{prefix}:\n" + "\n".join(urls)
            else:
                merged_content = "\n".join(urls)
        else:
            # 如果没有提取到URL，直接合并所有部分
            merged_content = "\n".join(sms_info['parts'])

        return merged_content

    def _cleanup_old_sms_parts(self):
        """清理超时的长短信部分"""
        current_time = time.time()
        sms_ids_to_remove = []

        for sms_id, sms_info in self.concat_sms_parts.items():
            if current_time - sms_info['received_time'] > self.concat_sms_timeout:
                sms_ids_to_remove.append(sms_id)

        for sms_id in sms_ids_to_remove:
            del self.concat_sms_parts[sms_id]
            self.status_changed.emit(f"Removed timeout concatenated SMS {sms_id}")

    def _decode_pdu_message(self, pdu_str):
        """Decode PDU format message (including Chinese characters)"""
        try:
            # Remove spaces and convert to bytes
            pdu_str = pdu_str.replace(" ", "")

            # 检查是否为长短信的一部分
            if self._is_part_of_concatenated_sms(pdu_str):
                # 可能是长短信的一部分，需要特殊处理
                # 这里需要根据实际的PDU格式进行解析
                pass

            # Try to decode using our utility function
            return ucs2_to_text(pdu_str)
        except Exception as e:
            print(f"PDU decode error: {str(e)}")
            # If decoding fails, return the original string
            return f"[Decode error: {pdu_str[:30]}...]"

    def _is_part_of_concatenated_sms(self, content):
        """检查内容是否为长短信的一部分"""
        # 移除空格
        content = content.replace(" ", "")

        # 检查是否为UCS2编码
        if not all(c in "0123456789ABCDEFabcdef" for c in content):
            return False

        # 检查内容长度是否足够
        if len(content) < 10:
            return False

        # 根据用户提供的示例，检查是否包含特定的模式
        # 示例中的长短信内容以"62117ED94F6053D14E86957F6587672C"开头
        if content.startswith("62117ED94F6053D14E86957F6587672C"):
            return True

        return False

    def _initialize_module(self):
        """Initialize the LTE module with basic settings"""
        try:
            # 在日志中标记初始化模块的开始
            self.log_at_command(False, "[INFO] 开始初始化LTE模块")

            # AT echo off
            self.send_at_command("ATE0")

            # Set SMS text mode
            self.send_at_command("AT+CMGF=1")

            # Set SMS character set to GSM
            self.send_at_command('AT+CSCS="GSM"')

            # Enable new SMS notification
            self.send_at_command('AT+CNMI=2,1,0,0,0')

            # Enable caller ID notification
            self.send_at_command("AT+CLIP=1")

            # Set call progress monitoring URC (Unsolicited Result Code)
            self.send_at_command("AT+XCALLSTAT=1")

            # Get IMEI
            self.send_at_command("AT+GSN")

            # 在日志中标记初始化模块的完成
            self.log_at_command(False, "[INFO] LTE模块初始化完成")
            return True
        except Exception as e:
            self.status_changed.emit(f"Module initialization error: {str(e)}")
            # 记录初始化错误
            self.log_at_command(False, f"[ERROR] 模块初始化错误: {str(e)}")
            return False

    def _get_module_info(self):
        """Get module information (internal method)"""
        if not self.connected:
            return {}

        # 尝试从缓存中获取
        cached_value = self._get_cached_value('module_info')
        if cached_value is not None:
            return cached_value

        # 缓存中没有，需要重新获取
        try:
            # 确保基本属性已被初始化
            if not self.manufacturer:
                manufacturer_response = self.send_at_command("AT+CGMI")
                if "OK" in manufacturer_response:
                    # 移除命令回显和OK响应，只保留实际内容
                    lines = [line.strip() for line in manufacturer_response.split('\n') if line.strip()]
                    # 过滤掉AT命令回显和OK响应
                    content_lines = [line for line in lines if line != "OK" and not line.startswith("AT+")]
                    self.manufacturer = '\n'.join(content_lines).strip()
                else:
                    self.manufacturer = "Unknown"

            if not self.model:
                model_response = self.send_at_command("AT+CGMM")
                if "OK" in model_response:
                    # 移除命令回显和OK响应，只保留实际内容
                    lines = [line.strip() for line in model_response.split('\n') if line.strip()]
                    # 过滤掉AT命令回显和OK响应
                    content_lines = [line for line in lines if line != "OK" and not line.startswith("AT+")]
                    self.model = '\n'.join(content_lines).strip()
                else:
                    self.model = "Unknown"

            if not self.imei:
                imei_response = self.send_at_command("AT+CGSN")
                if "OK" in imei_response:
                    # 移除命令回显和OK响应，只保留实际内容
                    lines = [line.strip() for line in imei_response.split('\n') if line.strip()]
                    # 过滤掉AT命令回显和OK响应
                    content_lines = [line for line in lines if line != "OK" and not line.startswith("AT+")]
                    self.imei = '\n'.join(content_lines).strip()
                else:
                    self.imei = "Unknown"

            if not self.firmware:
                firmware_response = self.send_at_command("AT+CGMR")
                if "OK" in firmware_response:
                    # 尝试提取特定格式的固件版本
                    match = re.search(r'\+CGMR: (.+)', firmware_response)
                    if match:
                        self.firmware = match.group(1)
                    else:
                        # 移除命令回显和OK响应，只保留实际内容
                        lines = [line.strip() for line in firmware_response.split('\n') if line.strip()]
                        # 过滤掉AT命令回显和OK响应
                        content_lines = [line for line in lines if line != "OK" and not line.startswith("AT+")]
                        self.firmware = '\n'.join(content_lines).strip()
                else:
                    self.firmware = "Unknown"

            # 更新其他信息
            if not self.phone_number:
                self._update_phone_number()

            if not self.carrier or not self.network_type:
                self._update_carrier_info()

            if not self.signal_strength:
                self._update_signal_strength()

            # 组装模块信息字典
            module_info = {
                'manufacturer': self.manufacturer,
                'model': self.model,
                'imei': self.imei,
                'firmware': self.firmware,
                'phone_number': self.phone_number,
                'carrier': self.carrier,
                'network_type': self.network_type,
                'signal_strength': self.signal_strength
            }

            # 更新缓存
            self._update_cache('module_info', module_info)
            return module_info
        except Exception as e:
            print(f"获取模块信息出错: {str(e)}")
            # 出错时返回带有默认值的字典
            return {
                'manufacturer': 'Error',
                'model': 'Error',
                'imei': 'Error',
                'firmware': 'Error',
                'phone_number': 'Error',
                'carrier': 'Error',
                'network_type': 'Error',
                'signal_strength': 'Error'
            }

    def get_module_info(self):
        """Get module information"""
        if not self.connected:
            return {}

        return self._get_module_info()

    def get_carrier_info(self):
        """Get carrier information"""
        if not self.connected:
            return ("未连接", "未连接")

        try:
            self._update_carrier_info()

            # 确保carrier和network_type属性存在
            if not hasattr(self, 'carrier') or not self.carrier:
                self.carrier = "未知"
            if not hasattr(self, 'network_type') or not self.network_type:
                self.network_type = "未知"

            return (self.carrier, self.network_type)
        except Exception as e:
            self.log_at_interaction(f"获取运营商信息失败: {str(e)}")
            print(f"获取运营商信息失败: {str(e)}")
            return ("获取出错", "获取出错")

    def get_phone_number(self):
        """Get phone number"""
        if not self.connected:
            return "未连接"

        try:
            self._update_phone_number()

            # 确保phone_number属性存在
            if not hasattr(self, 'phone_number') or not self.phone_number:
                self.phone_number = "未知"

            return self.phone_number
        except Exception as e:
            self.log_at_interaction(f"获取电话号码失败: {str(e)}")
            print(f"获取电话号码失败: {str(e)}")
            return "获取出错"

    def _update_carrier_info(self):
        """Update carrier information"""
        try:
            # 初始化默认值
            self.carrier = "未知"
            self.network_type = "未知"

            response = self.send_at_command("AT+COPS?")
            if response and "+COPS:" in response:
                # Try to parse carrier information
                match = re.search(r'\+COPS:\s*\d+,\d+,"([^"]+)"', response)
                if match:
                    carrier_name = match.group(1)
                    # Check if it's a numeric code like "46000"
                    if re.match(r'^\d+$', carrier_name):
                        # Map common Chinese carrier codes
                        carriers = {
                            "46000": "中国移动",
                            "46001": "中国联通",
                            "46002": "中国移动",
                            "46003": "中国电信",
                            "46004": "中国移动",
                            "46005": "中国电信",
                            "46006": "中国联通",
                            "46007": "中国移动",
                            "46008": "中国电信",
                            "46009": "中国联通",
                            "46011": "中国电信",
                        }
                        self.carrier = carriers.get(carrier_name, carrier_name)
                    else:
                        self.carrier = carrier_name

            # Try to get network type
            network_response = self.send_at_command("AT+CPSI?")
            if network_response and "+CPSI:" in network_response:
                parts = network_response.strip().split('\n')[0].replace("+CPSI: ", "").split(',')
                if len(parts) >= 2:
                    self.network_type = f"{parts[0]}/{parts[1]}"
                    # Add band info if available
                    if len(parts) >= 7:
                        self.network_type += f"/{parts[6]}"

            # 更新缓存 - 既缓存单个值，也缓存组合值以便不同的获取方式
            self._update_cache('carrier', self.carrier)
            return self.carrier
        except Exception as e:
            self.log_at_command(False, f"[ERROR] 获取运营商信息失败: {str(e)}")
            print(f"获取运营商信息失败: {str(e)}")
            self.carrier = "获取出错"
            self.network_type = "获取出错"
            self._update_cache('carrier', self.carrier)
            return self.carrier

    def _update_signal_strength(self):
        """Update signal strength information"""
        try:
            response = self.send_at_command("AT+CSQ")
            if not response or "+CSQ:" not in response:
                self.signal_strength = ("未知", "无响应")
                return self.signal_strength

            match = re.search(r'\+CSQ:\s*(\d+),\s*(\d+)', response)
            if match:
                rssi = int(match.group(1))
                ber = int(match.group(2))

                # 计算信号百分比
                if rssi == 99:
                    percent = 0
                    desc = "无信号"
                else:
                    # RSSI范围是0-31，对应-113到-51 dBm
                    percent = min(100, int((rssi / 31.0) * 100))

                    # 根据信号强度确定描述
                    if rssi > 25:  # > -67 dBm
                        desc = "极好"
                    elif rssi > 19:  # > -79 dBm
                        desc = "很好"
                    elif rssi > 14:  # > -89 dBm
                        desc = "好"
                    elif rssi > 9:   # > -99 dBm
                        desc = "一般"
                    elif rssi > 4:   # > -107 dBm
                        desc = "差"
                    else:
                        desc = "很差"

                # 将信号强度保存为元组(信号文本, 描述)
                self.signal_strength = (f"{percent}%", desc)
                return self.signal_strength
            else:
                # 如果无法解析信号强度，返回默认值
                self.signal_strength = ("未知", "格式错误")
                return self.signal_strength

        except Exception as e:
            self.log_at_interaction(f"解析信号强度失败: {str(e)}")
            print(f"解析信号强度失败: {str(e)}")
            self.signal_strength = ("错误", str(e)[:20])
            return self.signal_strength

    def _fetch_sms(self, storage, index):
        """Fetch SMS content by index"""
        response = self.send_at_command(f'AT+CMGR={index}')
        if response and "+CMGR:" in response:
            # Parse SMS header
            header_match = re.search(r'\+CMGR: "[^"]*","([^"]*)",[^,]*,"([^"]*)"', response)
            if header_match:
                sender = header_match.group(1)
                timestamp = header_match.group(2)

                # Check if sender is in UCS2 format (starts with 00)
                if sender.startswith("00"):
                    try:
                        sender = ucs2_to_text(sender)
                    except:
                        pass  # Keep original if decoding fails

                # Extract message content
                lines = response.split('\n')
                message = ""

                if len(lines) > 1:
                    content_line = lines[1].strip()
                    message = content_line

                    # Check if the content is in UCS2 format (hex string)
                    if all(c in "0123456789ABCDEFabcdef" for c in content_line.replace(" ", "")):
                        try:
                            # Try to decode as UCS2
                            message = ucs2_to_text(content_line)
                            self.status_changed.emit("Decoded UCS2 message from storage")
                        except Exception as e:
                            self.status_changed.emit(f"Failed to decode UCS2 from storage: {str(e)}")
                            # Keep original if decoding fails

                self.sms_received.emit(sender, timestamp, message)

    def _register_pcm_audio(self):
        """注册PCM音频（用于通话开始时）
        按照文档要求，在VOICE CALL: BEGIN后执行AT+CPCMREG=1
        """
        if not self.connected or not self.at_serial:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注册失败：未连接")
            return False

        # 如果已经不在通话中了，跳过注册
        if not self.in_call:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 不在通话中，跳过PCM音频注册")
            self.status_changed.emit("Not in call, PCM audio registration skipped")
            return False

        try:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 开始PCM音频注册过程")

            # 清除可能的额外数据
            if self.at_serial.in_waiting > 0:
                self.at_serial.read(self.at_serial.in_waiting)
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 清除了缓冲区数据")

            # 设置PCM格式为8K采样率（如需要16K，可更改为AT+CPCMFRM=1）
            try:
                with self.command_lock:
                    self.at_serial.write(b'AT+CPCMFRM=0\r')
                    time.sleep(0.1)
                    if self.at_serial.in_waiting > 0:
                        resp = self.at_serial.read(self.at_serial.in_waiting).decode('utf-8', errors='ignore')
                        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM格式设置响应: {resp}")
            except Exception as e:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 设置PCM格式出错: {str(e)}")

            # 直接发送PCM音频注册命令，使用更短的超时
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 发送PCM音频注册命令")

            # 确保没有另一个命令在发送
            with self.command_lock:
                self.at_serial.write(b'AT+CPCMREG=1\r')
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注册命令已发送")

                # 等待较短的时间以获取响应
                time.sleep(0.1)

                # 试图读取响应
                response = ""
                start_time = time.time()
                while time.time() - start_time < 0.5:  # 最多等待0.5秒
                    if self.at_serial.in_waiting > 0:
                        response += self.at_serial.read(self.at_serial.in_waiting).decode('utf-8', errors='ignore')
                        if "OK" in response or "ERROR" in response:
                            break
                    time.sleep(0.05)

                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注册响应: {response}")

                # 记录是否成功
                success = "OK" in response

            # 根据响应结果发送状态更新
            if success:
                self.status_changed.emit("PCM audio registered successfully")
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注册成功")
            else:
                self.status_changed.emit("PCM audio registration sent")
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注册状态未知")

            # 无论响应如何，发送激活信号，系统将尝试处理音频
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 发送PCM音频激活信号")
            self.pcm_audio_status.emit(True)

            # 添加调试记录
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注册流程完成")
            return True

        except Exception as e:
            self.status_changed.emit(f"PCM audio registration error: {str(e)}")
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注册出错: {str(e)}")

            # 错误发生时，仍然尝试激活音频，保持一致行为
            self.pcm_audio_status.emit(True)
            return False

    def _unregister_pcm_audio(self):
        """取消注册PCM音频（用于通话结束时）
        按照文档要求，在VOICE CALL: END后执行AT+CPCMREG=0
        """
        if not self.connected or not self.at_serial:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 取消PCM音频注册失败：未连接")
            # 即使未连接，也发送停止信号
            self.pcm_audio_status.emit(False)
            return False

        try:
            # 确保没有另一个命令在发送
            with self.command_lock:
                # 清除任何待处理的数据
                if self.at_serial.in_waiting > 0:
                    self.at_serial.read(self.at_serial.in_waiting)
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 清除了PCM音频注销前的缓冲区数据")

                # 使用直接写入代替send_at_command，避免阻塞
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 发送PCM音频注销命令")
                self.at_serial.write(b'AT+CPCMREG=0\r')
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注销命令已发送")

                # 给一点时间让模块响应
                time.sleep(0.1)

                # 尝试读取响应，但不等待过长时间
                response = ""
                start_time = time.time()
                while time.time() - start_time < 0.3:  # 等待最多0.3秒
                    if self.at_serial.in_waiting > 0:
                        response += self.at_serial.read(self.at_serial.in_waiting).decode('utf-8', errors='ignore')
                        if "OK" in response:
                            break
                    time.sleep(0.05)

                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注销响应: {response}")
                success = "OK" in response

            # 根据响应结果更新状态
            if success:
                self.status_changed.emit("PCM audio unregistered successfully")
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注销成功")
            else:
                self.status_changed.emit("PCM audio unregistration sent")
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注销状态未知")

            # 无论命令是否成功，都发送停止信号
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 发送PCM音频停止信号")
            self.pcm_audio_status.emit(False)

            return True

        except Exception as e:
            self.status_changed.emit(f"PCM audio unregistration error: {str(e)}")
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注销错误: {str(e)}")

            # 出错时也发送停止信号
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 注销出错，但仍发送停止信号")
            self.pcm_audio_status.emit(False)
            return False

    def _ensure_pcm_audio_unregistered(self):
        """确保PCM音频被取消注册"""
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 确保PCM音频已注销")

        # 首先确保通话状态正确
        self.in_call = False  # 强制设置为非通话状态，确保在所有情况下状态一致

        # 直接取消注册PCM音频
        result = self._unregister_pcm_audio()
        if result:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注销成功完成")
        else:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注销可能未完成，但已发送停止信号")

        # 返回实际的操作结果，以便调用者可以适当处理
        return result

    def make_call(self, number):
        """Make a phone call (MO - Mobile Originated call)"""
        if not self.connected:
            return False

        # 如果已经在通话中，先结束当前通话
        if self.in_call:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 已在通话中，先结束当前通话")
            self.end_call()

            # 使用循环检查通话状态，而不是固定等待时间
            wait_start = time.time()
            while self.in_call and time.time() - wait_start < 3.0:  # 最多等待3秒
                time.sleep(0.1)

            if self.in_call:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 无法结束先前通话，放弃拨号")
                self.status_changed.emit("Failed to end previous call")
                return False

        # 确保PCM音频已经关闭
        self._ensure_pcm_audio_unregistered()

        # 设置模块为语音模式
        try:
            self.send_at_command("AT+FCLASS=8")  # 设置为语音模式，确保正确处理语音呼叫
        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 设置语音模式出错: {str(e)}")

        # 发起拨号命令
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 发起拨打电话到 {number}")
        response = self.send_at_command(f"ATD{number};")

        if "OK" in response:
            self.call_number = number
            self.status_changed.emit(f"Calling {number}")
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 正在拨打 {number}")

            # 注意：设置in_call=True应该在收到VOICE CALL: BEGIN之后
            # 这里只记录目标号码，不立即设置呼叫状态
            # 在收到VOICE CALL: BEGIN事件后，会自动设置in_call=True并注册PCM音频

            return True
        else:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 拨打电话失败: {response}")
            self.status_changed.emit(f"Failed to call {number}")
            return False

    def answer_call(self):
        """Answer an incoming call."""
        # 检查是否有来电
        self.log_at_interaction("尝试接听电话...")
        call_status = self.get_call_status()
        has_incoming = False

        for call in call_status:
            if call.get('state') == 'incoming':
                has_incoming = True
                break

        if not has_incoming:
            self.log_at_interaction("没有检测到来电，无法接听")
            return False

        # 确保在接听前停止铃声
        try:
            from sound_utils import stop_all_ringtones
            stop_all_ringtones()
        except Exception as e:
            self.log_at_interaction(f"停止铃声时出错: {str(e)}")

        # 先确保PCM音频被注销，避免冲突
        self._ensure_pcm_audio_unregistered()

        # 发送接听命令
        self.log_at_interaction("发送ATA命令接听电话")
        response = self.send_at_command("ATA", timeout=10)

        if "OK" in response or "CONNECT" in response:
            self.log_at_interaction("电话已接通，准备注册PCM音频")

            # 创建一个延时，给电话接通一点时间后再注册PCM音频
            def register_pcm_with_delay():
                time.sleep(1)  # 等待1秒确保电话真正接通
                self._ensure_pcm_audio_registered()
                self.log_at_interaction("PCM音频已注册，通话已建立")

            # 使用线程实现延时注册，避免主线程阻塞
            threading.Thread(target=register_pcm_with_delay, daemon=True).start()

            # 再次检查呼叫状态，确认是否真正接通
            time.sleep(2)  # 等待足够的时间让状态更新
            new_call_status = self.get_call_status()
            for call in new_call_status:
                if call.get('state') == 'active':
                    self.log_at_interaction("确认电话已接通")
                    return True

            self.log_at_interaction("电话可能未成功接通，请再次检查状态")
            return True  # 即使状态检查不是active，仍然返回True因为命令执行成功
        else:
            self.log_at_interaction(f"接听电话失败: {response}")
            return False

    def end_call(self):
        """结束当前通话，根据通话状态使用不同的挂断命令"""
        if not self.connected:
            return False

        # 获取当前通话状态
        calls = self.get_call_status()
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 当前通话状态: {calls}")

        # 根据通话状态选择合适的挂断命令
        if not calls:
            # 没有活动通话，但为安全起见仍发送挂断命令
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 无活动通话，但仍发送挂断命令")
            response = self.send_at_command("ATH")
        else:
            # 检查第一个通话的状态
            call = calls[0]
            stat = call.get('stat', -1)

            if stat == 4:  # 来电中(MT)
                # 来电振铃状态，使用 AT+CHUP 命令挂断
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 使用AT+CHUP挂断未接通的来电")
                response = self.send_at_command("AT+CHUP")
            else:
                # 其他状态使用 ATH 命令挂断
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 使用ATH挂断通话，状态: {self.call_states.get(stat, '未知')}")
                response = self.send_at_command("ATH")

        if "OK" in response:
            self.in_call = False
            self.call_connected = False
            self.status_changed.emit("通话结束")
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 通话已结束")

            # 通话结束后，立即取消PCM音频注册
            self._ensure_pcm_audio_unregistered()

            return True

        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 挂断通话失败，响应: {response}")
        return False

    def send_sms(self, number, message):
        """Send SMS message"""
        if not self.connected:
            return False

        # Format the phone number
        formatted_number = format_phone_number(number)

        # 清空响应缓冲区
        with self.command_lock:
            self.response_buffer.clear()
            self.last_response_time = time.time()

        # Set text mode and wait for OK response
        response = self.send_at_command("AT+CMGF=1")
        if "OK" not in response:
            self.status_changed.emit("Failed to set SMS text mode")
            return False

        # Add debug message
        self.status_changed.emit(f"Sending SMS to {formatted_number}")

        try:
            # Check if message contains Chinese characters
            if is_chinese_text(message):
                # Set character set to UCS2 for Unicode support
                response = self.send_at_command('AT+CSCS="UCS2"')
                if "OK" not in response:
                    self.status_changed.emit("Failed to set UCS2 character set")
                    return False

                # Convert message to UCS2 hex string
                hex_message = text_to_ucs2(message)
                if not hex_message:
                    self.status_changed.emit("Failed to encode message")
                    return False

                # Convert phone number to UCS2 format
                hex_number = text_to_ucs2(formatted_number)
                if not hex_number:
                    self.status_changed.emit("Failed to encode phone number")
                    return False

                # 清空响应缓冲区
                with self.command_lock:
                    self.response_buffer.clear()

                # Send message command with UCS2 encoded phone number
                cmd = f'AT+CMGS="{hex_number}"'
                self.at_serial.write((cmd + '\r').encode())
                time.sleep(0.5)  # Wait for > prompt

                # Send message content and Ctrl+Z to end
                self.at_serial.write(hex_message.encode() + b'\x1A')
                self.status_changed.emit("Sending UCS2 encoded message...")
            else:
                # Set character set to GSM for ASCII support
                response = self.send_at_command('AT+CSCS="GSM"')
                if "OK" not in response:
                    self.status_changed.emit("Failed to set GSM character set")
                    return False

                # 清空响应缓冲区
                with self.command_lock:
                    self.response_buffer.clear()

                # Send message command
                cmd = f'AT+CMGS="{formatted_number}"'
                self.at_serial.write((cmd + '\r').encode())
                time.sleep(0.5)  # Wait for > prompt

                # Send message content and Ctrl+Z to end
                self.at_serial.write(message.encode() + b'\x1A')
                self.status_changed.emit("Sending ASCII message...")

            # Wait for response with longer timeout
            start_time = time.time()
            response_lines = []

            while time.time() - start_time < 15.0:  # Increased timeout to 15 seconds
                # 检查是否有新响应
                if self.response_buffer:
                    with self.command_lock:
                        # 获取并移除第一个响应
                        line = self.response_buffer.pop(0)

                    response_lines.append(line)
                    self.status_changed.emit(f"SMS response: {line}")

                    if "+CMGS:" in line:
                        self.status_changed.emit(f"SMS sent to {formatted_number}")
                        return True
                    elif "ERROR" in line or "+CMS ERROR:" in line:
                        self.status_changed.emit(f"SMS error: {line}")
                        return False

                # 短暂等待新响应
                time.sleep(0.1)

            # If we get here, we timed out waiting for a response
            last_response = response_lines[-1] if response_lines else 'None'
            self.status_changed.emit(f"SMS send timeout. Last response: {last_response}")
            return False

        except Exception as e:
            self.status_changed.emit(f"SMS send exception: {str(e)}")
            return False

    def delete_sms(self, index=None, delete_type=None):
        """Delete SMS messages

        delete_type:
        0 - Delete message at index
        1 - Delete all read messages
        2 - Delete all read and sent messages
        3 - Delete all read, sent and unsent messages
        4 - Delete all messages
        """
        if not self.connected:
            return False

        if index is not None and delete_type is not None:
            command = f"AT+CMGD={index},{delete_type}"
        elif index is not None:
            command = f"AT+CMGD={index}"
        elif delete_type is not None:
            command = f"AT+CMGD=1,{delete_type}"
        else:
            return False

        response = self.send_at_command(command)
        if "OK" in response:
            self.status_changed.emit("SMS deleted")
            return True
        return False

    def get_sms_list(self, status="ALL"):
        """Get list of SMS messages

        status:
        "REC UNREAD" - Unread messages
        "REC READ" - Read messages
        "STO UNSENT" - Stored unsent messages
        "STO SENT" - Stored sent messages
        "ALL" - All messages
        """
        if not self.connected:
            return []

        # Set text mode
        self.send_at_command("AT+CMGF=1")

        # Get messages
        if status == "ALL":
            response = self.send_at_command("AT+CMGL")
        else:
            response = self.send_at_command(f'AT+CMGL="{status}"')

        if not response or "OK" not in response:
            return []

        messages = []
        lines = response.split('\n')
        i = 0

        while i < len(lines):
            line = lines[i]
            if line.startswith("+CMGL:"):
                # Parse header
                header_match = re.search(r'\+CMGL: (\d+),"([^"]*)",'
                                        r'"([^"]*)",[^,]*,"([^"]*)"', line)
                if header_match:
                    index = header_match.group(1)
                    msg_status = header_match.group(2)
                    sender = header_match.group(3)
                    timestamp = header_match.group(4)

                    # Get message content
                    if i + 1 < len(lines):
                        content = lines[i + 1]

                        # Check if PDU or text mode
                        if any(c for c in content if not (c.isalnum() or c.isspace() or c in '+-,.;:!?')):
                            # Likely PDU data, decode it
                            content = self._decode_pdu_message(content)

                        messages.append({
                            'index': index,
                            'status': msg_status,
                            'sender': sender,
                            'timestamp': timestamp,
                            'content': content
                        })

                        i += 2  # Skip content line
                    else:
                        i += 1
                else:
                    i += 1
            else:
                i += 1

        return messages

    def get_network_info(self):
        """获取网络信息（优先使用缓存）"""
        # 检查缓存
        cached_value = self._get_cached_value('network')
        if cached_value is not None:
            return cached_value

        if not self.connected:
            return None

        # 缓存无效，重新获取网络信息
        try:
            self._update_carrier_info()  # 网络信息在_update_carrier_info中更新
            # 更新缓存
            self._update_cache('network', self.network_type)
            return self.network_type
        except Exception as e:
            print(f"获取网络信息出错: {str(e)}")
            return None

    def get_signal_strength(self):
        """Get signal strength"""
        if not self.connected:
            return "未连接"

        try:
            self._update_signal_strength()
            # 确保返回正确格式的信号强度信息
            if hasattr(self, 'signal_strength') and self.signal_strength:
                if isinstance(self.signal_strength, tuple) and len(self.signal_strength) == 2:
                    # 已经是(signal_text, signal_desc)格式
                    return self.signal_strength
                elif isinstance(self.signal_strength, str):
                    # 如果是字符串，将其作为信号描述返回
                    return (self.signal_strength, "")
                else:
                    # 不支持的格式，转换为字符串
                    return (str(self.signal_strength), "")
            else:
                # 没有信号强度信息
                return ("未知", "信号数据不可用")
        except Exception as e:
            self.log_at_interaction(f"获取信号强度失败: {str(e)}")
            print(f"获取信号强度失败: {str(e)}")
            return ("错误", str(e)[:20])

    def get_call_status(self):
        """Get current call status"""
        if not self.connected:
            return []

        # 记录CLCC命令调用
        static_var_name = "_clcc_call_count"
        if not hasattr(self, static_var_name):
            setattr(self, static_var_name, 0)

        # 增加计数器
        current_count = getattr(self, static_var_name) + 1
        setattr(self, static_var_name, current_count)

        # 记录到日志
        self.log_at_command(True, f"AT+CLCC (第{current_count}次调用)")
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 调用AT+CLCC检查通话状态 (第{current_count}次)")

        try:
            # Send AT+CLCC command to get call status
            response = self.send_at_command("AT+CLCC")

            if not response or "OK" not in response:
                return []

            calls = []
            lines = response.split('\n')

            for line in lines:
                line = line.strip()
                if not line or line == "OK" or not line.startswith("+CLCC:"):
                    continue

                # Parse +CLCC response
                # Format: +CLCC: <id>,<dir>,<stat>,<mode>,<mpty>[,<number>,<type>[,<alpha>]]
                parts = line.replace("+CLCC:", "").strip().split(",")

                if len(parts) < 5:
                    continue

                call = {
                    'id': int(parts[0]),
                    'dir': int(parts[1]),  # 0=MO, 1=MT
                    'stat': int(parts[2]),  # 0=active, 1=held, 2=dialing, 3=alerting, 4=incoming, 5=waiting
                    'mode': int(parts[3]),  # 0=voice, 1=data, 2=fax
                    'mpty': int(parts[4])   # 0=not multiparty, 1=multiparty
                }

                # Check if number is included
                if len(parts) >= 7:
                    # Remove quotes from number
                    number = parts[5].strip('"')
                    call['number'] = number

                    # Check if alpha is included
                    if len(parts) >= 8:
                        alpha = parts[7].strip('"')
                        call['alpha'] = alpha

                calls.append(call)

            # 如果通话状态发生变化，记录日志
            if not hasattr(self, '_last_call_status') or self._last_call_status != calls:
                self._last_call_status = calls
                call_desc = self._format_call_status_for_log(calls)
                self.log_at_command(False, f"CLCC响应解析: {call_desc}")

            return calls
        except Exception as e:
            print(f"Error getting call status: {str(e)}")
            return []

    def _format_call_status_for_log(self, calls):
        """格式化通话状态用于日志记录"""
        if not calls:
            return "无通话"

        status_texts = []
        for call in calls:
            direction = "呼出" if call.get('dir') == 0 else "呼入"

            status_code = call.get('stat', -1)
            if status_code == 0:
                status = "活动"
            elif status_code == 1:
                status = "保持"
            elif status_code == 2:
                status = "拨号中"
            elif status_code == 3:
                status = "振铃中"
            elif status_code == 4:
                status = "来电中"
            elif status_code == 5:
                status = "等待中"
            else:
                status = f"未知({status_code})"

            number = call.get('number', '未知号码')
            status_texts.append(f"{direction}/{status}/{number}")

        return ", ".join(status_texts)

    def get_call_state_text(self):
        """
        获取当前通话状态的文本描述
        """
        calls = self.get_call_status()
        if not calls:
            return "无通话"

        # 获取第一个通话的状态描述
        call = calls[0]
        stat = call.get('stat', -1)
        state_text = self.call_states.get(stat, "未知状态")

        # 添加方向信息
        direction = "呼出" if call.get('dir', 0) == 0 else "呼入"

        # 添加号码信息
        number = call.get('number', '')
        number_text = f", 号码: {number}" if number else ""

        return f"{direction}通话, {state_text}{number_text}"

    def is_call_connected(self):
        """检查通话是否已接通（不仅仅是振铃状态）"""
        # 获取最新通话状态
        calls = self.get_call_status()

        # 如果没有通话，则未接通
        if not calls:
            return False

        # 检查第一个通话是否处于活动状态(stat=0)
        call = calls[0]
        return call.get('stat', -1) == 0

    def _get_cached_value(self, cache_key):
        """获取缓存值，如果有效则返回缓存，否则返回None"""
        cache_item = self.info_cache.get(cache_key)
        if not cache_item:
            return None

        current_time = time.time()
        if cache_item['value'] is not None and (current_time - cache_item['timestamp']) < cache_item['valid_time']:
            return cache_item['value']

        return None

    def _update_cache(self, cache_key, value):
        """更新缓存值"""
        if cache_key in self.info_cache:
            self.info_cache[cache_key]['value'] = value
            self.info_cache[cache_key]['timestamp'] = time.time()

    def _update_phone_number(self):
        """Update phone number information"""
        try:
            response = self.send_at_command("AT+CNUM")
            if not response:
                self.phone_number = "无法获取号码"
                self._update_cache('phone_number', self.phone_number)
                return self.phone_number

            if "+CNUM:" in response:
                match = re.search(r'\+CNUM: "[^"]*","([^"]+)"', response)
                if match:
                    self.phone_number = match.group(1)
                    # 保存到缓存
                    self._update_cache('phone_number', self.phone_number)
                    return self.phone_number
                else:
                    # 如果匹配失败，尝试其他格式
                    match = re.search(r'\+CNUM:.*?"([^"]*)"', response)
                    if match:
                        self.phone_number = match.group(1)
                        # 保存到缓存
                        self._update_cache('phone_number', self.phone_number)
                        return self.phone_number

            # 如果无法获取电话号码，尝试查询SIM卡状态
            sim_response = self.send_at_command("AT+CPIN?")
            if sim_response and "READY" in sim_response:
                # 尝试获取SIM卡IMSI
                sim_info = self.send_at_command("AT+CIMI")
                if sim_info and "OK" in sim_info:
                    self.phone_number = "SIM卡已就绪"
                    # 保存到缓存
                    self._update_cache('phone_number', self.phone_number)
                    return self.phone_number

            # 如果所有尝试都失败
            self.phone_number = "无法获取号码"
            # 保存到缓存
            self._update_cache('phone_number', self.phone_number)
            return self.phone_number
        except Exception as e:
            self.log_at_interaction(f"获取电话号码失败: {str(e)}")
            print(f"获取电话号码失败: {str(e)}")
            self.phone_number = "获取号码出错"
            # 保存到缓存
            self._update_cache('phone_number', self.phone_number)
            return self.phone_number

    def _ensure_pcm_audio_registered(self):
        """确保PCM音频被注册（如果尚未注册）"""
        if not self.pcm_registered:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 注册PCM音频")
            result = self._register_pcm_audio()
            self.pcm_registered = True
            return result
        return True

    def log_at_interaction(self, message):
        """记录AT交互日志（简化的接口，内部使用log_at_command）"""
        self.log_at_command(False, f"[INFO] {message}")
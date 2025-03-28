import serial
import threading
import time
import re
import binascii
import queue
import os
from PyQt5.QtCore import QObject, pyqtSignal, QDateTime, QTimer
from sms_utils import text_to_ucs2, ucs2_to_text, is_chinese_text, format_phone_number

# Import serial.tools.list_ports for port detection
import serial.tools.list_ports

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
        self.at_serial = None
        self.nmea_serial = None
        self.at_port = ""
        self.nmea_port = ""
        self.at_baudrate = 115200
        self.nmea_baudrate = 9600
        self.connected = False
        self.running = False  # Flag to control the read thread
        self.read_thread = None
        self.response_queue = queue.Queue()
        self.lock = threading.Lock()

        # Command cache
        self.command_cache = {}

        # Module information
        self.imei = ""
        self.imsi = ""
        self.model = ""
        self.manufacturer = ""
        self.firmware = ""
        self.phone_number = ""
        self.carrier = ""
        self.network_type = ""
        self.signal_strength = ""

        # Call status
        self.in_call = False
        self.call_connected = False  # 标记通话是否已经接通（区分来电振铃和通话接通）
        self.call_number = ""
        self.call_notification_sent = False  # Flag to track if we've already notified about this call
        self.call_states = {
            0: "正在进行",   # active
            1: "保持",      # hold
            2: "拨号中",    # dialing (MO)
            3: "振铃中",    # alerting (MO)
            4: "来电中",    # incoming (MT)
            5: "等待中"     # waiting (MT)
        }

        # SMS handling
        self.waiting_for_sms_content = False
        self.pending_sms_sender = None
        self.pending_sms_timestamp = None

        # 长短信处理
        self.concat_sms_parts = {}  # 用于存储长短信的各个部分
        self.concat_sms_timeout = 30  # 长短信合并超时时间（秒）

        # 启动定期清理超时长短信的定时器
        self.cleanup_timer = QTimer()
        self.cleanup_timer.timeout.connect(self._cleanup_old_sms_parts)
        self.cleanup_timer.start(10000)  # 每10秒清理一次

        # 添加AT命令日志文件路径
        self.at_log_file = None
        self._setup_at_log_file()

    def _setup_at_log_file(self):
        """设置AT命令日志文件"""
        try:
            # 确保.LTE目录存在
            home_dir = os.path.expanduser("~")
            lte_dir = os.path.join(home_dir, ".LTE")
            if not os.path.exists(lte_dir):
                os.makedirs(lte_dir)

            # 清理旧日志文件（保留最近7天的日志）
            self._cleanup_old_log_files(lte_dir)

            # 创建基于日期的日志文件 - 使用time模块获取当前日期
            today = time.strftime("%Y-%m-%d")
            log_file_path = os.path.join(lte_dir, f"at_commands_{today}.log")

            # 以追加模式打开日志文件
            self.at_log_file = open(log_file_path, "a", encoding="utf-8")
            print(f"AT命令日志文件已创建: {log_file_path}")

            # 记录会话开始 - 使用time模块获取当前时间
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            self.at_log_file.write(f"\n===== LTE管理器会话开始 {timestamp} =====\n")
            self.at_log_file.flush()

            return True
        except Exception as e:
            print(f"创建AT命令日志文件失败: {str(e)}")
            self.at_log_file = None
            return False

    def _cleanup_old_log_files(self, log_dir, max_days=7):
        """清理旧的日志文件，只保留最近max_days天的日志"""
        try:
            # 获取当前时间戳
            current_time = time.time()
            # 计算max_days天前的时间戳
            max_age = current_time - (max_days * 24 * 60 * 60)

            # 遍历日志目录
            for file in os.listdir(log_dir):
                # 只处理AT命令日志文件
                if file.startswith('at_commands_') and file.endswith('.log'):
                    file_path = os.path.join(log_dir, file)
                    # 获取文件修改时间
                    file_time = os.path.getmtime(file_path)
                    # 如果文件修改时间早于max_days天前，则删除
                    if file_time < max_age:
                        os.remove(file_path)
                        print(f"已删除旧日志文件: {file}")
        except Exception as e:
            print(f"清理旧日志文件时出错: {str(e)}")

    def _log_at_interaction(self, command, response=None):
        """记录AT命令交互"""
        try:
            if self.at_log_file:
                # 使用time模块获取时间戳
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}"
                if command is not None:
                    # 只记录发送的命令
                    self.at_log_file.write(f"{timestamp} >>> {command}\n")
                self.at_log_file.flush()
        except Exception as e:
            print(f"记录AT命令时出错: {str(e)}")

    def _log_response(self, command, response):
        """单独记录AT命令的响应，避免重复记录命令"""
        try:
            if self.at_log_file:
                # 使用time模块获取时间戳
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}"
                if response:
                    self.at_log_file.write(f"{timestamp} <<< {response}\n")
                self.at_log_file.flush()
        except Exception as e:
            print(f"记录AT命令响应时出错: {str(e)}")

    def _log_unsolicited(self, response):
        """记录非请求的响应，使用独立的格式"""
        try:
            if self.at_log_file:
                # 使用time模块获取时间戳
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}"
                self.at_log_file.write(f"{timestamp} <UNSOLICITED> {response}\n")
                self.at_log_file.flush()
        except Exception as e:
            print(f"记录非请求响应时出错: {str(e)}")

    def connect(self, port=None, baudrate=115200):
        """Connect to the LTE module"""
        try:
            # Check if already connected
            if self.is_connected():
                print("Already connected")
                return True

            # Initialize command cache
            self.command_cache = {}

            # 重置连接状态
            self.connected = False

            # Validate port
            if not port:
                # Try to auto-detect port
                port = self._auto_detect_port()
                if not port:
                    self.status_changed.emit("Error: No serial port detected")
                    return False

            self.status_changed.emit(f"Connecting to {port}...")
            print(f"尝试连接到端口: {port}, 波特率: {baudrate}")

            # Make sure any previous connection is properly closed
            try:
                if hasattr(self, 'at_serial') and self.at_serial:
                    self.at_serial.close()
                    time.sleep(0.5)  # Increased delay to give OS more time to release the port
                    print(f"已关闭之前的串口连接，等待500ms")
            except Exception as e:
                print(f"Warning: Error closing previous serial connection: {str(e)}")

            # Connect to the serial port
            try:
                print(f"打开串口: {port}")
                self.at_serial = serial.Serial(
                    port=port,
                    baudrate=baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=2.0,  # Increased timeout
                    write_timeout=1.0,
                    xonxoff=False,
                    rtscts=False,
                    dsrdtr=False
                )
                print(f"串口已打开，初始化响应队列")

                # Check if the port is open
                if not self.at_serial.is_open:
                    print(f"错误: 串口未能打开")
                    return False

                # Initialize the response queue
                self.response_queue = queue.Queue()

                # Clear any pending data
                self.at_serial.reset_input_buffer()
                self.at_serial.reset_output_buffer()
                print(f"串口缓冲区已重置")

                # Set the running flag before starting the thread
                self.running = True

                # Start the read thread
                self.read_thread = threading.Thread(target=self._read_thread, daemon=True)
                self.read_thread.start()
                print(f"读取线程已启动")

                # 确保日志文件已创建 (但不重复创建)
                if not self.at_log_file:
                    print("创建AT命令日志文件...")
                    self._setup_at_log_file()

                # Wait for the thread to start reading
                time.sleep(0.2)

                # 直接尝试写入一个空行到串口，测试是否可用
                try:
                    self.at_serial.write(b'\r\n')
                    time.sleep(0.1)
                    print("发送空行成功")
                except Exception as e:
                    print(f"发送空行失败: {str(e)}")

                # Send AT command multiple times to ensure connection
                print(f"发送AT测试命令...")
                response = ""
                for attempt in range(3):
                    try:
                        # 直接使用串口写入，而不是send_at_command
                        if attempt == 0:
                            self.at_serial.write(b'AT\r\n')
                            print("直接写入AT命令")
                            time.sleep(0.2)
                            # 读取响应
                            data = self.at_serial.read(self.at_serial.in_waiting or 100)
                            if data:
                                response = data.decode('utf-8', errors='replace')
                                print(f"直接读取响应: {response}")
                                if "OK" in response:
                                    print("直接通信成功!")
                                    self.connected = True
                                    break
                        else:
                            # 使用正常方法发送命令
                            response = self.send_at_command("AT", timeout=2.0, retries=1)
                            print(f"AT命令尝试 {attempt+1}/3 响应: {response}")
                            if "OK" in response or self.connected:
                                break
                    except Exception as e:
                        print(f"AT命令尝试 {attempt+1} 失败: {str(e)}")
                    time.sleep(0.5)

                if self.connected:  # 使用self.connected标志，该标志在send_at_command中设置
                    self.status_changed.emit(f"Connected to {port}")
                    print(f"成功连接到 {port}")
                    self.port = port
                    self.baudrate = baudrate

                    # Configure the module
                    print(f"开始配置模块...")
                    self._configure_module()

                    return True
                else:
                    self.running = False  # Stop the read thread
                    self.status_changed.emit("Error: Module not responding")
                    print(f"错误: 模块未响应, 响应内容: {response}")
                    if hasattr(self, 'at_serial') and self.at_serial and self.at_serial.is_open:
                        self.at_serial.close()
                    return False

            except Exception as e:
                self.running = False  # Make sure thread stops if an error occurs
                self.status_changed.emit(f"Error connecting: {str(e)}")
                print(f"连接错误: {str(e)}")
                return False

        except Exception as e:
            self.status_changed.emit(f"Error in connect: {str(e)}")
            print(f"连接过程中发生错误: {str(e)}")
            return False

    def disconnect(self):
        """Disconnect from the LTE module"""
        try:
            if self.is_connected():
                # Stop the read thread
                self.running = False
                if self.read_thread and self.read_thread.is_alive():
                    try:
                        self.read_thread.join(1.0)  # Wait for thread to finish, timeout after 1 second
                    except Exception as e:
                        print(f"Warning: Error waiting for read thread: {str(e)}")

                # Log disconnection
                if self.at_log_file:
                    # 使用time模块获取时间戳
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    self.at_log_file.write(f"\n===== LTE管理器会话结束 {timestamp} =====\n\n")
                    self.at_log_file.flush()
                    self.at_log_file.close()
                    print(f"AT命令日志文件已关闭: {self.at_log_file.name}")
                    self.at_log_file = None

                # Close the serial port
                if hasattr(self, 'at_serial') and self.at_serial:
                    self.at_serial.close()

                self.connected = False
                self.status_changed.emit("Disconnected")
                return True
            return False
        except Exception as e:
            self.status_changed.emit(f"Error disconnecting: {str(e)}")
            return False

    def is_connected(self):
        """Check if connected to the LTE module"""
        return self.connected

    def send_at_command(self, command, timeout=2.0, retries=2, use_cache=False):
        """发送AT命令并等待响应"""
        # 如果使用缓存并且命令有缓存
        if use_cache and command in self.command_cache:
            cache_time, cache_result = self.command_cache[command]
            # 检查缓存是否过期 (500ms)
            if time.time() - cache_time < 0.5:
                # 记录使用了缓存
                print(f"使用缓存结果: {command}")
                self._log_at_interaction(command, f"[CACHED] {cache_result}")
                return cache_result

        # 初始化重试计数器
        retry_count = 0

        print(f"发送AT命令: {command}, 超时: {timeout}秒, 最大重试次数: {retries}")

        while retry_count < retries:
            try:
                # 检查串口是否已打开（不检查self.connected标志）
                if not hasattr(self, 'at_serial') or not self.at_serial or not self.at_serial.is_open:
                    error_msg = "ERROR: Serial port not open"
                    print(f"命令发送失败: {error_msg}")
                    self._log_at_interaction(command, error_msg)
                    return error_msg

                # 清空输入缓冲区
                try:
                    self.at_serial.reset_input_buffer()
                    print(f"输入缓冲区已清空")
                except Exception as e:
                    print(f"清空输入缓冲区失败: {str(e)}")

                # 记录发送的AT命令
                self._log_at_interaction(command, None)

                # 发送命令
                cmd = command + "\r\n"
                bytes_written = self.at_serial.write(cmd.encode())
                print(f"发送命令: {command}，已写入 {bytes_written} 字节")

                # 确保命令已发送
                self.at_serial.flush()
                print(f"命令已刷新到设备")

                # 等待并读取响应
                response = self._read_serial(timeout)
                print(f"收到命令响应: {response}")

                # 检查响应
                if "ERROR" in response:
                    # 记录错误响应
                    self._log_response(command, response)

                    # 区分不同类型的错误
                    # 1. 查询命令返回ERROR - 这通常表示命令不支持，不需要重试
                    if command.endswith("=?") or command.endswith("?"):
                        print(f"命令不支持: {command} -> {response}")
                        # 将响应缓存并返回，不重试
                        self.command_cache[command] = (time.time(), response)
                        return response

                    # 2. CME ERROR或CMS ERROR - 这是带错误代码的特定错误，说明命令被识别但执行失败
                    if "+CME ERROR:" in response or "+CMS ERROR:" in response:
                        print(f"命令执行错误: {command} -> {response}")
                        # 如果是特定错误代码，不需要重试
                        self.command_cache[command] = (time.time(), response)
                        return response

                    # 3. 普通ERROR - 可能需要重试的通信问题
                    print(f"命令执行错误: {command} -> {response}, 重试 {retry_count+1}/{retries}")
                    retry_count += 1
                    time.sleep(0.5)  # 出错时延迟后重试
                    continue
                else:
                    # 命令成功，记录并缓存响应
                    self._log_response(command, response)
                    print(f"命令执行成功: {command}")

                    # 如果这是AT命令并且响应包含OK，则设置connected标志
                    if command == "AT" and "OK" in response:
                        self.connected = True
                        print("连接状态已设置为已连接")

                    # 缓存响应结果
                    self.command_cache[command] = (time.time(), response)
                    return response

            except Exception as e:
                print(f"命令 {command} 执行时出错: {str(e)}")
                error_msg = f"ERROR: {str(e)}"
                retry_count += 1
                time.sleep(0.5)  # 出错时延迟后重试
                continue

        # 达到最大重试次数后返回错误
        print(f"命令 {command} 已达到最大重试次数 {retries}，放弃执行")
        return f"ERROR: Max retries ({retries}) exceeded"

    def _read_thread(self):
        """Thread function to continuously read from serial port"""
        buffer = ""

        print("Serial read thread started")
        while self.running:
            if not self.at_serial or not self.at_serial.is_open:
                time.sleep(0.1)
                continue

            try:
                # Read data from serial port
                if self.at_serial.in_waiting > 0:
                    data = self.at_serial.read(self.at_serial.in_waiting)
                    if data:
                        text = data.decode('utf-8', errors='replace')
                        buffer += text

                        # Process complete lines
                        while '\r\n' in buffer:
                            line, buffer = buffer.split('\r\n', 1)
                            line = line.strip()

                            if not line:
                                continue

                            # Process unsolicited responses
                            self._process_unsolicited(line)

                            # Add to response queue for command responses
                            self.response_queue.put(line)
                else:
                    # No data available, sleep briefly to reduce CPU usage
                    time.sleep(0.01)
            except Exception as e:
                print(f"Serial read error: {str(e)}")
                time.sleep(0.1)

        print("Serial read thread stopped")

    def _read_serial(self, timeout=5.0):
        """读取串口响应，直到超时或收到完整响应"""
        start_time = time.time()
        response = []
        command_echo_received = False

        print(f"等待AT命令响应，最大超时时间: {timeout}秒")

        # 等待响应，直到超时
        while time.time() - start_time < timeout:
            try:
                # 使用queue.get()获取响应行
                line = self.response_queue.get(timeout=0.2)
                print(f"收到响应行: {line}")

                # 检查是否是命令回显（某些模块会回显命令）
                if not command_echo_received and line.startswith("AT"):
                    command_echo_received = True
                    # 可以选择跳过命令回显行，不添加到响应中
                    continue

                response.append(line)

                # 检查是否为完整响应的各种情况
                # 1. 标准的OK结束
                if line == "OK":
                    print("收到完整响应标识，结束等待")
                    break

                # 2. ERROR结束
                elif line == "ERROR" or "+CMS ERROR:" in line or "+CME ERROR:" in line:
                    print("收到完整响应标识，结束等待")
                    break

                # 3. 没有正常的结束标记，但对于某些特殊响应需要特殊处理
                # 例如，对于COPS?命令如果收到了+COPS:行，可能不需要等待OK
                elif "+COPS:" in line and "OK" not in response:
                    # 继续等待OK，不直接退出
                    pass

                # 4. 对于返回消息体的命令，等待足够长的时间确保收到完整消息
                # 特别是短信内容、通话状态等
                elif "+CMGR:" in line or "+CMGL:" in line or "+CLCC:" in line:
                    # 继续等待，这些命令通常有多行响应
                    pass

            except queue.Empty:
                # 如果队列为空，检查是否已有足够的响应内容
                # 如果已经收到至少一行，且最后一行包含某些特定结束标记，也可以结束等待
                if response:
                    # 检查最后一行是否是某种结束标记
                    last_line = response[-1]
                    if last_line in ["OK", "ERROR"] or "+CMS ERROR:" in last_line or "+CME ERROR:" in last_line:
                        print("在队列等待期间确认已收到完整响应")
                        break

                    # 对于某些命令，没有明确结束标记，但收到特定响应后短时间内没有更多响应，也可视为完成
                    # 例如，AT+CSQ后只有一行+CSQ:响应，但没有OK
                    if ("+CSQ:" in last_line or "+CREG:" in last_line or "+CGREG:" in last_line) and \
                       time.time() - start_time > 1.0:  # 等待至少1秒以确保无更多响应
                        print("已收到关键响应行且无后续内容，视为完成")
                        break

        # 如果没有收到任何响应，返回超时错误
        if not response:
            print("读取超时，未收到任何响应")
            return "ERROR: Read timeout"

        # 合并所有响应行并返回
        full_response = "\n".join(response)
        return full_response

    def _process_unsolicited(self, line):
        """处理非请求响应"""
        # 不把AT命令及其响应作为unsolicited response处理
        if line.startswith("AT") or line == "OK" or line == "ERROR" or line.startswith("+CSQ") or line.startswith("+CREG") or line.startswith("+CGREG"):
            # 跳过可能的命令回显或常见查询响应
            return

        # 对于真正的非请求通知，记录并处理
        self._log_unsolicited(line)

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
            try:
                # 提取发送者号码和时间戳，用于后续匹配和合并短信
                sender_match = re.search(r'\+CMT: "([^"]*)",[^,]*,"([^"]*)"', line)
                if sender_match:
                    sender = sender_match.group(1)
                    timestamp = sender_match.group(2)

                    # 解码发送者号码（如果是UCS2编码）
                    if sender.startswith("00"):
                        try:
                            sender = ucs2_to_text(sender)
                        except Exception as e:
                            print(f"解码发送者号码出错: {str(e)}")

                    # 保存短信头部信息，用于后续处理
                    self.pending_sms_sender = sender
                    self.pending_sms_timestamp = timestamp

                    # 生成SMS ID用于匹配多段短信
                    sms_id = f"{sender}_{timestamp[:10]}"

                    # 检查是否已有相同ID的短信正在处理中
                    is_continuation = False
                    if sms_id in self.concat_sms_parts:
                        # 这是已有短信的后续部分
                        is_continuation = True
                        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 检测到后续短信部分: {sms_id}")
                        self.status_changed.emit(f"检测到后续短信部分，来自 {sender}")

                    # 标记等待内容行
                    self.waiting_for_sms_content = True

                    # 保存当前短信ID，用于内容行处理
                    self.current_sms_id = sms_id
                    self.current_is_continuation = is_continuation

                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 收到短信头部，发送者: {sender}, 时间: {timestamp}, ID: {sms_id}")
                else:
                    # 无法解析发送者和时间，使用默认处理方式
                    if self._is_concatenated_sms(line):
                        self._handle_concatenated_sms(line)
                    else:
                        self._handle_regular_sms(line)
            except Exception as e:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 处理短信头部出错: {str(e)}")
                # 错误时使用旧方法尝试处理
                if self._is_concatenated_sms(line):
                    self._handle_concatenated_sms(line)
                else:
                    self._handle_regular_sms(line)

        elif self.waiting_for_sms_content:
            # 这是短信内容行
            self.waiting_for_sms_content = False
            message = line

            # 检查是否保存了短信ID信息
            sms_id = getattr(self, 'current_sms_id', None)
            is_continuation = getattr(self, 'current_is_continuation', False)

            # 清除临时属性
            if hasattr(self, 'current_sms_id'):
                del self.current_sms_id
            if hasattr(self, 'current_is_continuation'):
                del self.current_is_continuation

            try:
                # 检查是否是UCS2编码
                if all(c in "0123456789ABCDEFabcdef" for c in line.replace(" ", "")):
                    # 尝试解码UCS2内容
                    decoded_content = None
                    try:
                        decoded_content = ucs2_to_text(line)
                        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - UCS2内容解码成功: {decoded_content[:50]}...")
                    except Exception as decode_error:
                        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - UCS2解码错误: {str(decode_error)}")

                        # 尝试替代解码方法
                        try:
                            hex_bytes = binascii.unhexlify(line.replace(" ", ""))
                            decoded_content = hex_bytes.decode('utf-16-be', errors='replace')
                            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 替代解码成功: {decoded_content[:50]}...")
                        except Exception as alt_error:
                            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 替代解码也失败: {str(alt_error)}")

                    # 如果是长短信的一部分（根据特定特征判断）
                    is_long_message_part = False
                    if "62117ED94F6053D14E86957F6587672C" in line:
                        is_long_message_part = True
                        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 检测到长短信特征")
                    elif decoded_content and "https://" in decoded_content:
                        is_long_message_part = True
                        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 检测到URL内容，视为长短信")

                    # 处理长短信
                    if is_long_message_part or is_continuation:
                        # 处理为长短信的一部分
                        self._process_long_message_part(self.pending_sms_sender, self.pending_sms_timestamp, line, decoded_content, sms_id)
                    else:
                        # 常规短信处理，直接发送解码后的内容
                        message = decoded_content if decoded_content else line
                        self.sms_received.emit(
                            self.pending_sms_sender,
                            self.pending_sms_timestamp,
                            message
                        )
                        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 发送常规短信到UI")
                else:
                    # 非UCS2编码，直接发送
                    self.sms_received.emit(
                        self.pending_sms_sender,
                        self.pending_sms_timestamp,
                        message
                    )
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 发送纯文本短信到UI")
            except Exception as e:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 处理短信内容时出错: {str(e)}")
                # 出错时尝试直接发送原始内容
                self.sms_received.emit(
                    self.pending_sms_sender,
                    self.pending_sms_timestamp,
                    f"[解码错误] {message[:100]}..."
                )

            # 清除待处理短信数据
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

    def _process_long_message_part(self, sender, timestamp, content, decoded_content, sms_id):
        """处理长短信的一部分，支持追加入库功能"""
        try:
            # 移除空格
            content = content.replace(" ", "")

            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 处理长短信部分，ID: {sms_id}, 内容长度: {len(content)}")

            # 特殊格式检测
            is_special_format = "62117ED94F6053D14E86957F6587672C" in content

            # 如果没有解码后的内容，尝试解码
            if not decoded_content:
                try:
                    decoded_content = ucs2_to_text(content)
                except Exception as e:
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 解码UCS2内容出错: {str(e)}")
                    try:
                        # 尝试替代解码方法
                        hex_bytes = binascii.unhexlify(content)
                        decoded_content = hex_bytes.decode('utf-16-be', errors='replace')
                    except Exception as alt_e:
                        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 替代解码方法失败: {str(alt_e)}")
                        decoded_content = f"[无法解码] {content[:50]}..."

            # 提取URL (如果有)
            url = None
            if is_special_format and decoded_content:
                # 尝试从特殊格式中提取URL
                try:
                    url_match = re.search(r':(https?://[^\s]+)', decoded_content)
                    if url_match:
                        url = url_match.group(1)
                        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 从特殊格式中提取URL: {url}")
                except Exception as url_e:
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 从特殊格式提取URL失败: {str(url_e)}")

            # 如果没有提取到URL但有解码后的内容，尝试从普通文本中提取
            if not url and decoded_content:
                url_match = re.search(r'(https?://[^\s]+)', decoded_content)
                if url_match:
                    url = url_match.group(1)
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 从文本中提取URL: {url}")

            # 初始化或更新长短信记录
            if sms_id not in self.concat_sms_parts:
                # 提取消息前缀（如果有）
                prefix = "消息"
                if decoded_content and ":" in decoded_content:
                    prefix_match = re.match(r'^([^:]+):', decoded_content)
                    if prefix_match:
                        prefix = prefix_match.group(1).strip()

                # 创建新的长短信记录
                self.concat_sms_parts[sms_id] = {
                    'sender': sender,
                    'timestamp': timestamp,
                    'parts': [],
                    'urls': [],
                    'received_time': time.time(),
                    'prefix': prefix,
                    'is_processed': False  # 标记是否已处理
                }
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 创建新的长短信记录: {sms_id}")

            # 更新长短信记录
            sms_record = self.concat_sms_parts[sms_id]

            # 添加解码后的内容到parts
            if decoded_content and decoded_content not in sms_record['parts']:
                sms_record['parts'].append(decoded_content)
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 添加第 {len(sms_record['parts'])} 部分到长短信记录")

            # 添加URL到urls列表（如果有且不重复）
            if url and url not in sms_record['urls']:
                sms_record['urls'].append(url)
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 添加URL到长短信记录: {url}")

            # 更新接收时间
            sms_record['received_time'] = time.time()

            # 使用定时器，延迟合并处理长短信（等待其他部分到达）
            # 如果是分条短信，设置较短的延迟；如果是长短信，设置较长的延迟
            delay = 1.5 if len(sms_record['parts']) > 1 else 3.0
            threading.Timer(delay, lambda: self._check_and_merge_sms(sms_id)).start()

            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 设置 {delay} 秒后合并长短信")

        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 处理长短信部分时出错: {str(e)}")
            # 出错时尝试直接发送当前部分
            try:
                message = decoded_content if decoded_content else content
                self.sms_received.emit(sender, timestamp, f"[长短信处理错误] {message[:100]}...")
            except Exception as send_e:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 发送错误消息失败: {str(send_e)}")

    def _check_and_merge_sms(self, sms_id):
        """检查并合并长短信，支持追加内容到已处理的长短信"""
        if sms_id not in self.concat_sms_parts:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 无法找到长短信记录: {sms_id}")
            return

        sms_info = self.concat_sms_parts[sms_id]

        # 检查是否已处理过
        if sms_info.get('is_processed', False):
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 长短信 {sms_id} 已处理过，检查是否有新部分")

            # 如果已处理过但有新内容（最近3秒内收到的），则追加处理
            current_time = time.time()
            if current_time - sms_info['received_time'] < 3:
                # 有新部分，继续等待更多部分
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 检测到新内容，延迟后再次尝试合并")
                threading.Timer(2.0, lambda: self._check_and_merge_sms(sms_id)).start()
                return

            # 有新内容需要追加，重新合并并发送更新
            merged_content = self._merge_sms_parts(sms_id)
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 发送更新的长短信内容: {merged_content[:50]}...")

            # 发送信号，表示这是更新的内容
            self.status_changed.emit(f"更新长短信内容，来自 {sms_info['sender']}")
            self.sms_received.emit(
                sms_info['sender'],
                sms_info['timestamp'],
                merged_content
            )

            # 记录处理状态
            sms_info['is_processed'] = True

            # 更新上次处理时间
            sms_info['last_processed'] = current_time
            return

        # 检查是否有有效部分
        if not sms_info.get('parts', []):
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 长短信 {sms_id} 没有有效部分，跳过合并")
            return

        # 检查是否收到后续部分的超时（通常1-3秒内应该收到所有部分）
        current_time = time.time()
        time_since_last_part = current_time - sms_info['received_time']

        # 如果最近2秒内收到新部分，继续等待
        if time_since_last_part < 2.0:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 最近才收到新部分 ({time_since_last_part:.1f}秒前)，继续等待")
            return

        # 超过等待时间，进行合并处理
        merged_content = self._merge_sms_parts(sms_id)

        # 发送完整消息
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 发送合并后的长短信: {merged_content[:50]}...")
        self.sms_received.emit(
            sms_info['sender'],
            sms_info['timestamp'],
            merged_content
        )

        # 标记为已处理
        sms_info['is_processed'] = True
        sms_info['last_processed'] = current_time

        # 记录日志
        self.status_changed.emit(f"已接收完整长短信，来自 {sms_info['sender']}")

        # 不删除记录，而是保留用于后续追加处理
        # 长短信记录将在清理定时任务中处理

    def _is_concatenated_sms(self, header_line):
        """检查是否为长短信"""
        # 检查是否符合长短信格式
        try:
            # 长短信格式通常包含更多逗号分隔的字段
            parts = header_line.split(',')
            # 普通短信头部通常有3个字段，长短信可能有更多
            if len(parts) >= 6:
                return True
            return False
        except Exception as e:
            print(f"检查长短信格式出错: {str(e)}")
            return False

    def _handle_regular_sms(self, header_line):
        """处理普通短信"""
        try:
            # 解析SMS头部，格式通常为: +CMT: "sender","","timestamp"
            header_match = re.search(r'\+CMT: "([^"]*)",[^,]*,"([^"]*)"', header_line)
            if header_match:
                sender = header_match.group(1)
                timestamp = header_match.group(2)

                # 检查发送者是否为UCS2格式（通常以00开头）
                if sender.startswith("00"):
                    try:
                        sender = ucs2_to_text(sender)
                    except Exception as e:
                        print(f"解码发送者号码失败: {str(e)}")
                        # 解码失败时保留原始格式

                # 保存发送者和时间信息，等待下一行接收内容
                self.pending_sms_sender = sender
                self.pending_sms_timestamp = timestamp
                self.waiting_for_sms_content = True

                # 发送状态更新
                self.status_changed.emit(f"收到来自 {sender} 的短信")
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 收到来自 {sender} 的短信")
            else:
                # 如果头部格式不匹配，使用默认值
                self.pending_sms_sender = "未知号码"
                self.pending_sms_timestamp = time.strftime("%y/%m/%d,%H:%M:%S")
                self.waiting_for_sms_content = True

                # 发送状态更新
                self.status_changed.emit("收到短信（无法识别发送者）")
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 收到短信（头部格式异常：{header_line}）")
        except Exception as e:
            print(f"处理短信头部出错: {str(e)}")
            # 出错时使用默认值
            self.pending_sms_sender = "错误"
            self.pending_sms_timestamp = time.strftime("%y/%m/%d,%H:%M:%S")
            self.waiting_for_sms_content = True
            self.status_changed.emit(f"解析短信头部时出错: {str(e)}")

    def _handle_concatenated_sms(self, header_line):
        """处理长短信的头部信息"""
        try:
            # 解析长短信头部，格式可能更复杂
            parts = header_line.split(',')
            if len(parts) < 3:
                # 格式不符合预期，作为普通短信处理
                self._handle_regular_sms(header_line)
                return

            # 尝试提取发送者和时间戳
            sender_part = parts[0].replace('+CMT: ', '').strip('"')
            # 时间戳位置可能在第3个位置
            timestamp_part = parts[2].strip('"') if len(parts) > 2 else time.strftime("%y/%m/%d,%H:%M:%S")

            # 检查发送者是否为UCS2格式
            if sender_part.startswith("00"):
                try:
                    sender_part = ucs2_to_text(sender_part)
                except Exception as e:
                    print(f"解码长短信发送者出错: {str(e)}")
                    # 解码失败时保留原始格式

            # 保存信息等待后续处理
            self.pending_sms_sender = sender_part
            self.pending_sms_timestamp = timestamp_part
            self.waiting_for_sms_content = True

            # 发送状态更新
            self.status_changed.emit(f"收到来自 {sender_part} 的长短信部分")
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 收到来自 {sender_part} 的长短信部分")
        except Exception as e:
            # 出错时尝试作为普通短信处理
            print(f"处理长短信头部出错: {str(e)}")
            self._handle_regular_sms(header_line)

    def _process_concatenated_sms_part(self, sender, timestamp, content):
        """处理长短信的内容部分"""
        try:
            # 移除空格
            content = content.replace(" ", "")

            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 处理长短信内容: {content[:50]}...")

            # 检查是否为特定格式的长短信
            is_special_format = "62117ED94F6053D14E86957F6587672C" in content

            # 尝试解码内容
            try:
                decoded_content = ucs2_to_text(content)
                print(f"解码内容: {decoded_content[:50]}...")
            except Exception as e:
                print(f"UCS2解码错误: {str(e)}")
                # 如果解码失败，尝试不同方法或直接发送原始内容
                try:
                    # 尝试直接从十六进制转换为字节，然后解码
                    hex_bytes = binascii.unhexlify(content)
                    decoded_content = hex_bytes.decode('utf-16-be', errors='replace')
                    print(f"替代解码成功: {decoded_content[:50]}...")
                except Exception as alt_e:
                    print(f"替代解码失败: {str(alt_e)}")
                    # 如果所有解码方法都失败，发送原始内容
                    self.sms_received.emit(
                        sender,
                        timestamp,
                        f"[无法解码的消息] {content[:50]}..."
                    )
                    return

            # 特殊格式处理 - 提取URL
            url = None
            if is_special_format:
                # 尝试从特殊格式中提取URL
                parts = content.split("003A", 1)  # 003A是冒号的UCS2编码
                if len(parts) > 1:
                    url_content = "003A" + parts[1]  # 加回冒号
                    try:
                        url_text = ucs2_to_text(url_content)
                        url_match = re.search(r':(https?://[^\s]+)', url_text)
                        if url_match:
                            url = url_match.group(1)
                            print(f"提取URL: {url}")
                    except Exception as url_e:
                        print(f"URL提取错误: {str(url_e)}")

            # 如果没有找到URL，尝试从普通文本中提取
            if not url:
                url_match = re.search(r'(https?://[^\s]+)', decoded_content)
                if url_match:
                    url = url_match.group(1)
                    print(f"从普通文本提取URL: {url}")

            # 创建或更新长短信记录
            sms_id = f"{sender}_{timestamp[:10]}"

            if sms_id not in self.concat_sms_parts:
                prefix = "消息"
                if ":" in decoded_content:
                    prefix = decoded_content.split(":", 1)[0].strip()

                self.concat_sms_parts[sms_id] = {
                    'sender': sender,
                    'timestamp': timestamp,
                    'parts': [],
                    'urls': [],
                    'received_time': time.time(),
                    'prefix': prefix
                }

            # 添加这部分到长短信记录
            if url and url not in self.concat_sms_parts[sms_id]['urls']:
                self.concat_sms_parts[sms_id]['urls'].append(url)

            if decoded_content not in self.concat_sms_parts[sms_id]['parts']:
                self.concat_sms_parts[sms_id]['parts'].append(decoded_content)

            # 更新接收时间
            self.concat_sms_parts[sms_id]['received_time'] = time.time()

            # 使用定时器，3秒后尝试合并长短信
            threading.Timer(3.0, lambda: self._check_and_merge_sms(sms_id)).start()

            print(f"已保存长短信部分，将在3秒后尝试合并")

        except Exception as e:
            print(f"处理长短信内容部分出错: {str(e)}")
            # 出错时直接发送解码后的内容
            try:
                decoded = ucs2_to_text(content) if all(c in "0123456789ABCDEFabcdef" for c in content) else content
                self.sms_received.emit(
                    sender,
                    timestamp,
                    decoded
                )
            except Exception as final_e:
                print(f"最终解码尝试失败: {str(final_e)}")
                self.sms_received.emit(
                    sender,
                    timestamp,
                    f"[消息解码失败] {content[:50]}..."
                )

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
            # 如果有URL，优先使用URL格式返回
            if prefix:
                merged_content = f"{prefix}:\n" + "\n".join(urls)
            else:
                merged_content = "\n".join(urls)
        else:
            # 如果没有URL，合并所有文本部分
            merged_content = "\n".join(sms_info['parts'])

        return merged_content

    def _cleanup_old_sms_parts(self):
        """清理超时的长短信部分"""
        try:
            current_time = time.time()
            sms_ids_to_remove = []

            for sms_id, sms_info in self.concat_sms_parts.items():
                # 检查是否已处理且超过保留时间（10分钟）
                if sms_info.get('is_processed', False) and current_time - sms_info.get('last_processed', 0) > 600:
                    sms_ids_to_remove.append(sms_id)
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 清理已处理的长短信: {sms_id}")
                # 检查未处理但已超时的长短信（30秒）
                elif not sms_info.get('is_processed', False) and current_time - sms_info.get('received_time', 0) > 30:
                    # 如果有内容但未处理（可能是因为只收到部分内容），尝试合并发送
                    if sms_info.get('parts', []):
                        try:
                            # 合并可用部分并发送
                            merged_content = self._merge_sms_parts(sms_id)
                            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 发送超时但未处理的长短信: {merged_content[:50]}...")
                            self.sms_received.emit(
                                sms_info['sender'],
                                sms_info['timestamp'],
                                f"[部分内容] {merged_content}"
                            )
                        except Exception as e:
                            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 处理超时长短信时出错: {str(e)}")

                    sms_ids_to_remove.append(sms_id)
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 清理超时未处理的长短信: {sms_id}")

            # 移除标记的记录
            for sms_id in sms_ids_to_remove:
                del self.concat_sms_parts[sms_id]
                self.status_changed.emit(f"清理长短信记录: {sms_id}")

            # 打印当前缓存状态
            if self.concat_sms_parts:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 当前有 {len(self.concat_sms_parts)} 条长短信记录在缓存中")
        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 清理长短信部分时出错: {str(e)}")

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
        try:
            # 移除空格
            content = content.replace(" ", "")

            # 检查是否为UCS2编码
            if not all(c in "0123456789ABCDEFabcdef" for c in content):
                return False

            # 检查内容长度是否足够
            if len(content) < 10:
                return False

            # 检查是否包含特定模式
            # 1. 检查特定前缀，这是已知的长短信特征
            if content.startswith("62117ED94F6053D14E86957F6587672C"):
                print(f"检测到长短信特定前缀: 62117ED94F6053D14E86957F6587672C")
                return True

            # 2. 检查内容是否包含URL的UCS2编码
            # https的UCS2编码前缀: 00680074007400700073
            if "00680074007400700073" in content:
                print(f"检测到UCS2编码的HTTPS URL")
                return True

            # 3. 检查内容长度是否超过标准短信长度限制
            # UCS2编码的短信最多支持70个字符，即140个字节，对应280个十六进制字符
            if len(content) > 280:
                print(f"内容长度({len(content)})超过标准短信限制")
                return True

            # 4. 尝试解码并检查是否包含特定内容标记
            try:
                decoded = ucs2_to_text(content)
                # 检查解码后内容是否包含URL
                if re.search(r'https?://', decoded):
                    print(f"检测到包含URL的内容")
                    return True
            except:
                pass

            return False
        except Exception as e:
            print(f"检查长短信内容部分时出错: {str(e)}")
            return False

    def _initialize_module(self):
        """初始化LTE模块

        设置基本配置，包括来电提醒、短信格式等
        并获取设备信息
        """
        try:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 初始化LTE模块")

            # 每个命令间添加30毫秒延迟

            # 检查并注销PCM音频，确保通话音频正确处理
            self._stop_pcm_audio()
            time.sleep(0.03)  # 30毫秒延迟

            # 检查当前CLIP状态
            clip_status = self.send_at_command("AT+CLIP?")
            time.sleep(0.03)  # 30毫秒延迟

            # 只有当CLIP不是1时才启用
            if not clip_status or "+CLIP: 1" not in clip_status:
                # 启用来电显示功能 (呼叫线路标识显示)
                clip_response = self.send_at_command("AT+CLIP=1")
                if "OK" in clip_response:
                    self.status_changed.emit("来电显示功能已启用")
                else:
                    self.status_changed.emit("启用来电显示功能失败")
                time.sleep(0.03)  # 30毫秒延迟

            # 检查当前SMS格式状态
            sms_format = self.send_at_command("AT+CMGF?")
            time.sleep(0.03)  # 30毫秒延迟

            # 只有当不是文本模式时才设置
            if not sms_format or "+CMGF: 1" not in sms_format:
                # 设置短信为文本模式
                cmgf_response = self.send_at_command("AT+CMGF=1")
                if "OK" in cmgf_response:
                    self.status_changed.emit("短信文本模式已启用")
                else:
                    self.status_changed.emit("启用短信文本模式失败")
                time.sleep(0.03)  # 30毫秒延迟

            # 检查新消息指示配置
            cnmi_status = self.send_at_command("AT+CNMI?")
            time.sleep(0.03)  # 30毫秒延迟

            # 只有当不是2,2,0,0,0时才设置
            if not cnmi_status or "+CNMI: 2,2,0,0,0" not in cnmi_status:
                # 设置新消息指示
                cnmi_response = self.send_at_command("AT+CNMI=2,2,0,0,0")
                if "OK" in cnmi_response:
                    self.status_changed.emit("短信通知已启用")
                else:
                    self.status_changed.emit("启用短信通知失败")
                time.sleep(0.03)  # 30毫秒延迟

            # 获取模块信息 (厂商、型号、IMEI等)
            self._get_module_info()
            time.sleep(0.03)  # 30毫秒延迟

            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - LTE模块初始化完成")

        except Exception as e:
            self.status_changed.emit(f"初始化模块失败: {str(e)}")
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 初始化模块失败: {str(e)}")

    def _get_module_info(self):
        """获取模块信息（初始化时调用一次）"""
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 获取设备基本信息")

        # 记录上次更新时间
        self.last_info_update = time.time()

        # 获取制造商信息
        response = self.send_at_command("AT+CGMI")
        if response and "OK" in response:
            # 移除命令回显和OK响应，只保留实际内容
            lines = [line.strip() for line in response.split('\n') if line.strip()]
            # 过滤掉AT命令回显和OK响应
            content_lines = [line for line in lines if line != "OK" and not line.startswith("AT+")]
            self.manufacturer = '\n'.join(content_lines).strip()

        # 获取模块型号
        response = self.send_at_command("AT+CGMM")
        if response and "OK" in response:
            # 移除命令回显和OK响应，只保留实际内容
            lines = [line.strip() for line in response.split('\n') if line.strip()]
            # 过滤掉AT命令回显和OK响应
            content_lines = [line for line in lines if line != "OK" and not line.startswith("AT+")]
            self.model = '\n'.join(content_lines).strip()

        # 获取IMEI号码
        response = self.send_at_command("AT+CGSN")
        if response and "OK" in response:
            # 移除命令回显和OK响应，只保留实际内容
            lines = [line.strip() for line in response.split('\n') if line.strip()]
            # 过滤掉AT命令回显和OK响应
            content_lines = [line for line in lines if line != "OK" and not line.startswith("AT+")]
            self.imei = '\n'.join(content_lines).strip()

        # 获取固件版本
        response = self.send_at_command("AT+CGMR")
        if response and "OK" in response:
            match = re.search(r'\+CGMR: (.+)', response)
            if match:
                self.firmware = match.group(1)
            else:
                # 移除命令回显和OK响应，只保留实际内容
                lines = [line.strip() for line in response.split('\n') if line.strip()]
                # 过滤掉AT命令回显和OK响应
                content_lines = [line for line in lines if line != "OK" and not line.startswith("AT+")]
                self.firmware = '\n'.join(content_lines).strip()

        # 获取电话号码、运营商和信号强度信息
        self._update_phone_number()
        self._update_carrier_info()
        self._update_signal_strength()

        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 设备基本信息获取完成")

    def _update_phone_number(self):
        """更新电话号码信息（缓存30分钟）"""
        # 添加缓存检查，减少AT命令交互
        current_time = time.time()
        if hasattr(self, 'last_phone_update') and current_time - self.last_phone_update < 1800:  # 30分钟缓存
            # 使用缓存的值
            return self.phone_number

        response = self.send_at_command("AT+CNUM")
        if response and "+CNUM:" in response:
            match = re.search(r'\+CNUM: "[^"]*","([^"]+)"', response)
            if match:
                self.phone_number = match.group(1)
                self.last_phone_update = current_time  # 记录更新时间
                return self.phone_number

        # 如果没有找到号码但有之前的值，保留之前的值
        return self.phone_number

    def _update_carrier_info(self):
        """更新运营商信息（缓存10分钟）"""
        # 添加缓存检查，减少AT命令交互
        current_time = time.time()
        if hasattr(self, 'last_carrier_update') and current_time - self.last_carrier_update < 600:  # 10分钟缓存
            # 使用缓存的值
            return (self.carrier, self.network_type)

        # 更新运营商信息
        carrier_updated = False
        response = self.send_at_command("AT+COPS?")
        if response and "+COPS:" in response:
            match = re.search(r'\+COPS: \d+,\d+,"([^"]+)"', response)
            if match:
                self.carrier = match.group(1)
                carrier_updated = True

        # 更新网络类型
        network_updated = False
        response = self.send_at_command("AT+CPSI?")
        if response and "+CPSI:" in response:
            # 移除命令回显，只保留+CPSI:部分
            match = re.search(r'\+CPSI:(.+)', response)
            if match:
                parts = match.group(1).split(',')
                if len(parts) > 1:
                    self.network_type = parts[0].strip()
                    network_updated = True
            else:
                parts = response.split(',')
                if len(parts) > 1:
                    self.network_type = parts[0].replace("+CPSI:", "").strip()
                    network_updated = True

        # 如果有更新，记录更新时间
        if carrier_updated or network_updated:
            self.last_carrier_update = current_time

        return (self.carrier, self.network_type)

    def _update_signal_strength(self):
        """更新信号强度信息（无缓存，需要实时监控）"""
        response = self.send_at_command("AT+CSQ")
        if response and "+CSQ:" in response:
            match = re.search(r'\+CSQ: (\d+),', response)
            if match:
                rssi = int(match.group(1))
                if rssi == 99:
                    self.signal_strength = "Unknown"
                else:
                    # Convert to dBm (-113 to -51 dBm)
                    dbm = -113 + (2 * rssi)
                    self.signal_strength = f"{dbm} dBm ({rssi}/31)"

        return self.signal_strength

    def get_carrier_info(self):
        """获取运营商信息（使用缓存机制）"""
        if not self.connected:
            return None

        # 使用缓存的值，不每次都发送AT命令
        if hasattr(self, 'carrier') and self.carrier:
            # 只有在超过缓存时间时才更新
            current_time = time.time()
            if not hasattr(self, 'last_carrier_update') or current_time - self.last_carrier_update >= 600:  # 10分钟缓存
                self._update_carrier_info()
        else:
            # 第一次请求，直接更新
            self._update_carrier_info()

        return self.carrier

    def get_phone_number(self):
        """获取电话号码（使用缓存机制）"""
        if not self.connected:
            return None

        # 使用缓存的值，不每次都发送AT命令
        if hasattr(self, 'phone_number') and self.phone_number:
            # 只有在超过缓存时间时才更新
            current_time = time.time()
            if not hasattr(self, 'last_phone_update') or current_time - self.last_phone_update >= 1800:  # 30分钟缓存
                self._update_phone_number()
        else:
            # 第一次请求，直接更新
            self._update_phone_number()

        return self.phone_number

    def get_network_info(self):
        """获取网络信息（使用缓存机制）"""
        if not self.connected:
            return None

        # 使用缓存的值，不每次都发送AT命令
        if hasattr(self, 'network_type') and self.network_type:
            # 只有在超过缓存时间时才更新（与运营商信息共享缓存时间）
            current_time = time.time()
            if not hasattr(self, 'last_carrier_update') or current_time - self.last_carrier_update >= 600:  # 10分钟缓存
                self._update_carrier_info()  # 这里会同时更新network_type
        else:
            # 第一次请求，直接更新
            self._update_carrier_info()

        return self.network_type

    def get_signal_strength(self):
        """获取信号强度（实时更新）"""
        if not self.connected:
            return None

        # 信号强度需要实时更新，其他信息则使用缓存
        self._update_signal_strength()
        return self.signal_strength

    def get_module_info(self):
        """获取模块信息（使用缓存机制）"""
        if not self.connected:
            return {}

        # 检查是否需要刷新模块信息（默认每小时更新一次）
        current_time = time.time()
        if not hasattr(self, 'last_info_update') or current_time - self.last_info_update >= 3600:  # 1小时缓存
            self._get_module_info()
        else:
            # 仅更新可能变化的信息：信号强度
            self._update_signal_strength()

            # 适当更新运营商信息（如果缓存过期）
            if not hasattr(self, 'last_carrier_update') or current_time - self.last_carrier_update >= 600:
                self._update_carrier_info()

        return {
            'manufacturer': self.manufacturer,
            'model': self.model,
            'imei': self.imei,
            'firmware': self.firmware,
            'phone_number': self.phone_number,
            'carrier': self.carrier,
            'network_type': self.network_type,
            'signal_strength': self.signal_strength
        }

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
                with self.lock:
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
            with self.lock:
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
            with self.lock:
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
        """接听来电"""
        try:
            # 首先检查是否真的有来电
            calls = self.get_call_status()
            has_incoming_call = False

            for call in calls:
                if call.get('stat') == 4 and call.get('dir') == 1:  # 来电中(MT)
                    has_incoming_call = True
                    break

            if not has_incoming_call:
                self.status_changed.emit("当前没有待接听的来电")
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 尝试接听来电失败：当前无待接听来电")
                return False

            # 停止所有铃声
            self._stop_all_ringtones()
            time.sleep(0.03)  # 30毫秒延迟

            # 确保PCM音频已注册
            self._ensure_pcm_audio_registered()
            time.sleep(0.03)  # 30毫秒延迟

            # 发送接听命令
            response = self.send_at_command("ATA")

            if "OK" in response or "CONNECT" in response:
                # 标记已接通
                self.call_connected = True
                self.in_call = True
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 通话已接通")
                return True
            else:
                # 即使命令返回失败，仍检查通话是否已建立（有时模块会接通但返回错误）
                time.sleep(0.5)  # 等待一小段时间让通话建立

                # 再次检查通话状态，确认是否已接通
                calls_after = self.get_call_status()
                call_established = False

                for call in calls_after:
                    if call.get('stat') in [0, 1] and call.get('dir') == 1:  # 活动或保持状态的呼入通话
                        call_established = True
                        self.in_call = True
                        self.call_connected = True
                        break

                if call_established:
                    self.status_changed.emit("通话已接通")
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 通话接听成功")
                    return True
                else:
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 接听失败: {response}")
                    return False

        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 接听电话时出错: {str(e)}")
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

            # 检查响应中是否包含OK
            if "OK" in response:
                self.in_call = False
                self.call_connected = False
                self.status_changed.emit("通话结束")
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 通话已结束")

                # 通话结束后，立即取消PCM音频注册
                self._ensure_pcm_audio_unregistered()
                return True
            else:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 挂断通话失败，响应: {response}")
                return False
        else:
            # 检查第一个通话的状态
            call = calls[0]
            stat = call.get('stat', -1)

            if stat == 4:  # 来电中(MT)
                # 来电振铃状态，使用 AT+CHUP 命令挂断
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 使用AT+CHUP挂断未接通的来电")
                response = self.send_at_command("AT+CHUP")

                # 对于AT+CHUP命令，检查特殊成功标志
                if "OK" in response or "NO CARRIER" in response or "VOICE CALL: END" in response:
                    self.in_call = False
                    self.call_connected = False
                    self.status_changed.emit("来电已拒绝")
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 来电已拒绝")

                    # 通话结束后，立即取消PCM音频注册
                    self._ensure_pcm_audio_unregistered()
                    return True

                # 检查响应中是否包含+CLCC行，且状态为6(终止)
                # 如: +CLCC: 1,1,6,0,0,"18571797477",129,""
                if "+CLCC:" in response and ",6," in response:
                    self.in_call = False
                    self.call_connected = False
                    self.status_changed.emit("来电已拒绝")
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 通过CLCC状态确认来电已拒绝")

                    # 通话结束后，立即取消PCM音频注册
                    self._ensure_pcm_audio_unregistered()
                    return True

                # 发送CHUP后立即检查通话状态
                time.sleep(0.5)  # 短暂延迟，等待模块处理
                after_calls = self.get_call_status()
                if not after_calls:
                    self.in_call = False
                    self.call_connected = False
                    self.status_changed.emit("来电已拒绝")
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 二次确认来电已拒绝")

                    # 通话结束后，立即取消PCM音频注册
                    self._ensure_pcm_audio_unregistered()
                    return True

                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 拒绝来电失败，响应: {response}")
                return False
            else:
                # 其他状态使用 ATH 命令挂断
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 使用ATH挂断通话，状态: {self.call_states.get(stat, '未知')}")
                response = self.send_at_command("ATH")

                # 检查响应中是否包含OK或其他成功标志
                if "OK" in response or "NO CARRIER" in response or "VOICE CALL: END" in response:
                    self.in_call = False
                    self.call_connected = False
                    self.status_changed.emit("通话结束")
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 通话已结束")

                    # 通话结束后，立即取消PCM音频注册
                    self._ensure_pcm_audio_unregistered()
                    return True

                # 发送ATH后立即检查通话状态
                time.sleep(0.5)  # 短暂延迟，等待模块处理
                after_calls = self.get_call_status()
                if not after_calls:
                    self.in_call = False
                    self.call_connected = False
                    self.status_changed.emit("通话结束")
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 二次确认通话已结束")

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

        # Clear any pending responses
        while not self.response_queue.empty():
            self.response_queue.get()

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

                # Send message command
                cmd = f'AT+CMGS="{formatted_number}"'
                self.at_serial.write((cmd + '\r').encode())
                time.sleep(0.5)  # Wait for > prompt

                # Send message content and Ctrl+Z to end
                self.at_serial.write(message.encode() + b'\x1A')
                self.status_changed.emit("Sending ASCII message...")

            # Wait for response with longer timeout
            start_time = time.time()
            response = []

            while time.time() - start_time < 15.0:  # Increased timeout to 15 seconds
                try:
                    line = self.response_queue.get(timeout=0.5)
                    response.append(line)
                    self.status_changed.emit(f"SMS response: {line}")

                    if "+CMGS:" in line:
                        self.status_changed.emit(f"SMS sent to {formatted_number}")
                        return True
                    elif "ERROR" in line or "+CMS ERROR:" in line:
                        self.status_changed.emit(f"SMS error: {line}")
                        return False
                except queue.Empty:
                    continue

            # If we get here, we timed out waiting for a response
            self.status_changed.emit(f"SMS send timeout. Last response: {response[-1] if response else 'None'}")
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

    def get_call_status(self):
        """
        获取当前所有通话状态

        返回值为通话列表，每个通话包含：
        - id: 通话ID
        - dir: 方向（0=MO, 1=MT）
        - stat: 状态（0=活动, 1=保持, 2=拨号, 3=振铃, 4=来电, 5=等待）
        - mode: 模式（0=语音）
        - mpty: 多方通话（0=非多方通话）
        - number: 电话号码
        """
        if not self.is_connected():
            return []

        # 检查是否有缓存且在短时间内（500毫秒内）
        current_time = time.time()
        if hasattr(self, 'last_call_status_check') and hasattr(self, 'cached_call_status'):
            if current_time - self.last_call_status_check < 0.5:  # 500毫秒内直接使用缓存结果
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 使用缓存的通话状态 ({int((current_time - self.last_call_status_check) * 1000)}ms)")
                return self.cached_call_status

        try:
            # 记录本次查询时间
            self.last_call_status_check = current_time

            # 发送AT+CLCC查询通话状态命令
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 发送AT+CLCC查询通话状态")
            response = self.send_at_command("AT+CLCC")
            calls = []

            # 检查响应是否有效
            if not response:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - AT+CLCC无响应")
                self.cached_call_status = []
                return []

            # 检查是否有错误响应
            if "ERROR" in response:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - AT+CLCC返回错误: {response}")
                self.cached_call_status = []
                return []

            # 检查响应中是否只有OK（无通话）
            if "+CLCC:" not in response:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 无活动通话")
                self.cached_call_status = []
                return []

            # 解析响应
            lines = response.strip().split('\n')
            for line in lines:
                line = line.strip()
                if not line.startswith('+CLCC:'):
                    continue

                try:
                    # +CLCC: <id>,<dir>,<stat>,<mode>,<mpty>[,<number>,<type>[,<alpha>]]
                    parts = line[7:].strip().split(',')

                    # 验证是否有足够的字段
                    if len(parts) < 5:
                        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - CLCC响应格式不完整: {line}")
                        continue

                    # 尝试解析通话信息
                    call = {
                        'id': int(parts[0].strip()),
                        'dir': int(parts[1].strip()),
                        'stat': int(parts[2].strip()),
                        'mode': int(parts[3].strip()),
                        'mpty': int(parts[4].strip())
                    }

                    # 判断是否有电话号码字段
                    if len(parts) > 5:
                        number = parts[5].strip()
                        if number.startswith('"') and number.endswith('"'):
                            number = number[1:-1]  # 移除引号
                        call['number'] = number

                    # 记录该通话状态的文本描述（用于日志）
                    state_text = self.call_states.get(call['stat'], "未知状态")
                    direction = "呼出" if call['dir'] == 0 else "呼入"
                    number_info = f", 号码: {call.get('number', '未知')}" if 'number' in call else ""
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 检测到{direction}通话: {state_text}{number_info}")

                    calls.append(call)
                except Exception as parse_error:
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 解析CLCC响应行错误: {str(parse_error)}, 行: {line}")
                    continue

            # 保存缓存结果
            self.cached_call_status = calls

            # 输出通话状态摘要
            if calls:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 当前有 {len(calls)} 个活动通话")
            else:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 没有活动通话")

            # 通话状态变化时的特殊处理
            if calls and not self.in_call:
                # 之前不在通话，现在有通话 - 进入通话状态
                self.in_call = True
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 检测到新通话，共 {len(calls)} 个通话")

                # 获取最高优先级的通话状态
                highest_priority_call = None
                for call in calls:
                    # 优先级: 活动 > 来电 > 拨号 > 其他
                    if highest_priority_call is None or call['stat'] < highest_priority_call['stat']:
                        highest_priority_call = call

                # 记录主叫号码或被叫号码
                if highest_priority_call and 'number' in highest_priority_call:
                    self.call_number = highest_priority_call['number']
                else:
                    self.call_number = ""

                # 如果是状态为0的通话（活动通话），则更新通话已接通标志和时间
                if highest_priority_call and highest_priority_call['stat'] == 0:
                    self.call_connected = True
                    self.call_connect_time = time.time()
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 通话已接通，记录开始时间")

            elif not calls and self.in_call:
                # 之前在通话，现在没有通话 - 退出通话状态
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 所有通话已结束")
                self.in_call = False

                # 备份通话状态，然后清除
                was_connected = self.call_connected
                self.call_connected = False

                # 如果有记录通话号码，生成结束通知
                if self.call_number:
                    duration = "Missed"  # 默认为未接

                    # 根据通话是否曾经接通决定如何计算时长
                    if was_connected:
                        # 计算通话时长（秒）
                        if hasattr(self, 'call_connect_time'):
                            call_duration = round(time.time() - self.call_connect_time)
                            duration = str(call_duration)
                            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 通话结束，持续时间: {call_duration}秒")

                    # 发出通话结束信号
                    self.call_ended.emit(duration)
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 通话结束，号码: {self.call_number}，持续时间: {duration}")

                    # 清除通话号码记录
                    self.call_number = ""

                    # 清除连接时间记录
                    if hasattr(self, 'call_connect_time'):
                        del self.call_connect_time

            return calls
        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 获取通话状态出错: {str(e)}")
            # 出错时返回空列表，并缓存空列表
            self.cached_call_status = []
            return []

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

    def _ensure_pcm_audio_registered(self):
        """确保PCM音频已注册，用于通话音频处理"""
        try:
            # 检查当前PCM音频注册状态
            pcm_status = self.send_at_command("AT+CPCMREG?")
            time.sleep(0.03)  # 30毫秒延迟

            # 如果PCM没有注册，则进行注册
            if not pcm_status or "+CPCMREG: 1" not in pcm_status:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 注册PCM音频")
                reg_response = self.send_at_command("AT+CPCMREG=1")

                if "OK" in reg_response:
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注册成功")
                    self.pcm_audio_status.emit(True)

                    # 设置PCM音频格式
                    time.sleep(0.03)  # 30毫秒延迟
                    frm_response = self.send_at_command("AT+CPCMFRM=1")
                    if "OK" in frm_response:
                        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频格式设置成功")
                    else:
                        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频格式设置失败")
                else:
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注册失败")
                    self.pcm_audio_status.emit(False)
            else:
                # 已经注册，发出信号
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频已注册")
                self.pcm_audio_status.emit(True)

            return True
        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 确保PCM音频注册出错: {str(e)}")
            self.pcm_audio_status.emit(False)
            return False

    def _stop_pcm_audio(self):
        """停止PCM音频注册"""
        try:
            # 检查当前PCM音频注册状态
            pcm_status = self.send_at_command("AT+CPCMREG?")

            # 如果已注册，则注销
            if pcm_status and "+CPCMREG: 1" in pcm_status:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 发送PCM音频注销命令")
                response = self.send_at_command("AT+CPCMREG=0")
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注销命令已发送")
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注销响应: {response}")

                if "OK" in response:
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注销成功")
                else:
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频注销状态未知")
            else:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频未注册或读取状态失败")

            # 无论如何，都发送PCM音频已停止信号
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 发送PCM音频停止信号")
            self.pcm_audio_status.emit(False)

            return True
        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 停止PCM音频注册出错: {str(e)}")
            return False

    def _stop_all_ringtones(self):
        """停止所有铃声和声音"""
        try:
            # 这里不应该发送AT+CLIP=0，那会禁用来电显示
            # 而是通知上层应用停止铃声播放
            self.status_changed.emit("STOP_RINGTONES")

            # 清理任何可能的铃声缓存
            if hasattr(self, 'ringtone_thread') and self.ringtone_thread:
                try:
                    self.ringtone_thread.stop()
                    self.ringtone_thread = None
                except:
                    pass

            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 停止铃声信号已发送")
            return True
        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 停止铃声出错: {str(e)}")
            return False

    def _auto_detect_port(self):
        """尝试自动检测LTE模块连接的串口"""
        try:
            # 获取所有可用串口
            available_ports = [port.device for port in serial.tools.list_ports.comports()]
            print(f"检测到可用串口: {available_ports}")

            if not available_ports:
                self.status_changed.emit("未检测到任何串口设备")
                print("未检测到任何串口设备")
                return None

            # 如果只有一个串口，直接返回
            if len(available_ports) == 1:
                print(f"只有一个串口可用，直接使用: {available_ports[0]}")
                return available_ports[0]

            # 如果有多个串口，尝试连接每个串口并发送AT命令
            print("检测到多个串口，尝试查找LTE模块...")
            for port in available_ports:
                try:
                    print(f"尝试在串口 {port} 上查找LTE模块...")
                    # 尝试打开串口
                    test_serial = serial.Serial(
                        port=port,
                        baudrate=115200,
                        bytesize=serial.EIGHTBITS,
                        parity=serial.PARITY_NONE,
                        stopbits=serial.STOPBITS_ONE,
                        timeout=1,
                        write_timeout=1
                    )

                    # 清空缓冲区
                    test_serial.reset_input_buffer()
                    test_serial.reset_output_buffer()

                    # 发送AT命令并等待响应
                    print(f"向 {port} 发送AT命令")
                    test_serial.write(b'AT\r\n')
                    time.sleep(0.5)

                    # 读取响应
                    response = test_serial.read(test_serial.in_waiting or 100).decode('utf-8', errors='replace')
                    print(f"从 {port} 收到响应: {response}")

                    # 关闭测试连接
                    test_serial.close()

                    # 检查响应是否包含OK
                    if 'OK' in response:
                        self.status_changed.emit(f"自动检测到LTE模块连接在 {port}")
                        print(f"在 {port} 上找到LTE模块")
                        return port
                except Exception as e:
                    print(f"测试 {port} 时出错: {str(e)}")
                    try:
                        # 确保串口已关闭
                        if 'test_serial' in locals() and test_serial.is_open:
                            test_serial.close()
                    except:
                        pass
                    continue

            # 如果没有找到匹配的串口，返回COM6作为默认（如果存在）
            if 'COM6' in available_ports:
                self.status_changed.emit(f"未能确定LTE模块连接的串口，尝试使用COM6")
                print(f"未能确定LTE模块连接的串口，尝试使用COM6")
                return 'COM6'

            # 否则返回第一个可用串口
            self.status_changed.emit(f"未能确定LTE模块连接的串口，使用第一个可用串口 {available_ports[0]}")
            print(f"未能确定LTE模块连接的串口，使用第一个可用串口 {available_ports[0]}")
            return available_ports[0]
        except Exception as e:
            self.status_changed.emit(f"串口自动检测出错: {str(e)}")
            return None

    def _configure_module(self):
        """配置LTE模块的初始设置"""
        try:
            # 日志记录配置开始
            self.status_changed.emit("初始化LTE模块")

            # 禁用回显 - 可选，取决于模块和应用需求
            # self.send_at_command("ATE0")

            # 设置SMS文本模式
            self.send_at_command("AT+CMGF=1")

            # 设置SMS字符集
            self.send_at_command('AT+CSCS="UCS2"')

            # 设置来电显示
            self.send_at_command("AT+CLIP=1")

            # 设置新消息指示
            self.send_at_command('AT+CNMI=2,2,0,0,0')

            # 查询是否有PCM音频注册
            pcm_status = self.send_at_command("AT+ECPCMREG?")
            if "+ECPCMREG: 1" in pcm_status:
                # PCM已注册，先取消注册
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频已注册，取消注册")
                self._unregister_pcm_audio()
            else:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频未注册或读取状态失败")
                # 确保PCM音频处于未注册状态
                self.pcm_audio_status.emit(False)

            # 获取基本信息
            self._update_device_info()

            self.status_changed.emit("LTE模块初始化完成")
            return True
        except Exception as e:
            self.status_changed.emit(f"LTE模块配置失败: {str(e)}")
            return False

    def _update_device_info(self):
        """获取设备基本信息"""
        try:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 获取设备基本信息")

            # 获取IMEI
            response = self.send_at_command("AT+GSN")
            if response and "ERROR" not in response:
                self.imei = response.strip().split("\n")[0]

            # 获取制造商
            response = self.send_at_command("AT+GMI")
            if response and "ERROR" not in response:
                self.manufacturer = response.strip().split("\n")[0]

            # 获取型号
            response = self.send_at_command("AT+GMM")
            if response and "ERROR" not in response:
                self.model = response.strip().split("\n")[0]

            # 获取固件版本
            response = self.send_at_command("AT+GMR")
            if response and "ERROR" not in response:
                self.firmware = response.strip().split("\n")[0]

            # 获取电话号码
            response = self.send_at_command("AT+CNUM")
            if response and "+CNUM:" in response:
                match = re.search(r'\+CNUM: "([^"]*)",("?[^"]*"?),(\d+)', response)
                if match:
                    self.phone_number = match.group(2).strip('"')
                    print(f"电话号码: {self.phone_number}")

            # 获取运营商信息
            response = self.send_at_command("AT+COPS?")
            if response and "+COPS:" in response:
                match = re.search(r'\+COPS: (\d+),(\d+),"([^"]*)"', response)
                if match:
                    self.carrier = match.group(3)
                    print(f"运营商: {self.carrier}")

                    # 检查网络类型值，可能是第4个项目
                    if len(match.groups()) >= 4:
                        # 值映射: 0=GSM, 2=UTRAN, 7=LTE, 13=NR
                        net_type_map = {
                            '0': '2G (GSM)',
                            '2': '3G (UMTS)',
                            '7': '4G (LTE)',
                            '13': '5G (NR)'
                        }
                        net_type = match.group(4) if len(match.groups()) >= 4 else '7'  # 默认LTE
                        self.network_type = net_type_map.get(net_type, f'Unknown ({net_type})')
                    else:
                        # 从response中提取网络类型
                        self._update_network_type()
            else:
                # 如果COPS查询失败，尝试单独更新网络类型
                self._update_network_type()

            # 获取信号强度
            response = self.send_at_command("AT+CSQ")
            if response and "+CSQ:" in response:
                match = re.search(r'\+CSQ: (\d+),(\d+)', response)
                if match:
                    rssi = int(match.group(1))
                    # 转换RSSI为信号格数和dBm值
                    if rssi == 99:
                        self.signal_strength = "无信号"
                    else:
                        # RSSI值0-31，对应-113dBm到-51dBm
                        dbm = -113 + (2 * rssi)
                        # 信号格数（0-4格）
                        if rssi >= 16:  # >= -81dBm
                            bars = 4
                        elif rssi >= 12:  # >= -89dBm
                            bars = 3
                        elif rssi >= 8:  # >= -97dBm
                            bars = 2
                        elif rssi >= 4:  # >= -105dBm
                            bars = 1
                        else:
                            bars = 0

                        self.signal_strength = f"{bars}格 ({dbm}dBm)"
                        print(f"信号强度: {self.signal_strength}")

            # 发送初始化完成的信号
            self.status_changed.emit(f"设备信息已更新: {self.model} {self.carrier}")
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 设备基本信息获取完成")
            return True
        except Exception as e:
            self.status_changed.emit(f"获取设备信息失败: {str(e)}")
            return False

    def _update_network_type(self):
        """单独更新网络类型信息"""
        try:
            # 尝试使用AT+CEREG?命令获取LTE网络注册状态
            cereg_response = self.send_at_command("AT+CEREG?")
            if "CEREG: " in cereg_response:
                # 检查是否有网络注册
                match = re.search(r'\+CEREG: \d+,(\d+)', cereg_response)
                if match and match.group(1) in ['1', '5']:  # 1=已注册，本地网络; 5=已注册，漫游
                    self.network_type = "4G (LTE)"
                    return

            # 尝试使用AT+CREG?命令获取GSM/UMTS网络注册状态
            creg_response = self.send_at_command("AT+CREG?")
            if "CREG: " in creg_response:
                match = re.search(r'\+CREG: \d+,(\d+)', creg_response)
                if match and match.group(1) in ['1', '5']:
                    # 进一步检查是2G还是3G
                    cgreg_response = self.send_at_command("AT+CGREG?")
                    if "CGREG: " in cgreg_response and re.search(r'\+CGREG: \d+,[15]', cgreg_response):
                        self.network_type = "3G (UMTS)"
                    else:
                        self.network_type = "2G (GSM)"
                    return

            # 如果以上都失败，使用默认值
            self.network_type = "未知"
        except Exception as e:
            print(f"更新网络类型失败: {str(e)}")
            self.network_type = "更新失败"
import serial
import threading
import time
import re
import binascii
import queue
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
        self.at_serial = None
        self.nmea_serial = None
        self.at_port = ""
        self.nmea_port = ""
        self.at_baudrate = 115200
        self.nmea_baudrate = 9600
        self.connected = False
        self.running = False
        self.read_thread = None
        self.response_queue = queue.Queue()
        self.lock = threading.Lock()

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

    def connect(self, at_port, at_baudrate=115200, nmea_port="", nmea_baudrate=9600):
        """Connect to the LTE module"""
        try:
            self.at_port = at_port
            self.at_baudrate = at_baudrate
            self.nmea_port = nmea_port
            self.nmea_baudrate = nmea_baudrate

            # Connect to AT port
            self.at_serial = serial.Serial(
                port=at_port,
                baudrate=at_baudrate,
                timeout=1
            )

            # Connect to NMEA port if provided and not "None"
            if nmea_port and nmea_port.lower() != "none":
                try:
                    self.nmea_serial = serial.Serial(
                        port=nmea_port,
                        baudrate=nmea_baudrate,
                        timeout=1
                    )
                    self.status_changed.emit(f"Connected to NMEA port {nmea_port}")
                except Exception as e:
                    self.status_changed.emit(f"NMEA port connection failed: {str(e)}")
                    # Continue even if NMEA connection fails
                    self.nmea_serial = None
            else:
                self.nmea_serial = None
                self.status_changed.emit("NMEA port not specified, skipping")

            self.connected = True
            self.running = True

            # Start read thread
            self.read_thread = threading.Thread(target=self._read_serial)
            self.read_thread.daemon = True
            self.read_thread.start()

            # Initialize module
            self._initialize_module()

            self.status_changed.emit("Connected to LTE module")
            return True
        except Exception as e:
            self.status_changed.emit(f"Connection error: {str(e)}")
            self.connected = False
            return False

    def disconnect(self):
        """Disconnect from the LTE module"""
        if not self.connected:
            return

        self.running = False
        if self.read_thread and self.read_thread.is_alive():
            self.read_thread.join(1)

        if self.at_serial:
            try:
                self.at_serial.close()
            except:
                pass
            self.at_serial = None

        if self.nmea_serial:
            try:
                self.nmea_serial.close()
            except:
                pass
            self.nmea_serial = None

        self.connected = False
        self.status_changed.emit("Disconnected from LTE module")

        # 停止清理定时器
        if self.cleanup_timer.isActive():
            self.cleanup_timer.stop()

    def is_connected(self):
        """Check if connected to the LTE module"""
        return self.connected

    def send_at_command(self, command, timeout=5.0, expect_response=True):
        """Send AT command to the module and wait for response"""
        if not self.connected or not self.at_serial:
            return None

        with self.lock:
            # Clear any pending responses
            while not self.response_queue.empty():
                self.response_queue.get()

            # Send command
            cmd = command.strip()
            if not cmd.endswith('\r'):
                cmd += '\r'

            self.at_serial.write(cmd.encode())

            if not expect_response:
                return None

            # Wait for response
            start_time = time.time()
            response = []
            command_echo_received = False

            while time.time() - start_time < timeout:
                try:
                    line = self.response_queue.get(timeout=0.5)

                    # 跳过命令回显行
                    if not command_echo_received and line.strip() == command.strip():
                        command_echo_received = True
                        continue

                    response.append(line)

                    # Check if response is complete (ends with OK or ERROR)
                    if line.strip() in ["OK", "ERROR"] or "ERROR" in line:
                        break
                except queue.Empty:
                    continue

            return '\n'.join(response)

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

                        # Process unsolicited responses
                        self._process_unsolicited(line)

                        # Add to response queue for command responses
                        self.response_queue.put(line)
            except Exception as e:
                print(f"Serial read error: {str(e)}")
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
        # Check if module is responsive
        response = self.send_at_command("AT")
        if "OK" not in response:
            self.status_changed.emit("Module not responding")
            return False

        # 确保PCM音频在初始状态下是未注册的
        self._unregister_pcm_audio()

        # 设置PCM音频格式为8KHz采样率（默认值，模块重启后恢复）
        # 如果需要16KHz采样率，请设置AT+CPCMFRM=1
        self.send_at_command("AT+CPCMFRM=0")
        self.status_changed.emit("PCM audio format set to 8KHz sampling rate")

        # 启用来电显示功能 (呼叫线路标识显示)
        clip_response = self.send_at_command("AT+CLIP=1")
        if "OK" in clip_response:
            self.status_changed.emit("Caller ID display enabled")
        else:
            self.status_changed.emit("Failed to enable caller ID display")

        # Set SMS text mode
        self.send_at_command("AT+CMGF=1")

        # Set SMS notification mode (direct content delivery)
        self.send_at_command("AT+CNMI=2,2,0,0,0")

        # Get module information
        self._get_module_info()

        return True

    def _get_module_info(self):
        """Get module information"""
        # Get manufacturer
        response = self.send_at_command("AT+CGMI")
        if response and "OK" in response:
            # 移除命令回显和OK响应，只保留实际内容
            lines = [line.strip() for line in response.split('\n') if line.strip()]
            # 过滤掉AT命令回显和OK响应
            content_lines = [line for line in lines if line != "OK" and not line.startswith("AT+")]
            self.manufacturer = '\n'.join(content_lines).strip()

        # Get model
        response = self.send_at_command("AT+CGMM")
        if response and "OK" in response:
            # 移除命令回显和OK响应，只保留实际内容
            lines = [line.strip() for line in response.split('\n') if line.strip()]
            # 过滤掉AT命令回显和OK响应
            content_lines = [line for line in lines if line != "OK" and not line.startswith("AT+")]
            self.model = '\n'.join(content_lines).strip()

        # Get IMEI
        response = self.send_at_command("AT+CGSN")
        if response and "OK" in response:
            # 移除命令回显和OK响应，只保留实际内容
            lines = [line.strip() for line in response.split('\n') if line.strip()]
            # 过滤掉AT命令回显和OK响应
            content_lines = [line for line in lines if line != "OK" and not line.startswith("AT+")]
            self.imei = '\n'.join(content_lines).strip()

        # Get firmware version
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

        # Get phone number
        self._update_phone_number()

        # Get carrier info
        self._update_carrier_info()

        # Get signal strength
        self._update_signal_strength()

    def _update_phone_number(self):
        """Update phone number information"""
        response = self.send_at_command("AT+CNUM")
        if response and "+CNUM:" in response:
            match = re.search(r'\+CNUM: "[^"]*","([^"]+)"', response)
            if match:
                self.phone_number = match.group(1)

    def _update_carrier_info(self):
        """Update carrier information"""
        response = self.send_at_command("AT+COPS?")
        if response and "+COPS:" in response:
            match = re.search(r'\+COPS: \d+,\d+,"([^"]+)"', response)
            if match:
                self.carrier = match.group(1)

        # Get network type
        response = self.send_at_command("AT+CPSI?")
        if response and "+CPSI:" in response:
            # 移除命令回显，只保留+CPSI:部分
            match = re.search(r'\+CPSI:(.+)', response)
            if match:
                parts = match.group(1).split(',')
                if len(parts) > 1:
                    self.network_type = parts[0].strip()
            else:
                parts = response.split(',')
                if len(parts) > 1:
                    self.network_type = parts[0].replace("+CPSI:", "").strip()

    def _update_signal_strength(self):
        """Update signal strength information"""
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
        if not self.connected:
            return False

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

            # 接听电话（先接听，再注册PCM音频）
            response = self.send_at_command("ATA")

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

            if call_established or "OK" in response:
                self.in_call = True
                self.status_changed.emit("通话已接通")
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 通话接听成功")

                # 接听成功后，预约注册PCM音频
                # 延迟注册以确保通话已经完全建立
                # 使用threading.Timer替代QTimer，避免线程问题
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 延迟200ms后注册PCM音频")
                threading.Timer(0.2, self._register_pcm_audio).start()

                return True
            else:
                self.status_changed.emit("接听来电失败")
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 接听来电失败，响应: {response}")
                return False
        except Exception as e:
            self.status_changed.emit(f"接听来电错误: {str(e)}")
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 接听来电错误: {str(e)}")
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

    def get_carrier_info(self):
        """Get carrier information"""
        if not self.connected:
            return None

        self._update_carrier_info()
        return self.carrier

    def get_phone_number(self):
        """Get phone number"""
        if not self.connected:
            return None

        self._update_phone_number()
        return self.phone_number

    def get_network_info(self):
        """Get network information"""
        if not self.connected:
            return None

        self._update_carrier_info()
        return self.network_type

    def get_signal_strength(self):
        """Get signal strength"""
        if not self.connected:
            return None

        self._update_signal_strength()
        return self.signal_strength

    def get_module_info(self):
        """Get module information"""
        if not self.connected:
            return {}

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

    def get_call_status(self):
        """
        使用AT+CLCC命令获取当前通话状态

        返回一个字典列表，每个字典包含一个通话的信息：
        {
            'id': 通话ID,
            'dir': 方向(0=MO呼出, 1=MT呼入),
            'stat': 状态(0=活动, 1=保持, 2=拨号中, 3=振铃中, 4=来电中, 5=等待中),
            'mode': 模式(0=语音, 1=数据, 2=传真, 9=未知),
            'mpty': 是否多方通话,
            'number': 电话号码,
            'type': 号码类型,
            'alpha': 电话簿中的名称(如果有)
        }

        如果没有活动通话，返回空列表
        """
        if not self.connected:
            return []

        response = self.send_at_command("AT+CLCC")
        if "ERROR" in response:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - AT+CLCC命令执行失败")
            return []

        calls = []
        for line in response.split('\n'):
            if "+CLCC:" in line:
                # 解析+CLCC行
                # 格式: +CLCC: <id>,<dir>,<stat>,<mode>,<mpty>[,<number>,<type>[,<alpha>]]
                try:
                    parts = line.strip().replace("+CLCC:", "").split(',')
                    call = {}

                    # 提取基本字段
                    call['id'] = int(parts[0])
                    call['dir'] = int(parts[1])  # 0=MO呼出, 1=MT呼入
                    call['stat'] = int(parts[2]) # 0=活动, 1=保持, 2=拨号中, 3=振铃中, 4=来电中, 5=等待中
                    call['mode'] = int(parts[3]) # 0=语音, 1=数据, 2=传真, 9=未知
                    call['mpty'] = int(parts[4]) # 0=非多方通话, 1=多方通话

                    # 如果包含电话号码
                    if len(parts) > 5:
                        # 号码通常带引号
                        call['number'] = parts[5].strip('"')
                        call['type'] = int(parts[6]) if len(parts) > 6 else 0

                    # 如果包含联系人名称
                    if len(parts) > 7:
                        call['alpha'] = parts[7].strip('"') if parts[7] else ""

                    calls.append(call)

                    # 更新内部状态以反映当前通话状态
                    if call['stat'] in [0, 1, 2, 3]:  # 活动、保持、拨号中、振铃中
                        self.in_call = True
                        self.call_connected = (call['stat'] == 0)  # 仅当通话状态为"活动"时设置为已接通
                    elif call['stat'] == 4:  # 来电中
                        self.in_call = True
                        self.call_connected = False

                    # 更新当前通话的电话号码
                    if 'number' in call:
                        self.call_number = call['number']

                except Exception as e:
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 解析CLCC响应出错: {str(e)}")

        # 如果没有通话，重置状态
        if not calls:
            self.in_call = False
            self.call_connected = False

        return calls

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
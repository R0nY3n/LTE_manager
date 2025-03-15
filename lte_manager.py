import serial
import threading
import time
import re
import binascii
import queue
from PyQt5.QtCore import QObject, pyqtSignal, QDateTime
from sms_utils import text_to_ucs2, ucs2_to_text, is_chinese_text, format_phone_number

class LTEManager(QObject):
    # Signals
    sms_received = pyqtSignal(str, str, str)  # sender, timestamp, message
    call_received = pyqtSignal(str)  # caller number
    call_ended = pyqtSignal(str)  # duration
    status_changed = pyqtSignal(str)  # status message
    dtmf_received = pyqtSignal(str)  # DTMF tone

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
        self.call_number = ""

        # SMS handling
        self.waiting_for_sms_content = False
        self.pending_sms_sender = None
        self.pending_sms_timestamp = None

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
        self.running = False
        if self.read_thread:
            self.read_thread.join(timeout=1.0)

        if self.at_serial and self.at_serial.is_open:
            self.at_serial.close()

        if self.nmea_serial and self.nmea_serial.is_open:
            self.nmea_serial.close()

        self.connected = False
        self.status_changed.emit("Disconnected from LTE module")

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

        # Caller ID
        elif "+CLIP:" in line:
            match = re.search(r'\+CLIP: "([^"]+)"', line)
            if match:
                number = match.group(1)
                self.call_number = number
                self.call_received.emit(number)

        # Call ended
        elif "NO CARRIER" in line:
            self.in_call = False
            self.call_ended.emit("Call ended")

        # Voice call begin
        elif "VOICE CALL: BEGIN" in line:
            self.in_call = True
            self.status_changed.emit("Call in progress")

        # Voice call end
        elif "VOICE CALL: END:" in line:
            self.in_call = False
            match = re.search(r'VOICE CALL: END: (\d+)', line)
            if match:
                duration = match.group(1)
                self.call_ended.emit(duration)

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
            # Parse SMS header
            header_match = re.search(r'\+CMT: "([^"]*)",[^,]*,"([^"]*)"', line)
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
        elif self.waiting_for_sms_content:
            # This is the SMS content line
            self.waiting_for_sms_content = False
            message = line

            # Check if the content is in UCS2 format (hex string)
            if all(c in "0123456789ABCDEFabcdef" for c in line.replace(" ", "")):
                try:
                    # Try to decode as UCS2
                    message = ucs2_to_text(line)
                    self.status_changed.emit("Decoded UCS2 message")
                except Exception as e:
                    self.status_changed.emit(f"Failed to decode UCS2: {str(e)}")
                    # Keep original if decoding fails
                    message = line

            # Emit signal with SMS details
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

    def _initialize_module(self):
        """Initialize the LTE module with basic settings"""
        # Check if module is responsive
        response = self.send_at_command("AT")
        if "OK" not in response:
            self.status_changed.emit("Module not responding")
            return False

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

    def _decode_pdu_message(self, pdu_str):
        """Decode PDU format message (including Chinese characters)"""
        try:
            # Remove spaces and convert to bytes
            pdu_str = pdu_str.replace(" ", "")

            # Try to decode using our utility function
            return ucs2_to_text(pdu_str)
        except Exception as e:
            print(f"PDU decode error: {str(e)}")
            # If decoding fails, return the original string
            return f"[Decode error: {pdu_str[:30]}...]"

    def make_call(self, number):
        """Make a phone call"""
        if not self.connected:
            return False

        response = self.send_at_command(f"ATD{number};")
        if "OK" in response:
            self.in_call = True
            self.call_number = number
            self.status_changed.emit(f"Calling {number}")
            return True
        return False

    def answer_call(self):
        """Answer incoming call"""
        if not self.connected:
            return False

        response = self.send_at_command("ATA")
        if "OK" in response:
            self.in_call = True
            self.status_changed.emit("Call answered")
            return True
        return False

    def end_call(self):
        """End current call"""
        if not self.connected:
            return False

        response = self.send_at_command("ATH")
        if "OK" in response:
            self.in_call = False
            self.status_changed.emit("Call ended")
            return True
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
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                            QLineEdit, QTextEdit, QGroupBox, QFormLayout, QComboBox,
                            QGridLayout, QMessageBox, QSpinBox, QCheckBox, QFileDialog)
from PyQt5.QtCore import Qt, pyqtSlot
import serial.tools.list_ports
import os
import json

class SettingsTab(QWidget):
    def __init__(self, lte_manager, audio_features=None):
        super().__init__()
        self.lte_manager = lte_manager
        self.audio_features = audio_features

        # Settings file path
        self.settings_file = "lte_settings.json"

        # Default settings
        self.settings = {
            "at_port": "",
            "at_baudrate": "115200",
            "nmea_port": "None",
            "nmea_baudrate": "9600",
            "auto_connect": False,
            "audio_enabled": True,
            "ringtone_file": "",
            "recording_path": "",
            "auto_record_calls": True,  # 自动录制通话
            "auto_play_after_call": False,  # 通话结束后自动播放录音
            "auto_play_on_answer": False,  # 接听电话时自动播放声音
            "answer_play_audio_file": "",  # 接听时播放的音频文件
        }

        # 从文件加载设置
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    saved_settings = json.load(f)
                    for key, value in saved_settings.items():
                        self.settings[key] = value
        except Exception as e:
            print(f"加载设置失败: {str(e)}")

        # Connect signals
        self.lte_manager.status_changed.connect(self.on_status_changed)
        if self.audio_features:
            self.audio_features.status_changed.connect(self.on_status_changed)

        # Create UI components first
        self._create_ui_components()

        # Then setup the UI layout
        self.setup_ui()

        # Finally refresh the ports
        self.refresh_ports()

        # 应用设置到UI
        self._apply_settings_to_ui()

        # 如果初始化时有音频特性模块，立即应用相关设置
        if self.audio_features:
            # 设置录音路径
            if self.settings.get("recording_path") and os.path.exists(self.settings.get("recording_path")):
                self.audio_features.set_storage_path(self.settings.get("recording_path"))

            # 设置自动录音选项
            self.audio_features.set_auto_record_calls(self.settings.get("auto_record_calls", True))

            # 设置自动播放选项
            self.audio_features.set_auto_play_after_call(self.settings.get("auto_play_after_call", False))

    def _apply_settings_to_ui(self):
        """将加载的设置应用到UI组件"""
        # 加载设置
        self.auto_record_cb.setChecked(self.settings.get('auto_record_calls', True))
        self.auto_play_cb.setChecked(self.settings.get('auto_play_after_call', False))
        self.auto_play_on_answer_cb.setChecked(self.settings.get('auto_play_on_answer', False))

        recording_path = self.settings.get('recording_path', '')
        self.recording_path_edit.setText(recording_path)

        answer_play_file = self.settings.get('answer_play_audio_file', '')
        self.answer_play_edit.setText(answer_play_file)

        # 如果音频特性实例存在，应用设置
        if self.audio_features:
            self.audio_features.set_auto_record_calls(self.auto_record_cb.isChecked())
            self.audio_features.set_auto_play_after_call(self.auto_play_cb.isChecked())

            answer_audio_file = self.answer_play_edit.text()
            if answer_audio_file and os.path.exists(answer_audio_file):
                self.audio_features.set_auto_play_on_answer(
                    self.auto_play_on_answer_cb.isChecked(),
                    answer_audio_file
                )

    def _create_ui_components(self):
        """创建所有UI组件"""
        # 创建串口选择下拉框
        self.at_port_combo = QComboBox()
        self.at_port_combo.setMinimumWidth(120)

        self.at_baud_combo = QComboBox()
        self.at_baud_combo.setMinimumWidth(100)
        self.at_baud_combo.addItems(['115200', '9600', '38400', '57600'])

        self.nmea_port_combo = QComboBox()
        self.nmea_port_combo.setMinimumWidth(120)
        self.nmea_port_combo.addItem("None")

        self.nmea_baud_combo = QComboBox()
        self.nmea_baud_combo.setMinimumWidth(100)
        self.nmea_baud_combo.addItems(['9600', '38400', '57600', '115200'])

        # 创建按钮
        self.connect_btn = QPushButton("连接")
        self.connect_btn.clicked.connect(self.toggle_connection)

        # 创建AT命令输入框和发送按钮
        self.at_command_input = QLineEdit()
        self.at_command_input.setPlaceholderText("输入AT命令")
        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self.send_at_command)

        # 创建文本显示区域
        self.at_response_text = QTextEdit()
        self.at_response_text.setMaximumHeight(150)
        self.at_response_text.setReadOnly(True)

        self.status_text = QTextEdit()
        self.status_text.setMinimumHeight(200)
        self.status_text.setReadOnly(True)

        # 创建复选框
        self.auto_connect_check = QCheckBox("启动时自动连接")
        self.auto_connect_check.setChecked(self.settings.get("auto_connect", False))

        # 音频功能组件
        self.audio_enabled_check = QCheckBox("启用音频功能")
        self.audio_enabled_check.setChecked(self.settings.get("audio_enabled", True))

        # 自动录音选项
        self.auto_record_calls_check = QCheckBox("自动录制通话")
        self.auto_record_calls_check.setChecked(self.settings.get("auto_record_calls", True))
        self.auto_record_calls_check.setToolTip("接听电话时自动开始录音，通话结束时自动停止")
        self.auto_record_calls_check.stateChanged.connect(self.on_auto_record_changed)

        # 自动播放录音选项
        self.auto_play_after_call_check = QCheckBox("通话结束后自动播放录音")
        self.auto_play_after_call_check.setChecked(self.settings.get("auto_play_after_call", False))
        self.auto_play_after_call_check.setToolTip("通话结束后自动播放刚录制的通话录音")
        self.auto_play_after_call_check.stateChanged.connect(self.on_auto_play_changed)

        # 录音路径
        self.recording_path_input = QLineEdit()
        # 如果没有设置过录音路径，使用音频特性模块的默认路径
        if self.audio_features and not self.settings.get("recording_path"):
            self.settings["recording_path"] = self.audio_features.storage_path
        self.recording_path_input.setText(self.settings.get("recording_path", ""))

        self.recording_path_btn = QPushButton("浏览...")
        self.recording_path_btn.clicked.connect(self.browse_recording_path)
        self.recording_path_reset_btn = QPushButton("重置")
        self.recording_path_reset_btn.setToolTip("重置为默认路径")
        self.recording_path_reset_btn.clicked.connect(self.reset_recording_path)

        # 铃声设置
        self.ringtone_path_input = QLineEdit()
        self.ringtone_path_input.setText(self.settings.get("ringtone_file", ""))
        self.ringtone_path_btn = QPushButton("浏览...")
        self.ringtone_path_btn.clicked.connect(self.browse_ringtone_file)
        self.set_ringtone_btn = QPushButton("设置铃声")
        self.set_ringtone_btn.clicked.connect(self.set_ringtone)

        # 录音控制
        self.start_recording_btn = QPushButton("开始录音")
        self.start_recording_btn.clicked.connect(self.start_recording)
        self.stop_recording_btn = QPushButton("停止录音")
        self.stop_recording_btn.clicked.connect(self.stop_recording)

        # 录音类型选择
        self.recording_type_combo = QComboBox()
        self.recording_type_combo.addItems(["本地麦克风", "通话对方声音", "双方声音混合"])

        # 播放音频
        self.play_audio_input = QLineEdit()
        self.play_audio_input.setPlaceholderText("输入音频文件名或路径")
        self.play_audio_btn = QPushButton("播放")
        self.play_audio_btn.clicked.connect(self.play_audio)
        self.stop_audio_btn = QPushButton("停止")
        self.stop_audio_btn.clicked.connect(self.stop_audio)
        self.browse_audio_btn = QPushButton("浏览...")
        self.browse_audio_btn.clicked.connect(self.browse_audio_file)

        # 播放类型选择
        self.play_type_combo = QComboBox()
        self.play_type_combo.addItems(["本地播放", "远程播放(对方听)", "双方都播放"])

        # 创建音频控制部分
        self.audio_group = QGroupBox("音频控制")
        self.audio_layout = QVBoxLayout()

        # 通话录音部分
        self.recording_section = QGroupBox("通话录音")
        recording_layout = QVBoxLayout()

        # 自动录制选项
        self.auto_record_cb = QCheckBox("自动录制通话")
        self.auto_record_cb.setToolTip("接听电话时自动开始录音，挂断时自动停止")
        self.auto_record_cb.stateChanged.connect(self.on_auto_record_changed)
        recording_layout.addWidget(self.auto_record_cb)

        # 录音后自动播放选项
        self.auto_play_cb = QCheckBox("录音后自动播放")
        self.auto_play_cb.setToolTip("通话结束后自动播放录音")
        self.auto_play_cb.stateChanged.connect(self.on_auto_play_changed)
        recording_layout.addWidget(self.auto_play_cb)

        # 录音路径选择
        recording_path_layout = QHBoxLayout()
        self.recording_path_label = QLabel("录音存储路径:")
        self.recording_path_edit = QLineEdit()
        self.recording_path_edit.setReadOnly(True)
        self.browse_recording_btn = QPushButton("浏览...")
        self.browse_recording_btn.clicked.connect(self.browse_recording_path)
        self.reset_recording_btn = QPushButton("重置")
        self.reset_recording_btn.clicked.connect(self.reset_recording_path)

        recording_path_layout.addWidget(self.recording_path_label)
        recording_path_layout.addWidget(self.recording_path_edit)
        recording_path_layout.addWidget(self.browse_recording_btn)
        recording_path_layout.addWidget(self.reset_recording_btn)
        recording_layout.addLayout(recording_path_layout)

        # 录音控制按钮
        recording_control_layout = QHBoxLayout()
        self.start_recording_btn = QPushButton("开始录音")
        self.start_recording_btn.clicked.connect(self.start_recording)
        self.stop_recording_btn = QPushButton("停止录音")
        self.stop_recording_btn.clicked.connect(self.stop_recording)

        recording_control_layout.addWidget(self.start_recording_btn)
        recording_control_layout.addWidget(self.stop_recording_btn)
        recording_layout.addLayout(recording_control_layout)

        self.recording_section.setLayout(recording_layout)
        self.audio_layout.addWidget(self.recording_section)

        # 音频播放部分
        self.playback_section = QGroupBox("音频播放")
        playback_layout = QVBoxLayout()

        # 自动接听播放选项
        self.auto_play_on_answer_cb = QCheckBox("接听电话时自动播放音频")
        self.auto_play_on_answer_cb.setToolTip("接听电话时自动向对方播放指定音频文件")
        self.auto_play_on_answer_cb.stateChanged.connect(self.on_auto_play_on_answer_changed)
        playback_layout.addWidget(self.auto_play_on_answer_cb)

        # 接听播放音频选择
        answer_play_layout = QHBoxLayout()
        self.answer_play_label = QLabel("接听播放音频:")
        self.answer_play_edit = QLineEdit()
        self.answer_play_edit.setReadOnly(True)
        self.browse_answer_play_btn = QPushButton("浏览...")
        self.browse_answer_play_btn.clicked.connect(self.browse_answer_play_file)

        answer_play_layout.addWidget(self.answer_play_label)
        answer_play_layout.addWidget(self.answer_play_edit)
        answer_play_layout.addWidget(self.browse_answer_play_btn)
        playback_layout.addLayout(answer_play_layout)

        # 音频文件选择
        audio_file_layout = QHBoxLayout()
        self.audio_file_label = QLabel("音频文件:")
        self.audio_file_edit = QLineEdit()
        self.audio_file_edit.setReadOnly(True)
        self.browse_audio_btn = QPushButton("浏览...")
        self.browse_audio_btn.clicked.connect(self.browse_audio_file)

        audio_file_layout.addWidget(self.audio_file_label)
        audio_file_layout.addWidget(self.audio_file_edit)
        audio_file_layout.addWidget(self.browse_audio_btn)
        playback_layout.addLayout(audio_file_layout)

        # 播放控制按钮
        playback_control_layout = QHBoxLayout()
        self.play_audio_btn = QPushButton("播放音频")
        self.play_audio_btn.clicked.connect(self.play_audio)
        self.stop_audio_btn = QPushButton("停止播放")
        self.stop_audio_btn.clicked.connect(self.stop_audio)

        playback_control_layout.addWidget(self.play_audio_btn)
        playback_control_layout.addWidget(self.stop_audio_btn)
        playback_layout.addLayout(playback_control_layout)

        self.playback_section.setLayout(playback_layout)
        self.audio_layout.addWidget(self.playback_section)

        # 铃声设置部分
        self.ringtone_section = QGroupBox("铃声设置")
        ringtone_layout = QVBoxLayout()

        # 铃声文件选择
        ringtone_file_layout = QHBoxLayout()
        self.ringtone_file_label = QLabel("铃声文件:")
        self.ringtone_file_edit = QLineEdit()
        self.ringtone_file_edit.setReadOnly(True)
        self.browse_ringtone_btn = QPushButton("浏览...")
        self.browse_ringtone_btn.clicked.connect(self.browse_ringtone_file)

        ringtone_file_layout.addWidget(self.ringtone_file_label)
        ringtone_file_layout.addWidget(self.ringtone_file_edit)
        ringtone_file_layout.addWidget(self.browse_ringtone_btn)
        ringtone_layout.addLayout(ringtone_file_layout)

        # 铃声设置按钮
        self.set_ringtone_btn = QPushButton("设置铃声")
        self.set_ringtone_btn.clicked.connect(self.set_ringtone)
        ringtone_layout.addWidget(self.set_ringtone_btn)

        self.ringtone_section.setLayout(ringtone_layout)
        self.audio_layout.addWidget(self.ringtone_section)

        self.audio_group.setLayout(self.audio_layout)

    def setup_ui(self):
        """设置UI布局"""
        layout = QVBoxLayout(self)

        # 串口设置组
        serial_group = QGroupBox("串口设置")
        serial_layout = QGridLayout()

        # 第一行：AT串口和波特率
        serial_layout.addWidget(QLabel("AT串口:"), 0, 0)
        serial_layout.addWidget(self.at_port_combo, 0, 1)

        serial_layout.addWidget(QLabel("AT波特率:"), 0, 2)
        serial_layout.addWidget(self.at_baud_combo, 0, 3)

        # 第二行：NMEA串口和波特率
        serial_layout.addWidget(QLabel("NMEA串口:"), 1, 0)
        serial_layout.addWidget(self.nmea_port_combo, 1, 1)

        serial_layout.addWidget(QLabel("NMEA波特率:"), 1, 2)
        serial_layout.addWidget(self.nmea_baud_combo, 1, 3)

        # 第三行：刷新按钮和自动连接选项
        refresh_btn = QPushButton("刷新串口")
        refresh_btn.clicked.connect(self.refresh_ports)
        serial_layout.addWidget(refresh_btn, 2, 0, 1, 2)

        serial_layout.addWidget(self.auto_connect_check, 2, 2, 1, 2)

        serial_group.setLayout(serial_layout)
        layout.addWidget(serial_group)

        # 连接按钮
        layout.addWidget(self.connect_btn)

        # 添加音频控制组
        layout.addWidget(self.audio_group)

        # AT命令区域
        at_group = QGroupBox("AT命令")
        at_layout = QVBoxLayout()

        # AT命令输入区域
        at_input_layout = QHBoxLayout()
        at_input_layout.addWidget(self.at_command_input)
        at_input_layout.addWidget(self.send_btn)
        at_layout.addLayout(at_input_layout)

        # AT响应显示区域
        at_layout.addWidget(self.at_response_text)

        at_group.setLayout(at_layout)
        layout.addWidget(at_group)

        # 状态信息区域
        status_group = QGroupBox("状态信息")
        status_layout = QVBoxLayout()
        status_layout.addWidget(self.status_text)
        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

    def on_auto_record_changed(self, state):
        """处理自动录制通话选项变更"""
        is_checked = state == Qt.Checked
        if self.audio_features:
            self.audio_features.set_auto_record_calls(is_checked)
            self.settings["auto_record_calls"] = is_checked
            self.save_settings()

    def on_auto_play_changed(self, state):
        """处理自动播放录音选项变更"""
        is_checked = state == Qt.Checked
        if self.audio_features:
            self.audio_features.set_auto_play_after_call(is_checked)
            self.settings["auto_play_after_call"] = is_checked
            self.save_settings()

    def reset_recording_path(self):
        """重置录音路径为默认值"""
        if self.audio_features:
            # 重置为音频模块的默认路径
            default_path = self.audio_features.storage_path
            self.recording_path_edit.setText(default_path)
            self.audio_features.set_storage_path(default_path)
            self.settings["recording_path"] = default_path
            self.save_settings()
            self.add_status_message(f"录音路径已重置为: {default_path}")

    def get_available_ports(self):
        """Get list of available serial ports"""
        ports = []
        for port in serial.tools.list_ports.comports():
            ports.append(port.device)
        return ports

    def refresh_ports(self):
        """Refresh available serial ports"""
        self.at_port_combo.clear()
        self.nmea_port_combo.clear()
        self.nmea_port_combo.addItem("None")

        ports = self.get_available_ports()
        for port in ports:
            self.at_port_combo.addItem(port)
            self.nmea_port_combo.addItem(port)

        # Set saved ports if available
        if self.settings["at_port"] in ports:
            self.at_port_combo.setCurrentText(self.settings["at_port"])

        if self.settings["nmea_port"] in ports or self.settings["nmea_port"] == "None":
            self.nmea_port_combo.setCurrentText(self.settings["nmea_port"])

    def toggle_connection(self):
        """切换连接状态"""
        if not self.lte_manager.is_connected():
            self.connect()
        else:
            self.disconnect()

    def connect(self):
        """连接到LTE模块"""
        at_port = self.at_port_combo.currentText()
        at_baud = int(self.at_baud_combo.currentText())

        # 旧代码保留在注释中，以便后续可能的恢复
        # nmea_port = self.nmea_port_combo.currentText()
        # nmea_baud = int(self.nmea_baud_combo.currentText())
        # if nmea_port == "None":
        #     nmea_port = ""

        # 使用新的连接方法，只传递AT端口和波特率
        if self.lte_manager.connect(port=at_port, baudrate=at_baud):
            self.connect_btn.setText("断开")
            self.save_settings()

            # 更新音频控件状态
            if self.audio_features:
                self.update_audio_controls_state()

                # 如果有设置铃声，自动应用
                if self.settings.get("ringtone_file") and os.path.exists(self.settings.get("ringtone_file")):
                    self.set_ringtone()

                # 如果有设置录音路径，自动应用
                if self.settings.get("recording_path") and os.path.exists(self.settings.get("recording_path")):
                    self.audio_features.set_storage_path(self.settings.get("recording_path"))

                # 应用自动录音和自动播放设置
                self.audio_features.set_auto_record_calls(self.auto_record_cb.isChecked())
                self.audio_features.set_auto_play_after_call(self.auto_play_cb.isChecked())
        else:
            QMessageBox.warning(self, "连接错误", "无法连接到LTE模块")

    def disconnect(self):
        """断开LTE模块连接"""
        self.lte_manager.disconnect()
        self.connect_btn.setText("连接")

        # 更新音频控件状态
        if self.audio_features:
            self.update_audio_controls_state()

    def send_at_command(self):
        """发送AT命令"""
        if not self.lte_manager.is_connected():
            QMessageBox.warning(self, "错误", "未连接到LTE模块")
            return

        command = self.at_command_input.text().strip()
        if not command:
            return

        self.at_response_text.append(f">>> {command}")
        response = self.lte_manager.send_at_command(command)
        if response:
            self.at_response_text.append(response)
        self.at_command_input.clear()

    def on_status_changed(self, status):
        """Handle status change"""
        self.add_status_message(status)

    def add_status_message(self, message):
        """Add message to status display"""
        self.status_text.append(message)
        self.status_text.ensureCursorVisible()

    def save_settings(self):
        """保存设置到文件"""
        try:
            # 更新设置字典
            self.settings["at_port"] = self.at_port_combo.currentText()
            self.settings["at_baudrate"] = self.at_baud_combo.currentText()
            self.settings["nmea_port"] = self.nmea_port_combo.currentText()
            self.settings["nmea_baudrate"] = self.nmea_baud_combo.currentText()
            self.settings["auto_connect"] = self.auto_connect_check.isChecked()
            self.settings["recording_path"] = self.recording_path_edit.text()
            self.settings["auto_record_calls"] = self.auto_record_cb.isChecked()
            self.settings["auto_play_after_call"] = self.auto_play_cb.isChecked()
            self.settings["auto_play_on_answer"] = self.auto_play_on_answer_cb.isChecked()
            self.settings["answer_play_audio_file"] = self.answer_play_edit.text()

            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f, indent=4)

            print("设置已保存到", self.settings_file)
        except Exception as e:
            print(f"保存设置失败: {str(e)}")

    def try_auto_connect(self):
        """尝试自动连接"""
        if self.auto_connect_check.isChecked():
            self.connect()

    # 音频功能相关方法
    def update_audio_controls_state(self):
        """更新音频控制按钮的状态"""
        if not hasattr(self, 'audio_features') or not self.audio_features:
            return

        connected = self.lte_manager.is_connected()
        recording = self.audio_features.is_recording() if connected else False
        playing = self.audio_features.playing if connected else False

        # 启用/禁用录音控制按钮
        self.start_recording_btn.setEnabled(connected and not recording)
        self.stop_recording_btn.setEnabled(connected and recording)

        # 启用/禁用播放控制按钮
        self.play_audio_btn.setEnabled(connected and not playing)
        self.stop_audio_btn.setEnabled(connected and playing)

        # 铃声设置按钮
        self.set_ringtone_btn.setEnabled(connected)

        # 自动功能控制
        self.auto_record_cb.setEnabled(connected)
        self.auto_play_cb.setEnabled(connected)
        self.auto_play_on_answer_cb.setEnabled(connected)

        # 路径设置按钮
        self.browse_recording_btn.setEnabled(True)  # 这个不依赖连接状态
        self.reset_recording_btn.setEnabled(True)   # 这个不依赖连接状态
        self.browse_audio_btn.setEnabled(connected)
        self.browse_ringtone_btn.setEnabled(connected)
        self.browse_answer_play_btn.setEnabled(connected)

    def browse_recording_path(self):
        """浏览并选择录音存储路径"""
        current_path = self.recording_path_edit.text() or os.path.expanduser("~")

        dir_path = QFileDialog.getExistingDirectory(
            self, "选择录音存储路径", current_path
        )

        if dir_path:
            self.recording_path_edit.setText(dir_path)
            if self.audio_features:
                self.audio_features.set_storage_path(dir_path)
            self.save_settings()
            self.add_status_message(f"录音存储路径已设置为: {dir_path}")

    def browse_ringtone_file(self):
        """浏览并选择铃声文件"""
        home_dir = os.path.expanduser("~")
        file_dialog = QFileDialog()
        file_path, _ = file_dialog.getOpenFileName(
            self, "选择铃声文件", home_dir,
            "音频文件 (*.mp3 *.wav *.amr);;所有文件 (*)"
        )

        if file_path:
            self.ringtone_file_edit.setText(file_path)
            self.save_settings()

    def set_ringtone(self):
        """设置铃声"""
        if not self.audio_features or not self.lte_manager.is_connected():
            return

        ringtone_file = self.ringtone_file_edit.text()
        if not ringtone_file:
            QMessageBox.warning(self, "设置铃声", "请先选择铃声文件")
            return

        if self.audio_features.set_ringtone(ringtone_file):
            QMessageBox.information(self, "设置铃声", "铃声设置成功")
        else:
            QMessageBox.warning(self, "设置铃声", "铃声设置失败")

    def start_recording(self):
        """开始录音"""
        if not self.audio_features or not self.lte_manager.is_connected():
            return

        record_type = self.recording_type_combo.currentIndex() + 1  # 1=本地, 2=远程, 3=混合

        if self.audio_features.start_recording(record_path=record_type):
            self.update_audio_controls_state()
            self.add_status_message("录音已开始")
        else:
            QMessageBox.warning(self, "录音", "开始录音失败")

    def stop_recording(self):
        """停止录音"""
        if not self.audio_features or not self.lte_manager.is_connected():
            return

        if self.audio_features.stop_recording():
            self.update_audio_controls_state()
            self.add_status_message("录音已停止")
        else:
            QMessageBox.warning(self, "录音", "停止录音失败")

    def browse_audio_file(self):
        """浏览并选择音频文件"""
        home_dir = os.path.expanduser("~")
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择音频文件", home_dir,
            "音频文件 (*.mp3 *.wav *.amr *.pcm);;所有文件 (*)"
        )

        if file_path:
            self.audio_file_edit.setText(file_path)

    def play_audio(self):
        """播放选择的音频文件"""
        if not self.lte_manager.is_connected() or not self.audio_features:
            QMessageBox.warning(self, "播放音频", "未连接到LTE模块或音频功能未启用")
            return

        audio_file = self.audio_file_edit.text()
        if not audio_file:
            # 如果未选择文件，提示用户
            self.browse_audio_file()
            audio_file = self.audio_file_edit.text()
            if not audio_file:
                return

        play_type = self.play_type_combo.currentIndex()

        success = self.audio_features.play_audio(audio_file, play_type)
        if success:
            self.add_status_message(f"开始播放: {os.path.basename(audio_file)}")
        else:
            QMessageBox.warning(self, "播放音频", "播放音频失败")

    def stop_audio(self):
        """停止播放音频"""
        if not self.audio_features or not self.lte_manager.is_connected():
            return

        if self.audio_features.stop_audio():
            self.update_audio_controls_state()
            self.add_status_message("音频播放已停止")
        else:
            QMessageBox.warning(self, "播放音频", "停止播放失败")

    def on_auto_play_on_answer_changed(self, state):
        """处理接听电话自动播放音频选项变更"""
        enabled = state == Qt.Checked

        if self.audio_features:
            # 如果启用但未设置音频文件，提示用户选择
            if enabled and not self.answer_play_edit.text():
                self.browse_answer_play_file()
                # 如果用户取消了选择，则取消勾选
                if not self.answer_play_edit.text():
                    self.auto_play_on_answer_cb.setChecked(False)
                    return

            audio_file = self.answer_play_edit.text() if self.answer_play_edit.text() else None
            self.audio_features.set_auto_play_on_answer(enabled, audio_file)

        self.save_settings()

    def browse_answer_play_file(self):
        """浏览并选择接听时要播放的音频文件"""
        home_dir = os.path.expanduser("~")
        file_dialog = QFileDialog()
        file_path, _ = file_dialog.getOpenFileName(
            self, "选择音频文件", home_dir,
            "音频文件 (*.amr *.wav *.mp3 *.pcm);;所有文件 (*)"
        )

        if file_path:
            self.answer_play_edit.setText(file_path)

            # 如果音频特性实例存在，更新设置
            if self.audio_features:
                self.audio_features.set_auto_play_on_answer(
                    self.auto_play_on_answer_cb.isChecked(),
                    file_path
                )

            self.save_settings()
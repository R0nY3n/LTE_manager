from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                            QLineEdit, QTextEdit, QGroupBox, QFormLayout, QComboBox,
                            QGridLayout, QMessageBox, QSpinBox, QCheckBox)
from PyQt5.QtCore import Qt, pyqtSlot, QTimer
import serial.tools.list_ports
import os
import json
import time

class SettingsTab(QWidget):
    def __init__(self, lte_manager):
        """初始化设置标签页"""
        super().__init__()
        self.lte_manager = lte_manager
        self.settings = {}
        self.settings_file = os.path.join(os.path.expanduser('~'), '.LTE', 'settings.json')

        # 加载设置
        self.settings = {
            "at_port": "",
            "at_baudrate": "115200",
            "nmea_port": "None",
            "nmea_baudrate": "9600",
            "auto_connect": False
        }
        self.load_settings()

        # 初始化UI
        self.init_ui()

        # 注册状态变化的信号处理
        self.lte_manager.status_changed.connect(self.on_status_changed)

        # 如果启用了自动连接，尝试连接
        if self.settings.get("auto_connect", False):
            QTimer.singleShot(1000, self.try_auto_connect)

    def init_ui(self):
        """初始化用户界面"""
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # 端口设置组
        port_group = QGroupBox("端口设置")
        port_layout = QFormLayout()

        # AT端口
        at_port_layout = QHBoxLayout()
        self.at_port_combo = QComboBox()
        at_port_layout.addWidget(self.at_port_combo)

        self.refresh_ports_button = QPushButton("刷新")
        self.refresh_ports_button.clicked.connect(self.refresh_ports)
        at_port_layout.addWidget(self.refresh_ports_button)

        port_layout.addRow("AT Port:", at_port_layout)

        # AT波特率
        self.at_baudrate_combo = QComboBox()
        self.at_baudrate_combo.addItems(["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"])
        self.at_baudrate_combo.setCurrentText(self.settings["at_baudrate"])  # 从保存的设置设置
        port_layout.addRow("AT Baudrate:", self.at_baudrate_combo)

        # NMEA端口
        nmea_port_layout = QHBoxLayout()
        self.nmea_port_combo = QComboBox()
        self.nmea_port_combo.addItem("None")
        nmea_port_layout.addWidget(self.nmea_port_combo)

        port_layout.addRow("NMEA Port:", nmea_port_layout)

        # NMEA波特率
        self.nmea_baudrate_combo = QComboBox()
        self.nmea_baudrate_combo.addItems(["4800", "9600", "19200", "38400", "57600", "115200"])
        self.nmea_baudrate_combo.setCurrentText(self.settings["nmea_baudrate"])  # 从保存的设置设置
        port_layout.addRow("NMEA Baudrate:", self.nmea_baudrate_combo)

        # 自动连接复选框
        self.auto_connect_checkbox = QCheckBox("自动连接")
        self.auto_connect_checkbox.setChecked(self.settings.get("auto_connect", False))
        self.auto_connect_checkbox.stateChanged.connect(self.on_auto_connect_changed)
        port_layout.addRow("启动选项:", self.auto_connect_checkbox)

        # 刷新端口列表
        self.refresh_ports()

        # 连接/断开按钮
        buttons_layout = QHBoxLayout()
        self.connect_button = QPushButton("连接")
        self.connect_button.clicked.connect(self.on_connect_button_clicked)
        buttons_layout.addWidget(self.connect_button)

        self.disconnect_button = QPushButton("断开")
        self.disconnect_button.clicked.connect(self.on_disconnect_button_clicked)
        self.disconnect_button.setEnabled(False)
        buttons_layout.addWidget(self.disconnect_button)

        port_layout.addRow("", buttons_layout)
        port_group.setLayout(port_layout)
        main_layout.addWidget(port_group)

        # 模块信息组
        module_group = QGroupBox("模块信息")
        module_layout = QVBoxLayout()

        # 添加信息文本显示区域
        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)
        self.info_text.setMinimumHeight(150)
        module_layout.addWidget(self.info_text)

        # 刷新按钮
        self.refresh_info_button = QPushButton("刷新信息")
        self.refresh_info_button.clicked.connect(self.refresh_module_info)
        self.refresh_info_button.setEnabled(False)
        module_layout.addWidget(self.refresh_info_button)

        module_group.setLayout(module_layout)
        main_layout.addWidget(module_group)

        # AT命令控制台
        console_group = QGroupBox("AT命令控制台")
        console_layout = QVBoxLayout()

        # 命令输入
        command_layout = QHBoxLayout()
        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("输入AT命令")
        command_layout.addWidget(self.command_input)

        self.send_command_button = QPushButton("发送")
        self.send_command_button.clicked.connect(self.on_send_command_button_clicked)
        self.send_command_button.setEnabled(False)
        command_layout.addWidget(self.send_command_button)

        console_layout.addLayout(command_layout)

        # 响应显示
        console_layout.addWidget(QLabel("响应:"))
        self.response_display = QTextEdit()
        self.response_display.setReadOnly(True)
        console_layout.addWidget(self.response_display)

        console_group.setLayout(console_layout)
        main_layout.addWidget(console_group)

        # 状态显示
        self.status_display = QTextEdit()
        self.status_display.setReadOnly(True)
        self.status_display.setMaximumHeight(100)
        main_layout.addWidget(QLabel("状态:"))
        main_layout.addWidget(self.status_display)

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

    def on_connect_button_clicked(self):
        """Handle connect button click"""
        at_port = self.at_port_combo.currentText()
        at_baudrate = int(self.at_baudrate_combo.currentText())
        nmea_port = self.nmea_port_combo.currentText()
        if nmea_port == "None":
            nmea_port = ""
        nmea_baudrate = int(self.nmea_baudrate_combo.currentText())

        # Save settings
        self.settings["at_port"] = at_port
        self.settings["at_baudrate"] = self.at_baudrate_combo.currentText()
        self.settings["nmea_port"] = nmea_port if nmea_port else "None"
        self.settings["nmea_baudrate"] = self.nmea_baudrate_combo.currentText()
        self.settings["auto_connect"] = self.auto_connect_checkbox.isChecked()
        self.save_settings()

        if self.lte_manager.connect(at_port, at_baudrate, nmea_port, nmea_baudrate):
            self.connect_button.setEnabled(False)
            self.disconnect_button.setEnabled(True)
            self.refresh_info_button.setEnabled(True)
            self.send_command_button.setEnabled(True)
            self.add_status_message("Connected to LTE module")
            self.refresh_module_info()
        else:
            self.add_status_message("Failed to connect to LTE module")

    def on_disconnect_button_clicked(self):
        """Handle disconnect button click"""
        self.lte_manager.disconnect()
        self.connect_button.setEnabled(True)
        self.disconnect_button.setEnabled(False)
        self.refresh_info_button.setEnabled(False)
        self.send_command_button.setEnabled(False)
        self.add_status_message("Disconnected from LTE module")

        # Reset module information
        self.info_text.clear()
        self.refresh_info_button.setEnabled(False)

    def on_send_command_button_clicked(self):
        """Handle send command button click"""
        command = self.command_input.text().strip()
        if not command:
            return

        self.add_status_message(f"Sending command: {command}")
        response = self.lte_manager.send_at_command(command)
        self.response_display.setText(response)
        self.command_input.clear()

    def refresh_module_info(self):
        """刷新模块信息显示"""
        if not self.lte_manager.is_connected():
            self.add_status_message("请先连接模块")
            return

        try:
            self.info_text.clear()
            self.add_status_message("正在获取模块信息...")

            # 获取模块信息
            module_info = self.lte_manager.get_module_info()
            if not module_info:
                self.add_status_message("获取模块信息失败")
                return

            # 显示模块信息
            self.info_text.append("<b>模块信息:</b>")
            for key, value in module_info.items():
                if value:  # 只显示有值的项目
                    self.info_text.append(f"<b>{key}:</b> {value}")

            # 获取运营商信息
            carrier_info = self.lte_manager.get_carrier_info()
            if carrier_info:
                if isinstance(carrier_info, tuple) and len(carrier_info) == 2:
                    carrier, network = carrier_info
                    self.info_text.append(f"<b>运营商:</b> {carrier}")
                    self.info_text.append(f"<b>网络类型:</b> {network}")
                else:
                    self.info_text.append(f"<b>运营商:</b> {carrier_info}")

            # 获取电话号码
            phone_number = self.lte_manager.get_phone_number()
            if phone_number:
                self.info_text.append(f"<b>电话号码:</b> {phone_number}")

            # 获取信号强度
            signal_info = self.lte_manager.get_signal_strength()
            if signal_info:
                if isinstance(signal_info, tuple) and len(signal_info) == 2:
                    signal_text, signal_desc = signal_info
                    self.info_text.append(f"<b>信号强度:</b> {signal_text} ({signal_desc})")
                else:
                    self.info_text.append(f"<b>信号强度:</b> {signal_info}")

            # 获取网络信息
            network_info = self.lte_manager.get_network_info()
            if network_info:
                self.info_text.append("<b>网络信息:</b>")
                for key, value in network_info.items():
                    if value:  # 只显示有值的项目
                        self.info_text.append(f"<b>{key}:</b> {value}")

            # 添加时间戳
            self.info_text.append(f"<i>更新时间: {time.strftime('%Y-%m-%d %H:%M:%S')}</i>")
            self.add_status_message("模块信息已更新")

        except Exception as e:
            self.add_status_message(f"刷新模块信息出错: {str(e)}")
            import traceback
            traceback.print_exc()

    def on_status_changed(self, status):
        """Handle status change"""
        self.add_status_message(status)

    def add_status_message(self, message):
        """Add message to status display"""
        self.status_display.append(message)
        self.status_display.ensureCursorVisible()

    def on_auto_connect_changed(self, state):
        """Handle auto connect checkbox state change"""
        self.settings["auto_connect"] = bool(state)
        self.save_settings()

    def try_auto_connect(self):
        """Try to automatically connect using saved settings"""
        if not self.settings.get("auto_connect", False):
            return False

        at_port = self.settings.get("at_port", "")
        if not at_port:
            self.add_status_message("Auto-connect: No saved port")
            return False

        # Check if the saved port is available
        available_ports = self.get_available_ports()
        if at_port not in available_ports:
            self.add_status_message(f"Auto-connect: Port {at_port} not available")
            return False

        # Get other settings
        at_baudrate = int(self.settings.get("at_baudrate", "115200"))
        nmea_port = self.settings.get("nmea_port", "None")
        if nmea_port == "None":
            nmea_port = ""
        nmea_baudrate = int(self.settings.get("nmea_baudrate", "9600"))

        # Try to connect
        self.add_status_message(f"Auto-connect: Trying to connect to {at_port}")
        if self.lte_manager.connect(at_port, at_baudrate, nmea_port, nmea_baudrate):
            self.connect_button.setEnabled(False)
            self.disconnect_button.setEnabled(True)
            self.refresh_info_button.setEnabled(True)
            self.send_command_button.setEnabled(True)
            self.add_status_message("Auto-connect: Connected to LTE module")
            self.refresh_module_info()
            return True
        else:
            self.add_status_message("Auto-connect: Failed to connect")
            return False

    def load_settings(self):
        """Load settings from file"""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    loaded_settings = json.load(f)
                    # Update settings with loaded values
                    for key, value in loaded_settings.items():
                        self.settings[key] = value
        except Exception as e:
            print(f"Error loading settings: {str(e)}")

    def save_settings(self):
        """Save settings to file"""
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            print(f"Error saving settings: {str(e)}")
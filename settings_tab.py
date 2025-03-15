from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                            QLineEdit, QTextEdit, QGroupBox, QFormLayout, QComboBox,
                            QGridLayout, QMessageBox, QSpinBox, QCheckBox)
from PyQt5.QtCore import Qt, pyqtSlot
import serial.tools.list_ports
import os
import json

class SettingsTab(QWidget):
    def __init__(self, lte_manager):
        super().__init__()
        self.lte_manager = lte_manager

        # Settings file path
        self.settings_file = "lte_settings.json"

        # Default settings
        self.settings = {
            "at_port": "",
            "at_baudrate": "115200",
            "nmea_port": "None",
            "nmea_baudrate": "9600",
            "auto_connect": False
        }

        # Load saved settings
        self.load_settings()

        # Connect signals
        self.lte_manager.status_changed.connect(self.on_status_changed)

        self.init_ui()

    def init_ui(self):
        # Main layout
        main_layout = QVBoxLayout(self)

        # Serial port settings
        port_group = QGroupBox("Serial Port Settings")
        port_layout = QFormLayout()

        # AT port
        at_port_layout = QHBoxLayout()
        self.at_port_combo = QComboBox()
        at_port_layout.addWidget(self.at_port_combo)

        self.refresh_ports_button = QPushButton("Refresh")
        self.refresh_ports_button.clicked.connect(self.refresh_ports)
        at_port_layout.addWidget(self.refresh_ports_button)

        port_layout.addRow("AT Port:", at_port_layout)

        # AT baudrate
        self.at_baudrate_combo = QComboBox()
        self.at_baudrate_combo.addItems(["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"])
        self.at_baudrate_combo.setCurrentText(self.settings["at_baudrate"])  # Set from saved settings
        port_layout.addRow("AT Baudrate:", self.at_baudrate_combo)

        # NMEA port
        nmea_port_layout = QHBoxLayout()
        self.nmea_port_combo = QComboBox()
        self.nmea_port_combo.addItem("None")
        nmea_port_layout.addWidget(self.nmea_port_combo)

        port_layout.addRow("NMEA Port:", nmea_port_layout)

        # NMEA baudrate
        self.nmea_baudrate_combo = QComboBox()
        self.nmea_baudrate_combo.addItems(["4800", "9600", "19200", "38400", "57600", "115200"])
        self.nmea_baudrate_combo.setCurrentText(self.settings["nmea_baudrate"])  # Set from saved settings
        port_layout.addRow("NMEA Baudrate:", self.nmea_baudrate_combo)

        # Auto connect checkbox
        self.auto_connect_checkbox = QCheckBox("自动连接")
        self.auto_connect_checkbox.setChecked(self.settings.get("auto_connect", False))
        self.auto_connect_checkbox.stateChanged.connect(self.on_auto_connect_changed)
        port_layout.addRow("启动选项:", self.auto_connect_checkbox)

        # Now refresh ports after all combo boxes are created
        self.refresh_ports()

        # Connect/Disconnect buttons
        buttons_layout = QHBoxLayout()
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.on_connect_button_clicked)
        buttons_layout.addWidget(self.connect_button)

        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(self.on_disconnect_button_clicked)
        self.disconnect_button.setEnabled(False)
        buttons_layout.addWidget(self.disconnect_button)

        port_layout.addRow("", buttons_layout)
        port_group.setLayout(port_layout)
        main_layout.addWidget(port_group)

        # Module information
        module_group = QGroupBox("Module Information")
        module_layout = QGridLayout()

        # Manufacturer
        module_layout.addWidget(QLabel("Manufacturer:"), 0, 0)
        self.manufacturer_label = QLabel("Not available")
        module_layout.addWidget(self.manufacturer_label, 0, 1)

        # Model
        module_layout.addWidget(QLabel("Model:"), 1, 0)
        self.model_label = QLabel("Not available")
        module_layout.addWidget(self.model_label, 1, 1)

        # IMEI
        module_layout.addWidget(QLabel("IMEI:"), 2, 0)
        self.imei_label = QLabel("Not available")
        module_layout.addWidget(self.imei_label, 2, 1)

        # Firmware
        module_layout.addWidget(QLabel("Firmware:"), 3, 0)
        self.firmware_label = QLabel("Not available")
        module_layout.addWidget(self.firmware_label, 3, 1)

        # Phone number
        module_layout.addWidget(QLabel("Phone Number:"), 0, 2)
        self.phone_number_label = QLabel("Not available")
        module_layout.addWidget(self.phone_number_label, 0, 3)

        # Carrier
        module_layout.addWidget(QLabel("Carrier:"), 1, 2)
        self.carrier_label = QLabel("Not available")
        module_layout.addWidget(self.carrier_label, 1, 3)

        # Network type
        module_layout.addWidget(QLabel("Network Type:"), 2, 2)
        self.network_type_label = QLabel("Not available")
        module_layout.addWidget(self.network_type_label, 2, 3)

        # Signal strength
        module_layout.addWidget(QLabel("Signal Strength:"), 3, 2)
        self.signal_strength_label = QLabel("Not available")
        module_layout.addWidget(self.signal_strength_label, 3, 3)

        # Refresh button
        self.refresh_info_button = QPushButton("Refresh Information")
        self.refresh_info_button.clicked.connect(self.refresh_module_info)
        self.refresh_info_button.setEnabled(False)
        module_layout.addWidget(self.refresh_info_button, 4, 0, 1, 4)

        module_group.setLayout(module_layout)
        main_layout.addWidget(module_group)

        # AT Command console
        console_group = QGroupBox("AT Command Console")
        console_layout = QVBoxLayout()

        # Command input
        command_layout = QHBoxLayout()
        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("Enter AT command")
        command_layout.addWidget(self.command_input)

        self.send_command_button = QPushButton("Send")
        self.send_command_button.clicked.connect(self.on_send_command_button_clicked)
        self.send_command_button.setEnabled(False)
        command_layout.addWidget(self.send_command_button)

        console_layout.addLayout(command_layout)

        # Response display
        console_layout.addWidget(QLabel("Response:"))
        self.response_display = QTextEdit()
        self.response_display.setReadOnly(True)
        console_layout.addWidget(self.response_display)

        console_group.setLayout(console_layout)
        main_layout.addWidget(console_group)

        # Status display
        self.status_display = QTextEdit()
        self.status_display.setReadOnly(True)
        self.status_display.setMaximumHeight(100)
        main_layout.addWidget(QLabel("Status:"))
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
        self.manufacturer_label.setText("Not available")
        self.model_label.setText("Not available")
        self.imei_label.setText("Not available")
        self.firmware_label.setText("Not available")
        self.phone_number_label.setText("Not available")
        self.carrier_label.setText("Not available")
        self.network_type_label.setText("Not available")
        self.signal_strength_label.setText("Not available")

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
        """Refresh module information"""
        if not self.lte_manager.is_connected():
            return

        # Get module information
        module_info = self.lte_manager.get_module_info()

        # Update labels
        self.manufacturer_label.setText(module_info.get('manufacturer', 'Not available'))
        self.model_label.setText(module_info.get('model', 'Not available'))
        self.imei_label.setText(module_info.get('imei', 'Not available'))
        self.firmware_label.setText(module_info.get('firmware', 'Not available'))
        self.phone_number_label.setText(module_info.get('phone_number', 'Not available'))
        self.carrier_label.setText(module_info.get('carrier', 'Not available'))
        self.network_type_label.setText(module_info.get('network_type', 'Not available'))
        self.signal_strength_label.setText(module_info.get('signal_strength', 'Not available'))

        self.add_status_message("Module information refreshed")

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
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                            QLineEdit, QTextEdit, QGroupBox, QTabWidget, QListWidget,
                            QListWidgetItem, QMessageBox, QSplitter, QComboBox,
                            QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy)
from PyQt5.QtCore import Qt, pyqtSlot, QDateTime, QSize

class PhoneSmsTab(QWidget):
    def __init__(self, lte_manager, database, sound_manager):
        super().__init__()
        self.lte_manager = lte_manager
        self.database = database
        self.sound_manager = sound_manager

        # Connect signals
        self.lte_manager.call_received.connect(self.on_call_received)
        self.lte_manager.call_ended.connect(self.on_call_ended)
        self.lte_manager.sms_received.connect(self.on_sms_received)
        self.lte_manager.dtmf_received.connect(self.on_dtmf_received)
        self.lte_manager.status_changed.connect(self.on_status_changed)

        self.init_ui()

    def init_ui(self):
        # Main layout
        main_layout = QVBoxLayout(self)

        # Create inner tab widget for phone and SMS
        inner_tab_widget = QTabWidget()
        main_layout.addWidget(inner_tab_widget)

        # Phone tab
        phone_widget = QWidget()
        phone_layout = QVBoxLayout(phone_widget)

        # Create a splitter for phone controls and call history
        phone_splitter = QSplitter(Qt.Vertical)
        phone_layout.addWidget(phone_splitter)

        # Top widget for phone controls
        phone_top_widget = QWidget()
        phone_top_layout = QVBoxLayout(phone_top_widget)

        # Phone controls
        phone_group = QGroupBox("Phone Controls")
        phone_controls_layout = QVBoxLayout()

        # Number input
        number_layout = QHBoxLayout()
        number_layout.addWidget(QLabel("Phone Number:"))
        self.phone_number_input = QLineEdit()
        self.phone_number_input.setPlaceholderText("Enter phone number")
        number_layout.addWidget(self.phone_number_input)
        phone_controls_layout.addLayout(number_layout)

        # Call buttons
        call_buttons_layout = QHBoxLayout()
        self.call_button = QPushButton("Call")
        self.call_button.clicked.connect(self.on_call_button_clicked)
        call_buttons_layout.addWidget(self.call_button)

        self.answer_button = QPushButton("Answer")
        self.answer_button.clicked.connect(self.on_answer_button_clicked)
        self.answer_button.setEnabled(False)
        call_buttons_layout.addWidget(self.answer_button)

        self.hangup_button = QPushButton("Hang Up")
        self.hangup_button.clicked.connect(self.on_hangup_button_clicked)
        self.hangup_button.setEnabled(False)
        call_buttons_layout.addWidget(self.hangup_button)

        phone_controls_layout.addLayout(call_buttons_layout)
        phone_group.setLayout(phone_controls_layout)
        phone_top_layout.addWidget(phone_group)

        # DTMF tones received
        dtmf_group = QGroupBox("DTMF Tones")
        dtmf_layout = QVBoxLayout()
        self.dtmf_display = QLineEdit()
        self.dtmf_display.setReadOnly(True)
        dtmf_layout.addWidget(self.dtmf_display)
        dtmf_group.setLayout(dtmf_layout)
        phone_top_layout.addWidget(dtmf_group)

        # Add phone top widget to splitter
        phone_splitter.addWidget(phone_top_widget)

        # Bottom widget for call history
        phone_bottom_widget = QWidget()
        phone_bottom_layout = QVBoxLayout(phone_bottom_widget)

        # Call log
        call_log_group = QGroupBox("Call History")
        call_log_layout = QVBoxLayout()

        # Call log controls
        call_log_controls = QHBoxLayout()
        self.refresh_call_log_button = QPushButton("Refresh")
        self.refresh_call_log_button.clicked.connect(self.refresh_call_log)
        call_log_controls.addWidget(self.refresh_call_log_button)

        self.clear_call_log_button = QPushButton("Clear Selected")
        self.clear_call_log_button.clicked.connect(self.clear_selected_call)
        call_log_controls.addWidget(self.clear_call_log_button)

        call_log_layout.addLayout(call_log_controls)

        # Call log table
        self.call_log_table = QTableWidget()
        self.call_log_table.setColumnCount(4)
        self.call_log_table.setHorizontalHeaderLabels(["Time", "Number", "Type", "Duration"])
        self.call_log_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.call_log_table.setMinimumHeight(200)  # Set minimum height
        call_log_layout.addWidget(self.call_log_table)

        call_log_group.setLayout(call_log_layout)
        phone_bottom_layout.addWidget(call_log_group)

        # Add phone bottom widget to splitter
        phone_splitter.addWidget(phone_bottom_widget)

        # Set initial sizes for splitter
        phone_splitter.setSizes([200, 400])

        # SMS tab
        sms_widget = QWidget()
        sms_layout = QVBoxLayout(sms_widget)

        # Create a splitter for SMS tab
        sms_splitter = QSplitter(Qt.Vertical)
        sms_layout.addWidget(sms_splitter)

        # Top widget for SMS sending
        sms_top_widget = QWidget()
        sms_top_layout = QVBoxLayout(sms_top_widget)

        # SMS controls
        sms_group = QGroupBox("Send SMS")
        sms_controls_layout = QVBoxLayout()

        # Number input
        sms_number_layout = QHBoxLayout()
        sms_number_layout.addWidget(QLabel("To:"))
        self.sms_number_input = QLineEdit()
        self.sms_number_input.setPlaceholderText("Enter recipient number")
        sms_number_layout.addWidget(self.sms_number_input)
        sms_controls_layout.addLayout(sms_number_layout)

        # Message input
        sms_controls_layout.addWidget(QLabel("Message:"))
        self.sms_message_input = QTextEdit()
        self.sms_message_input.setPlaceholderText("Type your message here")
        self.sms_message_input.setMinimumHeight(100)
        sms_controls_layout.addWidget(self.sms_message_input)

        # Send button
        self.send_sms_button = QPushButton("Send SMS")
        self.send_sms_button.clicked.connect(self.on_send_sms_button_clicked)
        sms_controls_layout.addWidget(self.send_sms_button)

        sms_group.setLayout(sms_controls_layout)
        sms_top_layout.addWidget(sms_group)

        # Add SMS top widget to splitter
        sms_splitter.addWidget(sms_top_widget)

        # Middle widget for SMS inbox
        sms_middle_widget = QWidget()
        sms_middle_layout = QVBoxLayout(sms_middle_widget)

        # SMS inbox
        sms_inbox_group = QGroupBox("SMS Messages")
        sms_inbox_layout = QVBoxLayout()

        # SMS list and controls
        sms_list_controls = QHBoxLayout()
        self.sms_type_combo = QComboBox()
        self.sms_type_combo.addItems(["All", "Unread", "Read", "Sent", "Unsent"])
        sms_list_controls.addWidget(QLabel("Show:"))
        sms_list_controls.addWidget(self.sms_type_combo)

        self.refresh_sms_button = QPushButton("Refresh")
        self.refresh_sms_button.clicked.connect(self.refresh_sms_list)
        sms_list_controls.addWidget(self.refresh_sms_button)

        self.delete_sms_button = QPushButton("Delete Selected")
        self.delete_sms_button.clicked.connect(self.delete_selected_sms)
        sms_list_controls.addWidget(self.delete_sms_button)

        sms_inbox_layout.addLayout(sms_list_controls)

        # Create a horizontal splitter for SMS list and content
        sms_content_splitter = QSplitter(Qt.Horizontal)

        # SMS list
        self.sms_list = QListWidget()
        self.sms_list.itemClicked.connect(self.on_sms_item_clicked)
        self.sms_list.setMinimumHeight(150)  # Set minimum height
        sms_content_splitter.addWidget(self.sms_list)

        # SMS content
        sms_content_widget = QWidget()
        sms_content_layout = QVBoxLayout(sms_content_widget)
        sms_content_layout.addWidget(QLabel("Message Content:"))
        self.sms_content = QTextEdit()
        self.sms_content.setReadOnly(True)
        sms_content_layout.addWidget(self.sms_content)
        sms_content_splitter.addWidget(sms_content_widget)

        # Set initial sizes for content splitter
        sms_content_splitter.setSizes([300, 300])

        sms_inbox_layout.addWidget(sms_content_splitter)
        sms_inbox_group.setLayout(sms_inbox_layout)
        sms_middle_layout.addWidget(sms_inbox_group)

        # Add SMS middle widget to splitter
        sms_splitter.addWidget(sms_middle_widget)

        # Bottom widget for SMS history
        sms_bottom_widget = QWidget()
        sms_bottom_layout = QVBoxLayout(sms_bottom_widget)

        # SMS history
        sms_history_group = QGroupBox("SMS History")
        sms_history_layout = QVBoxLayout()

        # SMS history controls
        sms_history_controls = QHBoxLayout()
        self.refresh_sms_history_button = QPushButton("Refresh History")
        self.refresh_sms_history_button.clicked.connect(self.refresh_sms_history)
        sms_history_controls.addWidget(self.refresh_sms_history_button)

        self.clear_sms_history_button = QPushButton("Clear Selected")
        self.clear_sms_history_button.clicked.connect(self.clear_selected_sms_history)
        sms_history_controls.addWidget(self.clear_sms_history_button)

        sms_history_layout.addLayout(sms_history_controls)

        # SMS history table
        self.sms_history_table = QTableWidget()
        self.sms_history_table.setColumnCount(4)
        self.sms_history_table.setHorizontalHeaderLabels(["Time", "Number", "Type", "Message"])
        self.sms_history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.sms_history_table.itemClicked.connect(self.on_sms_history_item_clicked)
        self.sms_history_table.setMinimumHeight(150)  # Set minimum height
        sms_history_layout.addWidget(self.sms_history_table)

        sms_history_group.setLayout(sms_history_layout)
        sms_bottom_layout.addWidget(sms_history_group)

        # Add SMS bottom widget to splitter
        sms_splitter.addWidget(sms_bottom_widget)

        # Set initial sizes for SMS splitter
        sms_splitter.setSizes([200, 300, 300])

        # Add tabs to inner tab widget
        inner_tab_widget.addTab(phone_widget, "Phone")
        inner_tab_widget.addTab(sms_widget, "SMS")

        # Status display
        self.status_display = QTextEdit()
        self.status_display.setReadOnly(True)
        self.status_display.setMaximumHeight(100)
        main_layout.addWidget(QLabel("Status:"))
        main_layout.addWidget(self.status_display)

        # Load initial data
        self.refresh_call_log()
        self.refresh_sms_history()

    def on_call_button_clicked(self):
        """Handle call button click"""
        number = self.phone_number_input.text().strip()
        if not number:
            QMessageBox.warning(self, "Input Error", "Please enter a phone number")
            return

        if self.lte_manager.make_call(number):
            self.call_button.setEnabled(False)
            self.answer_button.setEnabled(False)
            self.hangup_button.setEnabled(True)
            self.add_to_call_log(f"Outgoing call to {number}")

            # Add to database
            self.database.add_call(number, "outgoing")
        else:
            QMessageBox.warning(self, "Call Error", "Failed to make call")
            self.sound_manager.play_error()

    def on_answer_button_clicked(self):
        """Handle answer button click"""
        if self.lte_manager.answer_call():
            self.call_button.setEnabled(False)
            self.answer_button.setEnabled(False)
            self.hangup_button.setEnabled(True)
            self.add_to_call_log(f"Answered call from {self.lte_manager.call_number}")

            # Stop ringtone
            self.sound_manager.stop_ringtone()

            # Add to database
            self.database.add_call(self.lte_manager.call_number, "incoming")
        else:
            QMessageBox.warning(self, "Call Error", "Failed to answer call")
            self.sound_manager.play_error()

    def on_hangup_button_clicked(self):
        """Handle hangup button click"""
        if self.lte_manager.end_call():
            self.call_button.setEnabled(True)
            self.answer_button.setEnabled(False)
            self.hangup_button.setEnabled(False)
            self.add_to_call_log("Call ended")

            # Play call end sound
            self.sound_manager.play_call_end()
        else:
            QMessageBox.warning(self, "Call Error", "Failed to end call")
            self.sound_manager.play_error()

    def on_send_sms_button_clicked(self):
        """Handle send SMS button click"""
        number = self.sms_number_input.text().strip()
        message = self.sms_message_input.toPlainText().strip()

        if not number:
            QMessageBox.warning(self, "Input Error", "Please enter a recipient number")
            return

        if not message:
            QMessageBox.warning(self, "Input Error", "Please enter a message")
            return

        if self.lte_manager.send_sms(number, message):
            self.sms_message_input.clear()
            self.add_status_message(f"SMS sent to {number}")

            # Play success sound
            self.sound_manager.play_success()

            # Add to database
            self.database.add_sms(number, message, "outgoing", "sent")

            # Refresh SMS list and history
            self.refresh_sms_list()
            self.refresh_sms_history()
        else:
            QMessageBox.warning(self, "SMS Error", "Failed to send SMS")

            # Play error sound
            self.sound_manager.play_error()

            # Add to database as failed
            self.database.add_sms(number, message, "outgoing", "failed")

    def on_call_received(self, number):
        """Handle incoming call"""
        self.call_button.setEnabled(False)
        self.answer_button.setEnabled(True)
        self.hangup_button.setEnabled(True)
        self.add_to_call_log(f"Incoming call from {number}")

        # Play ringtone
        self.sound_manager.play_ringtone()

    def on_call_ended(self, duration):
        """Handle call ended"""
        self.call_button.setEnabled(True)
        self.answer_button.setEnabled(False)
        self.hangup_button.setEnabled(False)
        self.add_to_call_log(f"Call ended: {duration}")
        self.dtmf_display.clear()

        # Stop ringtone if still ringing
        self.sound_manager.stop_ringtone()

        # Play call end sound
        self.sound_manager.play_call_end()

        # Update call duration in database
        # This is a simplification - in a real app, you'd need to match the call
        if self.lte_manager.call_number:
            try:
                duration_seconds = int(duration) if duration != "Missed" else 0
            except:
                duration_seconds = 0

            # For missed calls, add a new record
            if duration == "Missed":
                self.database.add_call(self.lte_manager.call_number, "missed", 0)
            else:
                # Update the most recent call with this number
                # In a real app, you'd use a call ID to match exactly
                calls = self.database.get_call_history(limit=1, phone_number=self.lte_manager.call_number)
                if calls:
                    call_id = calls[0][0]  # First column is ID
                    self.database.cursor.execute(
                        "UPDATE call_history SET duration = ? WHERE id = ?",
                        (duration_seconds, call_id)
                    )
                    self.database.conn.commit()

        # Refresh call log
        self.refresh_call_log()

    def on_sms_received(self, sender, timestamp, message):
        """Handle SMS received"""
        self.add_status_message(f"SMS received from {sender}")

        # Play message received sound - play three beeps
        self.sound_manager.play_message_received()
        self.sound_manager.play_message_received()
        self.sound_manager.play_message_received()

        # Add to database
        self.database.add_sms(sender, message, "incoming", "received")

        # Refresh SMS list and history
        self.refresh_sms_list()
        self.refresh_sms_history()

        # Update the SMS content display directly
        self.sms_content.setText(f"From: {sender}\nTime: {timestamp}\n\n{message}")

        # Show a message box to alert the user
        QMessageBox.information(self, "New SMS", f"New message from {sender}\n\n{message[:100]}" + ("..." if len(message) > 100 else ""))

    def on_dtmf_received(self, tone):
        """Handle DTMF tone received"""
        current_text = self.dtmf_display.text()
        self.dtmf_display.setText(current_text + tone)

    def on_status_changed(self, status):
        """Handle status change"""
        self.add_status_message(status)

    def add_to_call_log(self, message):
        """Add message to status display"""
        timestamp = QDateTime.currentDateTime().toString("yyyy-MM-dd hh:mm:ss")
        self.status_display.append(f"{timestamp} - {message}")
        self.status_display.ensureCursorVisible()

    def add_status_message(self, message):
        """Add message to status display"""
        timestamp = QDateTime.currentDateTime().toString("yyyy-MM-dd hh:mm:ss")
        self.status_display.append(f"{timestamp} - {message}")
        self.status_display.ensureCursorVisible()

    def refresh_sms_list(self):
        """Refresh SMS list from module"""
        if not self.lte_manager.is_connected():
            return

        self.sms_list.clear()
        self.sms_content.clear()

        # Get SMS type filter
        sms_type = self.sms_type_combo.currentText()
        if sms_type == "All":
            status = "ALL"
        elif sms_type == "Unread":
            status = "REC UNREAD"
        elif sms_type == "Read":
            status = "REC READ"
        elif sms_type == "Sent":
            status = "STO SENT"
        elif sms_type == "Unsent":
            status = "STO UNSENT"

        # Get SMS list
        messages = self.lte_manager.get_sms_list(status)

        # Add messages to list
        for msg in messages:
            item = QListWidgetItem(f"{msg['index']} - From: {msg['sender']} - {msg['timestamp']}")
            item.setData(Qt.UserRole, msg)
            self.sms_list.addItem(item)

        # If no messages from module, show a message
        if self.sms_list.count() == 0:
            self.add_status_message("No messages found on the module. Check SMS history tab for stored messages.")

            # Try to get messages from database to show in the content area
            db_messages = self.database.get_sms_history(limit=1)
            if db_messages:
                # Format: id, phone_number, message, sms_type, timestamp, status
                _, _, message, _, _, _ = db_messages[0]
                self.sms_content.setText("Last message from database:\n\n" + message)

    def on_sms_item_clicked(self, item):
        """Handle SMS item click"""
        msg = item.data(Qt.UserRole)
        if msg:
            self.sms_content.setText(msg['content'])

    def delete_selected_sms(self):
        """Delete selected SMS from module"""
        selected_items = self.sms_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Selection Error", "Please select an SMS to delete")
            return

        for item in selected_items:
            msg = item.data(Qt.UserRole)
            if msg:
                if self.lte_manager.delete_sms(msg['index']):
                    self.add_status_message(f"Deleted SMS at index {msg['index']}")
                else:
                    self.add_status_message(f"Failed to delete SMS at index {msg['index']}")

        self.refresh_sms_list()

    def refresh_call_log(self):
        """Refresh call log from database"""
        # Get call history from database
        calls = self.database.get_call_history()

        # Clear table
        self.call_log_table.setRowCount(0)

        # Add calls to table
        for call in calls:
            row = self.call_log_table.rowCount()
            self.call_log_table.insertRow(row)

            # Format: id, phone_number, call_type, duration, timestamp, notes
            call_id, phone_number, call_type, duration, timestamp, notes = call

            # Format duration
            if duration:
                duration_str = f"{duration}s"
            else:
                duration_str = ""

            # Add items to row
            self.call_log_table.setItem(row, 0, QTableWidgetItem(timestamp))
            self.call_log_table.setItem(row, 1, QTableWidgetItem(phone_number))
            self.call_log_table.setItem(row, 2, QTableWidgetItem(call_type))
            self.call_log_table.setItem(row, 3, QTableWidgetItem(duration_str))

            # Store call ID in first column
            self.call_log_table.item(row, 0).setData(Qt.UserRole, call_id)

    def clear_selected_call(self):
        """Clear selected call from database"""
        selected_items = self.call_log_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Selection Error", "Please select a call to delete")
            return

        # Get unique rows
        rows = set()
        for item in selected_items:
            rows.add(item.row())

        # Delete each selected call
        for row in rows:
            call_id = self.call_log_table.item(row, 0).data(Qt.UserRole)
            if self.database.delete_call(call_id):
                self.add_status_message(f"Deleted call record {call_id}")
            else:
                self.add_status_message(f"Failed to delete call record {call_id}")

        # Refresh call log
        self.refresh_call_log()

    def refresh_sms_history(self):
        """Refresh SMS history from database"""
        # Get SMS history from database
        messages = self.database.get_sms_history()

        # Clear table
        self.sms_history_table.setRowCount(0)

        # Add messages to table
        for msg in messages:
            row = self.sms_history_table.rowCount()
            self.sms_history_table.insertRow(row)

            # Format: id, phone_number, message, sms_type, timestamp, status
            sms_id, phone_number, message, sms_type, timestamp, status = msg

            # Add items to row
            self.sms_history_table.setItem(row, 0, QTableWidgetItem(timestamp))
            self.sms_history_table.setItem(row, 1, QTableWidgetItem(phone_number))
            self.sms_history_table.setItem(row, 2, QTableWidgetItem(f"{sms_type} ({status})"))

            # Truncate message if too long
            if len(message) > 50:
                display_message = message[:47] + "..."
            else:
                display_message = message

            self.sms_history_table.setItem(row, 3, QTableWidgetItem(display_message))

            # Store full message and SMS ID
            self.sms_history_table.item(row, 3).setData(Qt.UserRole, message)
            self.sms_history_table.item(row, 0).setData(Qt.UserRole, sms_id)

    def on_sms_history_item_clicked(self, item):
        """Handle SMS history item click"""
        # If clicked on message column, show full message
        if item.column() == 3:
            full_message = item.data(Qt.UserRole)
            if full_message:
                self.sms_content.setText(full_message)

    def clear_selected_sms_history(self):
        """Clear selected SMS from history database"""
        selected_items = self.sms_history_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Selection Error", "Please select an SMS to delete")
            return

        # Get unique rows
        rows = set()
        for item in selected_items:
            rows.add(item.row())

        # Delete each selected SMS
        for row in rows:
            sms_id = self.sms_history_table.item(row, 0).data(Qt.UserRole)
            if self.database.delete_sms(sms_id):
                self.add_status_message(f"Deleted SMS record {sms_id}")
            else:
                self.add_status_message(f"Failed to delete SMS record {sms_id}")

        # Refresh SMS history
        self.refresh_sms_history()
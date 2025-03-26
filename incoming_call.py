import sys
from PyQt5.QtWidgets import QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QSize
from PyQt5.QtGui import QPixmap, QIcon
import winsound
import threading
import time
import os

class IncomingCallDialog(QDialog):
    # 定义信号
    answer_signal = pyqtSignal()
    reject_signal = pyqtSignal()

    def __init__(self, phone_number, caller_name=None, parent=None):
        super().__init__(parent)
        self.phone_number = phone_number
        self.caller_name = caller_name or "未知联系人"
        self.display_name = caller_name or phone_number
        self.init_ui()
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        # 初始化时间计时器
        self.start_time = time.time()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_time)
        self.timer.start(500)  # 每500毫秒更新一次

        # 15秒后自动关闭对话框（如果未接听），模拟未接来电
        self.auto_close_timer = QTimer(self)
        self.auto_close_timer.timeout.connect(self.auto_reject)
        self.auto_close_timer.setSingleShot(True)
        self.auto_close_timer.start(15000)  # 15秒后自动关闭

        # 设置窗口标志
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)

        # 记录对话框状态
        self.answer_clicked = False
        self.reject_clicked = False

        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 来电对话框已创建: {self.display_name}")

    def init_ui(self):
        self.setWindowTitle("来电")
        self.setStyleSheet("""
            QDialog {
                background-color: #F5F5F5;
                border: 1px solid #CCCCCC;
                border-radius: 10px;
            }
            QLabel {
                color: #333333;
                font-size: 14px;
            }
            QPushButton {
                border-radius: 20px;
                font-size: 14px;
                font-weight: bold;
                padding: 10px;
            }
            QPushButton#answer {
                background-color: #4CAF50;
                color: white;
            }
            QPushButton#answer:hover {
                background-color: #45a049;
            }
            QPushButton#reject {
                background-color: #f44336;
                color: white;
            }
            QPushButton#reject:hover {
                background-color: #d32f2f;
            }
        """)

        # 主布局
        layout = QVBoxLayout(self)

        # 联系人头像
        avatar_label = QLabel()
        avatar_path = os.path.join("assets", "user.png")  # 默认头像
        if os.path.exists(avatar_path):
            pixmap = QPixmap(avatar_path)
            avatar_label.setPixmap(pixmap.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        avatar_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(avatar_label)

        # 显示标题（来电）
        title_label = QLabel("来电")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #333333;")
        layout.addWidget(title_label)

        # 显示来电号码或联系人名称
        self.name_label = QLabel(self.display_name)
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setStyleSheet("font-size: 16px; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(self.name_label)

        # 显示号码（如果有联系人名称）
        if self.caller_name:
            number_label = QLabel(self.phone_number)
            number_label.setAlignment(Qt.AlignCenter)
            number_label.setStyleSheet("font-size: 12px; color: #555555;")
            layout.addWidget(number_label)

        # 显示振铃时间
        self.time_label = QLabel("正在响铃...")
        self.time_label.setAlignment(Qt.AlignCenter)
        self.time_label.setStyleSheet("font-size: 12px; color: #555555; margin-top: 5px;")
        layout.addWidget(self.time_label)

        # 按钮布局
        button_layout = QHBoxLayout()

        # 接听按钮
        self.answer_button = QPushButton()
        self.answer_button.setIcon(QIcon(os.path.join("assets", "answer.png")))
        self.answer_button.setIconSize(QSize(30, 30))
        self.answer_button.setObjectName("answer")
        self.answer_button.setFixedSize(60, 60)
        self.answer_button.clicked.connect(self.accept_call)
        button_layout.addWidget(self.answer_button)

        # 拒绝按钮
        self.reject_button = QPushButton()
        self.reject_button.setIcon(QIcon(os.path.join("assets", "hangup.png")))
        self.reject_button.setIconSize(QSize(30, 30))
        self.reject_button.setObjectName("reject")
        self.reject_button.setFixedSize(60, 60)
        self.reject_button.clicked.connect(self.reject_call)
        button_layout.addWidget(self.reject_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)
        self.setFixedSize(300, 350)

        # 将对话框移到屏幕中央
        screen_geometry = self.screen().availableGeometry()
        x = (screen_geometry.width() - self.width()) // 2
        y = (screen_geometry.height() - self.height()) // 2
        self.move(x, y)

    def update_time(self):
        """更新来电持续时间"""
        elapsed = int(time.time() - self.start_time)
        minutes, seconds = divmod(elapsed, 60)
        self.time_label.setText(f"已振铃 {minutes:02d}:{seconds:02d}")

    def accept_call(self):
        """处理接听按钮点击"""
        if self.answer_clicked:  # 防止重复点击
            return

        self.answer_clicked = True
        self.auto_close_timer.stop()  # 停止自动拒绝计时器
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 用户点击接听按钮: {self.display_name}")

        # 发送接听信号
        self.answer_signal.emit()
        self.timer.stop()
        self.accept()  # 关闭对话框

    def reject_call(self):
        """处理拒绝按钮点击"""
        if self.reject_clicked:  # 防止重复点击
            return

        self.reject_clicked = True
        self.auto_close_timer.stop()  # 停止自动拒绝计时器
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 用户点击拒绝按钮: {self.display_name}")

        # 发送拒绝信号
        self.reject_signal.emit()
        self.timer.stop()
        self.reject()  # 关闭对话框

    def auto_reject(self):
        """自动拒绝来电（未接听超时）"""
        if not self.answer_clicked and not self.reject_clicked:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 来电自动拒绝(超时): {self.display_name}")
            self.reject_clicked = True

            # 发送拒绝信号
            self.reject_signal.emit()
            self.timer.stop()
            self.reject()  # 关闭对话框

    def closeEvent(self, event):
        """处理对话框关闭事件"""
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 来电对话框关闭: {self.display_name}")
        self.timer.stop()
        self.auto_close_timer.stop()

        # 如果是通过"X"按钮关闭的，没有点击任何按钮，则视为拒绝
        if not self.answer_clicked and not self.reject_clicked:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 窗口被直接关闭，视为拒绝")
            self.reject_signal.emit()

        super().closeEvent(event)

def show_incoming_call(caller_number):
    """显示来电对话框并返回用户选择（True表示接听，False表示拒绝）"""
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

    # 确保没有遗留的来电对话框
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, IncomingCallDialog):
            print(f"发现正在显示的来电对话框，关闭它: {widget.display_name}")
            widget.close()
            widget.deleteLater()

    # 创建并显示新的来电对话框
    dialog = IncomingCallDialog(caller_number)
    result = dialog.exec_()

    # 记录用户选择
    user_choice = dialog.answer_clicked

    # 确保对话框被释放
    dialog.deleteLater()

    return user_choice

if __name__ == "__main__":
    # 测试来电对话框
    result = show_incoming_call("+8613800138000")
    print(f"用户选择了{'接听' if result else '拒绝'}来电")
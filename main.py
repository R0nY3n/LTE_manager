import sys
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QTabWidget, QWidget,
                            QVBoxLayout, QHBoxLayout, QLabel, QStatusBar, QMessageBox,
                            QSystemTrayIcon, QMenu, QAction)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor

from phone_sms_tab import PhoneSmsTab
from settings_tab import SettingsTab
from lte_manager import LTEManager
from database import LTEDatabase
from sound_utils import SoundManager

class LTEToolApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LTE Tool")
        self.resize(800, 600)

        # 加载图标文件
        self.load_icons()

        # 设置应用图标
        self.setWindowIcon(self.app_icon)

        # 创建系统托盘图标
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.app_icon)  # 初始使用应用图标
        self.tray_icon.setToolTip("LTE Tool - 未连接")

        # 设置系统托盘菜单和显示图标
        self.setup_tray_icon()

        # 打印系统托盘状态信息
        print("系统托盘可用:", QSystemTrayIcon.isSystemTrayAvailable())
        print("托盘图标可见:", self.tray_icon.isVisible())

        # 创建 LTE 管理器
        self.lte_manager = LTEManager()

        # 创建数据库
        self.database = LTEDatabase()

        # 创建声音管理器
        self.sound_manager = SoundManager()

        # 创建主窗口部件和布局
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)

        # 创建标签页部件
        self.tab_widget = QTabWidget()
        self.main_layout.addWidget(self.tab_widget)

        # 创建标签页
        self.phone_sms_tab = PhoneSmsTab(self.lte_manager, self.database, self.sound_manager)
        self.settings_tab = SettingsTab(self.lte_manager)

        # 添加标签页
        self.tab_widget.addTab(self.phone_sms_tab, "电话和短信")
        self.tab_widget.addTab(self.settings_tab, "设置")

        # 创建状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # 状态栏部件
        self.carrier_label = QLabel("运营商: 未连接")
        self.phone_number_label = QLabel("电话: 不可用")
        self.network_label = QLabel("网络: 未连接")
        self.signal_label = QLabel("信号: 不可用")

        # 添加部件到状态栏
        self.status_bar.addWidget(self.carrier_label)
        self.status_bar.addWidget(self.phone_number_label)
        self.status_bar.addWidget(self.network_label)
        self.status_bar.addWidget(self.signal_label)

        # 现在可以安全地更新连接状态（初始为未连接）
        self.update_connection_status(False)

        # 更新状态计时器
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status_bar)
        self.status_timer.start(5000)  # 每5秒更新一次

        # 连接信号
        self.lte_manager.status_changed.connect(self.on_status_changed)

        # 连接短信接收信号以显示通知
        self.lte_manager.sms_received.connect(self.on_sms_received_notification)
        self.lte_manager.call_received.connect(self.on_call_received_notification)

        # 尝试自动连接（如果启用）
        QTimer.singleShot(1000, self.try_auto_connect)

    def try_auto_connect(self):
        """尝试自动连接到LTE模块"""
        # 调用设置标签页的自动连接方法
        if hasattr(self, 'settings_tab') and self.settings_tab:
            self.settings_tab.try_auto_connect()

    def load_icons(self):
        """加载应用图标和状态图标"""
        # 默认图标 - 创建彩色图标作为备用
        default_pixmap = QPixmap(32, 32)
        default_pixmap.fill(Qt.blue)
        self.app_icon = QIcon(default_pixmap)

        # 连接状态图标 - 绿色指示器
        connected_pixmap = QPixmap(16, 16)
        connected_pixmap.fill(Qt.transparent)
        painter = QPainter(connected_pixmap)
        painter.setPen(Qt.green)
        painter.setBrush(QColor(0, 255, 0, 180))  # 半透明绿色
        painter.drawEllipse(2, 2, 12, 12)
        painter.end()
        self.connected_indicator = QIcon(connected_pixmap)

        # 未连接状态图标 - 红色指示器
        disconnected_pixmap = QPixmap(16, 16)
        disconnected_pixmap.fill(Qt.transparent)
        painter = QPainter(disconnected_pixmap)
        painter.setPen(Qt.red)
        painter.setBrush(QColor(255, 0, 0, 180))  # 半透明红色
        painter.drawEllipse(2, 2, 12, 12)
        painter.end()
        self.disconnected_indicator = QIcon(disconnected_pixmap)

        # 尝试加载实际图标文件
        try:
            # 首先尝试当前目录
            icon_path = os.path.abspath("phone_done.png")
            if os.path.exists(icon_path):
                self.app_icon = QIcon(icon_path)
                print(f"成功加载图标: {icon_path}")
            else:
                # 尝试在可执行文件目录中查找
                base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
                icon_path = os.path.join(base_dir, "phone_done.png")
                if os.path.exists(icon_path):
                    self.app_icon = QIcon(icon_path)
                    print(f"成功加载图标: {icon_path}")
                else:
                    print(f"警告: 未找到图标文件，使用默认蓝色图标")
        except Exception as e:
            print(f"加载图标时出错: {str(e)}")

    def setup_tray_icon(self):
        """设置系统托盘图标和菜单"""
        # 创建托盘菜单
        tray_menu = QMenu()

        # 添加操作
        show_action = QAction("显示", self)
        show_action.triggered.connect(self.show)
        tray_menu.addAction(show_action)

        hide_action = QAction("隐藏", self)
        hide_action.triggered.connect(self.hide)
        tray_menu.addAction(hide_action)

        tray_menu.addSeparator()

        # 连接状态操作（不可点击）
        self.connection_status_action = QAction("未连接", self)
        self.connection_status_action.setEnabled(False)
        tray_menu.addAction(self.connection_status_action)

        tray_menu.addSeparator()

        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.close)
        tray_menu.addAction(exit_action)

        # 设置托盘菜单
        self.tray_icon.setContextMenu(tray_menu)

        # 连接信号
        self.tray_icon.activated.connect(self.on_tray_icon_activated)

        # 显示托盘图标
        self.tray_icon.show()

    def update_connection_status(self, connected):
        """更新托盘图标中的连接状态"""
        if connected:
            # 使用应用图标，但在工具提示中显示连接状态
            self.tray_icon.setIcon(self.app_icon)
            self.tray_icon.setToolTip("LTE Tool - 已连接")
            self.connection_status_action.setText("已连接")
            # 在状态栏显示连接指示器
            if hasattr(self, 'status_bar'):
                self.status_bar.setStyleSheet("QStatusBar { background-color: rgba(0, 255, 0, 30); }")
        else:
            # 使用应用图标，但在工具提示中显示未连接状态
            self.tray_icon.setIcon(self.app_icon)
            self.tray_icon.setToolTip("LTE Tool - 未连接")
            self.connection_status_action.setText("未连接")
            # 在状态栏显示未连接指示器
            if hasattr(self, 'status_bar'):
                self.status_bar.setStyleSheet("QStatusBar { background-color: rgba(255, 0, 0, 30); }")

    def on_tray_icon_activated(self, reason):
        """处理托盘图标激活"""
        if reason == QSystemTrayIcon.DoubleClick:
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.activateWindow()

    def on_sms_received_notification(self, sender, timestamp, message):
        """收到短信时显示通知"""
        if self.tray_icon.isVisible():
            # 如果消息太长则截断
            display_message = message[:50] + "..." if len(message) > 50 else message
            self.tray_icon.showMessage(
                "新短信",
                f"发件人: {sender}\n{display_message}",
                QSystemTrayIcon.Information,
                5000  # 显示5秒
            )

    def on_call_received_notification(self, number):
        """收到来电时显示通知"""
        if self.tray_icon.isVisible():
            self.tray_icon.showMessage(
                "来电",
                f"来自: {number}",
                QSystemTrayIcon.Information,
                5000  # 显示5秒
            )

    def update_status_bar(self):
        """更新状态栏显示当前LTE模块信息"""
        is_connected = self.lte_manager.is_connected()
        self.update_connection_status(is_connected)

        if is_connected:
            # 更新运营商信息
            carrier_info = self.lte_manager.get_carrier_info()
            if carrier_info:
                self.carrier_label.setText(f"运营商: {carrier_info}")

            # 更新电话号码
            phone_number = self.lte_manager.get_phone_number()
            if phone_number:
                self.phone_number_label.setText(f"电话: {phone_number}")

            # 更新网络信息
            network_info = self.lte_manager.get_network_info()
            if network_info:
                self.network_label.setText(f"网络: {network_info}")

            # 更新信号强度
            signal_strength = self.lte_manager.get_signal_strength()
            if signal_strength:
                self.signal_label.setText(f"信号: {signal_strength}")
        else:
            self.carrier_label.setText("运营商: 未连接")
            self.phone_number_label.setText("电话: 不可用")
            self.network_label.setText("网络: 未连接")
            self.signal_label.setText("信号: 不可用")

    def on_status_changed(self, status):
        """处理状态变化"""
        # 在状态栏显示消息
        self.status_bar.showMessage(status, 5000)

        # 更新托盘图标中的连接状态
        if "Connected to LTE module" in status:
            self.update_connection_status(True)
        elif "Disconnected from LTE module" in status:
            self.update_connection_status(False)

    def closeEvent(self, event):
        """处理应用关闭事件"""
        # 停止所有声音
        self.sound_manager.stop_ringtone()

        # 关闭数据库连接
        self.database.close()

        # 断开LTE模块连接
        if self.lte_manager.is_connected():
            self.lte_manager.disconnect()

        # 移除托盘图标
        if self.tray_icon.isVisible():
            self.tray_icon.hide()

        # 接受关闭事件
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 窗口关闭时不退出应用，只有选择退出时才退出
    window = LTEToolApp()
    window.show()
    sys.exit(app.exec_())
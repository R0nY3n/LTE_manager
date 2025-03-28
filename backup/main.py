import sys
import os
import time
import threading
import re
from PyQt5.QtWidgets import (QApplication, QMainWindow, QTabWidget, QWidget,
                            QVBoxLayout, QHBoxLayout, QLabel, QStatusBar, QMessageBox,
                            QSystemTrayIcon, QMenu, QAction)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor
import traceback

# 使用PyInstaller打包时的资源文件注意事项:
# ----------------------------------
# 1. 图标文件应在spec文件中添加为附加数据:
#    a = Analysis(...,
#                datas=[
#                    ('default.png', '.'),
#                    ('running.png', '.'),
#                    ('error.png', '.')
#                ],
#                ...)
#
# 2. 数据库文件将自动保存在用户主目录的.LTE文件夹中
#
# 3. 其他资源文件也应通过datas参数添加，例如声音文件等

from phone_sms_tab import PhoneSmsTab
from settings_tab import SettingsTab
from lte_manager import LTEManager
from database import LTEDatabase
from sound_utils import SoundManager
from audio import PCMAudio
from ffmpeg_audio import FFmpegAudio  # 导入新的FFmpeg音频处理类
from incoming_call import show_incoming_call

class LTEToolApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LTE Tool")
        self.resize(800, 600)

        start_time = time.strftime('%Y-%m-%d %H:%M:%S')
        print(f"{start_time} - LTE Tool 应用程序启动")

        # 添加更新周期计数器，用于控制不同信息的更新频率
        # 初始化为1，确保首次连接时执行完整的信息获取
        self.update_counter = 1

        # 添加应用退出标志，用于区分最小化到托盘和退出程序
        self.is_exiting = False

        # 添加来电对话框标志，防止重复显示来电界面
        self.incoming_call_dialog_visible = False
        self.current_incoming_call_number = None
        self._incoming_call_dialog = None

        # 加载图标文件
        self.load_icons()

        # 设置应用图标
        self.setWindowIcon(self.default_icon)

        # 创建系统托盘图标
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.default_icon)  # 初始使用默认图标
        self.tray_icon.setToolTip("LTE Tool - 未连接")

        # 判断是否使用FFmpeg
        self.use_ffmpeg = False  # 设置为False，禁用所有音频处理

        # PCM音频处理器（已禁用）
        self.audio_processor = None
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 已禁用PCM音频处理")

        # 设置系统托盘菜单和显示图标
        self.setup_tray_icon()

        # 打印系统托盘状态信息
        print("系统托盘可用:", QSystemTrayIcon.isSystemTrayAvailable())
        print("托盘图标可见:", self.tray_icon.isVisible())

        # 创建 LTE 管理器
        self.lte_manager = LTEManager()

        # 创建数据库路径 - 使用用户主目录下的.LTE文件夹
        user_home = os.path.expanduser('~')
        lte_dir = os.path.join(user_home, '.LTE')
        if not os.path.exists(lte_dir):
            os.makedirs(lte_dir)
        db_path = os.path.join(lte_dir, 'lte_data.db')
        print(f"数据库路径: {db_path}")

        # 创建数据库
        self.database = LTEDatabase(db_path=db_path)

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

        # 创建GitHub链接标签页
        self.github_tab = QWidget()
        github_layout = QVBoxLayout(self.github_tab)

        # 添加GitHub链接标签
        github_label = QLabel("访问GitHub项目页面获取最新版本和更新：")
        github_label.setAlignment(Qt.AlignCenter)
        github_layout.addWidget(github_label)

        # 添加GitHub链接按钮
        github_link = QLabel('<a href="https://github.com/R0nY3n/LTE_manager">https://github.com/R0nY3n/LTE_manager</a>')
        github_link.setAlignment(Qt.AlignCenter)
        github_link.setOpenExternalLinks(True)  # 允许打开外部链接
        github_link.setTextInteractionFlags(Qt.TextBrowserInteraction)  # 允许文本交互
        github_layout.addWidget(github_link)

        # 添加说明文本
        info_label = QLabel("欢迎在GitHub上提交问题、建议或贡献代码！")
        info_label.setAlignment(Qt.AlignCenter)
        github_layout.addWidget(info_label)

        # 添加空白区域
        github_layout.addStretch()

        # 添加标签页
        self.tab_widget.addTab(self.phone_sms_tab, "电话和短信")
        self.tab_widget.addTab(self.settings_tab, "设置")
        self.tab_widget.addTab(self.github_tab, "GitHub")

        # 状态栏部件
        self.status_carrier = QLabel("运营商: 未连接")
        self.status_phone = QLabel("电话: 不可用")
        self.status_network = QLabel("网络: 未连接")
        self.status_signal = QLabel("信号: 不可用")
        self.audio_status_label = QLabel("音频: 未初始化")
        self.call_status_label = QLabel("通话: 无通话")  # 添加通话状态标签

        # 添加部件到状态栏
        self.statusBar().addWidget(self.status_carrier)
        self.statusBar().addWidget(self.status_phone)
        self.statusBar().addWidget(self.status_network)
        self.statusBar().addWidget(self.status_signal)
        self.statusBar().addWidget(self.audio_status_label)
        self.statusBar().addWidget(self.call_status_label)  # 添加到状态栏

        # 现在可以安全地更新连接状态（初始为未连接）
        self.update_connection_status(False)

        # 更新状态计时器 - 增加更长的更新间隔
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status_bar)
        self.status_timer.start(10000)  # 增加到10秒更新一次（从5秒改为10秒）

        # 通话状态检查计时器 - 修改为不定期检查模式
        self.call_status_timer = QTimer()
        self.call_status_timer.timeout.connect(self.check_call_status)
        # 不再固定间隔调用check_call_status
        # self.call_status_timer.start(1000)  # 每秒检查一次通话状态

        # 添加通话状态检查标志，用于控制何时检查通话状态
        self.should_check_call_status = False
        self.call_check_count = 0
        self.max_call_checks = 3  # 最多连续检查3次

        # 连接信号
        self.lte_manager.status_changed.connect(self.on_status_changed)

        # 连接短信接收信号以显示通知
        self.lte_manager.sms_received.connect(self.on_sms_received_notification)
        self.lte_manager.call_received.connect(self.on_call_received_notification)

        # 连接通话结束信号
        self.lte_manager.call_ended.connect(self.on_call_ended)

        # 连接PCM音频状态信号
        self.lte_manager.pcm_audio_status.connect(self.on_pcm_audio_status_changed)

        # 尝试自动连接（如果启用）
        QTimer.singleShot(1000, self.try_auto_connect)

    def initialize_audio_processor(self):
        """初始化PCM音频处理器（已禁用实际处理）"""
        try:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 音频处理已禁用，仅创建空壳")
            # 不再实际初始化音频处理器，但保留接口兼容性
            self.audio_processor = None
            self.audio_status_label.setText("音频: 已禁用")
        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 初始化音频处理器出错: {str(e)}")
            self.audio_processor = None
            # 确保异常处理中的状态更新也是安全的
            try:
                self.audio_status_label.setText("音频: 初始化失败")
            except:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 无法更新音频状态标签")

    def on_pcm_audio_status_changed(self, registered):
        """处理PCM音频注册状态变化"""
        try:
            # PCM音频已注册，只记录状态但不处理音频
            if registered:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频已注册，但不执行音频处理（已禁用）")
                self.audio_status_label.setText("音频: PCM已注册（处理已禁用）")
            else:
                # PCM音频已取消注册，只记录状态
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频已注销")
                self.audio_status_label.setText("音频: 非活动")
        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - PCM音频状态变化处理出错: {str(e)}")
            try:
                self.audio_status_label.setText("音频: 错误")
            except:
                pass

    def try_auto_connect(self):
        """尝试自动连接到LTE模块"""
        # 调用设置标签页的自动连接方法
        if hasattr(self, 'settings_tab') and self.settings_tab:
            self.settings_tab.try_auto_connect()

    def load_icons(self):
        """加载应用图标和状态图标"""
        # 获取应用程序资源目录
        if getattr(sys, 'frozen', False):
            # 如果是打包后的可执行文件
            base_dir = os.path.dirname(sys.executable)
            # 创建临时资源目录用于PyInstaller
            temp_dir = getattr(sys, '_MEIPASS', base_dir)
            resource_dir = temp_dir
        else:
            # 如果是开发环境
            resource_dir = os.path.dirname(os.path.abspath(__file__))

        # 加载状态图标
        self.default_icon = None  # 默认图标 - 未连接时使用
        self.running_icon = None  # 运行图标 - 连接成功时使用
        self.error_icon = None    # 错误图标 - 连接错误时使用

        # 定义图标路径
        default_icon_path = os.path.join(resource_dir, "default.png")
        running_icon_path = os.path.join(resource_dir, "running.png")
        error_icon_path = os.path.join(resource_dir, "error.png")

        # 加载默认图标 (default.png)
        if os.path.exists(default_icon_path):
            self.default_icon = QIcon(default_icon_path)
            self.app_icon = self.default_icon  # 默认应用图标
            print(f"成功加载默认图标: {default_icon_path}")
        else:
            # 创建默认图标作为备用
            print(f"找不到默认图标文件: {default_icon_path}，使用内置图标")
            default_pixmap = QPixmap(32, 32)
            default_pixmap.fill(QColor(100, 149, 237))  # 康乃馨蓝色
            self.default_icon = QIcon(default_pixmap)
            self.app_icon = self.default_icon

        # 加载运行图标 (running.png)
        if os.path.exists(running_icon_path):
            self.running_icon = QIcon(running_icon_path)
            print(f"成功加载运行图标: {running_icon_path}")
        else:
            # 创建运行图标作为备用
            print(f"找不到运行图标文件: {running_icon_path}，使用内置图标")
            running_pixmap = QPixmap(32, 32)
            running_pixmap.fill(QColor(60, 179, 113))  # 中等海洋绿
            self.running_icon = QIcon(running_pixmap)

        # 加载错误图标 (error.png)
        if os.path.exists(error_icon_path):
            self.error_icon = QIcon(error_icon_path)
            print(f"成功加载错误图标: {error_icon_path}")
        else:
            # 创建错误图标作为备用
            print(f"找不到错误图标文件: {error_icon_path}，使用内置图标")
            error_pixmap = QPixmap(32, 32)
            error_pixmap.fill(QColor(220, 20, 60))  # 猩红色
            self.error_icon = QIcon(error_pixmap)

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

        # 添加音频已禁用的通知项
        audio_disabled_action = QAction("音频处理已禁用", self)
        audio_disabled_action.setEnabled(False)  # 不可点击
        tray_menu.addAction(audio_disabled_action)

        tray_menu.addSeparator()

        # 连接状态操作（不可点击）
        self.connection_status_action = QAction("未连接", self)
        self.connection_status_action.setEnabled(False)
        tray_menu.addAction(self.connection_status_action)

        tray_menu.addSeparator()

        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self._exit_application)
        tray_menu.addAction(exit_action)

        # 设置托盘菜单
        self.tray_icon.setContextMenu(tray_menu)

        # 连接信号
        self.tray_icon.activated.connect(self.on_tray_icon_activated)

        # 显示托盘图标
        self.tray_icon.show()

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

    def on_call_received_notification(self, caller_number):
        """收到来电时显示通知和接听选项"""
        try:
            # 检查是否已有来电对话框正在显示，避免重复显示
            if self.incoming_call_dialog_visible:
                # 如果是同一个号码的来电，忽略此次通知
                if self.current_incoming_call_number == caller_number:
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 已有来电对话框显示中，忽略重复通知: {caller_number}")
                    return
                else:
                    # 如果是新号码，可能是之前的通知没有正确清理
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 检测到新来电，但旧对话框未关闭，强制清理: {self.current_incoming_call_number} -> {caller_number}")
                    # 继续处理新来电，旧对话框会在接听或拒绝时自动关闭

            print(f"收到来电: {caller_number}")

            # 设置当前来电号码和对话框状态
            self.current_incoming_call_number = caller_number
            self.incoming_call_dialog_visible = True

            # 确保应用程序窗口可见
            self.show()
            self.activateWindow()

            # 播放来电铃声
            self.sound_manager.play_incoming_call()

            # 显示系统通知
            if self.tray_icon.isVisible():
                self.tray_icon.showMessage(
                    "来电",
                    f"号码: {caller_number}",
                    QSystemTrayIcon.Information,
                    5000  # 显示5秒
                )

            # 立即在数据库中记录来电
            self.database.add_call(caller_number, None, "未接来电", 0)

            # 立即显示来电对话框 - 不再使用QTimer延迟
            self._show_incoming_call_dialog(caller_number)

            # 设置应当检查通话状态的标志，并启动计时器
            self.should_check_call_status = True
            self.call_check_count = 0
            if not self.call_status_timer.isActive():
                self.call_status_timer.start(1000)  # 开始每秒检查一次通话状态

        except Exception as e:
            print(f"处理来电通知时出错: {str(e)}")
            # 确保铃声停止
            self.sound_manager.stop_incoming_call()
            # 重置来电对话框状态
            self.incoming_call_dialog_visible = False
            self.current_incoming_call_number = None

    def _show_incoming_call_dialog(self, phone_number, caller_name=None):
        """显示来电对话框"""
        try:
            # 如果当前已经有来电对话框，先关闭它
            if self._incoming_call_dialog is not None and self._incoming_call_dialog.isVisible():
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 关闭已有的来电对话框")
                self._ensure_ringtone_stopped()
                self._incoming_call_dialog.close()
                self._incoming_call_dialog = None

            # 检查通话状态，确保确实有来电
            calls = self.lte_manager.get_call_status()
            has_incoming_call = False

            for call in calls:
                if call.get('stat') == 4 and call.get('dir') == 1:  # 来电中(MT)
                    has_incoming_call = True
                    break

            if not has_incoming_call:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 没有检测到来电，取消显示对话框")
                self._ensure_ringtone_stopped()
                return

            # 查找联系人信息
            if caller_name is None:
                contact = self.phone_sms_tab.contacts_tab.find_contact_by_number(phone_number)
                caller_name = contact["name"] if contact else None

            # 记录通话信息到数据库
            call_type = "未接来电"  # 初始设置为未接，后续根据用户操作修改
            self.database.add_call(phone_number, caller_name, call_type, 0)

            # 播放来电铃声
            self.sound_manager.play_incoming_call()

            # 创建并显示对话框
            self._incoming_call_dialog = IncomingCallDialog(
                phone_number,
                caller_name,
                parent=self
            )

            # 连接信号到槽
            self._incoming_call_dialog.answer_signal.connect(
                lambda: self._on_answer_call(phone_number, caller_name)
            )
            self._incoming_call_dialog.reject_signal.connect(
                lambda: self._on_reject_call(phone_number, caller_name)
            )

            # 连接对话框关闭信号，确保铃声停止
            self._incoming_call_dialog.finished.connect(self._ensure_ringtone_stopped)

            # 显示对话框
            self._incoming_call_dialog.show()

        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 显示来电对话框出错: {str(e)}")
            traceback.print_exc()
            # 确保在异常情况下也停止铃声
            self._ensure_ringtone_stopped()
            # 重置对话框状态
            self._incoming_call_dialog = None

    def _on_answer_call(self, phone_number, caller_name=None):
        """处理接听来电"""
        try:
            # 1. 立即停止铃声
            self._ensure_ringtone_stopped()

            # 2. 尝试接听电话
            result = self.lte_manager.answer_call()

            # 设置应当检查通话状态的标志，并启动计时器
            self.should_check_call_status = True
            self.call_check_count = 0
            if not self.call_status_timer.isActive():
                self.call_status_timer.start(1000)  # 开始每秒检查一次通话状态

            # 3. 检查通话状态，确认是否实际接通（即使API返回失败）
            time.sleep(0.5)  # 给模块一点时间更新状态
            calls = self.lte_manager.get_call_status()
            call_connected = False

            for call in calls:
                if call.get('stat') in [0, 1] and call.get('dir') == 1:  # 活动或保持的呼入通话
                    call_connected = True
                    break

            if result or call_connected:
                # 4. 修改数据库中的通话记录类型为"已接来电"
                self.database.update_call_type(phone_number, "已接来电")

                # 5. 更新UI状态
                self.phone_sms_tab.add_to_call_log(f"已接听来电: {phone_number}")
                self.phone_sms_tab.refresh_call_log()

                # 6. 再次检查通话状态，确认通话是否仍然活跃
                calls = self.lte_manager.get_call_status()
                is_call_active = False
                for call in calls:
                    if call.get('stat') in [0, 1]:  # 活动或保持状态
                        is_call_active = True
                        break

                if not is_call_active:
                    # 如果通话已结束，确保再次停止铃声
                    self._ensure_ringtone_stopped()
            else:
                # 通知接听失败
                QMessageBox.warning(self, "通话错误", "接听来电失败")
                self.sound_manager.play_error()
                self._ensure_ringtone_stopped()  # 再次确保铃声停止
        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 接听来电出错: {str(e)}")
            traceback.print_exc()
            self._ensure_ringtone_stopped()  # 确保在异常情况下也停止铃声

    def _on_reject_call(self, phone_number, caller_name=None):
        """处理拒接来电"""
        try:
            # 1. 立即停止铃声
            self._ensure_ringtone_stopped()

            # 设置应当检查通话状态的标志，并启动计时器
            self.should_check_call_status = True
            self.call_check_count = 0
            if not self.call_status_timer.isActive():
                self.call_status_timer.start(1000)  # 开始每秒检查一次通话状态

            # 2. 尝试挂断电话
            if self.lte_manager.end_call():
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 已拒绝来电: {phone_number}")
                # 3. 数据库中的通话记录类型保持为"未接来电"

                # 4. 更新UI状态
                self.phone_sms_tab.add_to_call_log(f"已拒绝来电: {phone_number}")
                self.phone_sms_tab.refresh_call_log()
            else:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 拒绝来电失败: {phone_number}")

            # 5. 再次确保铃声停止
            self._ensure_ringtone_stopped()
        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 拒绝来电出错: {str(e)}")
            traceback.print_exc()
            self._ensure_ringtone_stopped()  # 确保在异常情况下也停止铃声

    def _ensure_ringtone_stopped(self):
        """确保所有铃声已停止"""
        try:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 确保所有铃声已停止")
            self.sound_manager.stop_ringtone()
            self.sound_manager.stop_incoming_call()

            # 额外尝试停止系统声音
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
            except:
                pass

            # 如果还有声音线程在运行，给它们时间结束
            time.sleep(0.2)

            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 铃声停止过程完成")
        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 停止铃声出错: {str(e)}")
            traceback.print_exc()

    def on_call_ended(self, duration):
        """处理通话结束事件"""
        # 确保来电对话框状态被重置
        self.incoming_call_dialog_visible = False
        self.current_incoming_call_number = None

        # 记录通话结束信息
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 接收到通话结束信号，持续时间: {duration}")

        # 设置应当检查通话状态的标志，并启动计时器确认通话已结束
        self.should_check_call_status = True
        self.call_check_count = 0
        if not self.call_status_timer.isActive():
            self.call_status_timer.start(1000)  # 开始每秒检查一次通话状态

        # 使用状态栏显示消息
        if duration.isdigit():
            # 格式化持续时间（秒 -> 分:秒）
            seconds = int(duration)
            minutes = seconds // 60
            remaining_seconds = seconds % 60
            formatted_duration = f"{minutes}:{remaining_seconds:02d}"
            self.statusBar().showMessage(f"通话结束，持续时间: {formatted_duration}", 5000)
        else:
            # 如果不是数字（例如"Call ended"或"Missed"）
            self.statusBar().showMessage(f"通话结束: {duration}", 5000)

        # 更新数据库中的通话记录
        if self.lte_manager.call_number:
            try:
                # 将持续时间转换为秒
                if duration.isdigit():
                    duration_seconds = int(duration)
                else:
                    duration_seconds = 0

                # 查找最近的与此号码相关的通话记录
                calls = self.database.get_call_history(limit=1, phone_number=self.lte_manager.call_number)
                if calls:
                    # 更新现有记录
                    call_id = calls[0][0]  # 第一列是ID
                    # 更新持续时间和备注
                    self.database.cursor.execute(
                        "UPDATE call_history SET duration = ?, notes = NULL WHERE id = ?",
                        (duration_seconds, call_id)
                    )
                    self.database.conn.commit()
                    print(f"更新通话记录ID {call_id}，持续时间 {duration_seconds}秒")
                else:
                    # 如果找不到记录，添加一个新记录（这应该是不常见的情况）
                    self.database.add_call(
                        self.lte_manager.call_number,
                        None,
                        "未接来电" if duration == "Missed" or duration_seconds == 0 else "已接来电",
                        duration_seconds
                    )
                    print(f"新增通话记录，号码 {self.lte_manager.call_number}，持续时间 {duration_seconds}秒")
            except Exception as e:
                print(f"更新通话记录出错: {str(e)}")

    def _reset_audio_processor_state(self):
        """重置音频处理器状态（已简化为空操作）"""
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 重置音频处理器状态（空操作，处理已禁用）")

    def _stop_audio_with_timeout(self):
        """停止音频处理（已简化为空操作）"""
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 停止音频处理（空操作，处理已禁用）")

    def _cleanup_audio_resources(self):
        """清理所有音频相关资源（已简化为仅日志记录）"""
        # 更新状态
        try:
            self.audio_status_label.setText("音频: 非活动")
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 音频资源清理（空操作，处理已禁用）")
        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 清理音频资源出错: {str(e)}")

        # 确保停止任何正在播放的声音
        try:
            self.sound_manager.stop_ringtone()
            self.sound_manager.stop_incoming_call()
        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 停止声音时出错: {str(e)}")

    def check_call_status(self):
        """定期检查通话状态并更新UI"""
        if not self.lte_manager.is_connected():
            # 如果未连接，停止检查
            self.call_status_timer.stop()
            self.should_check_call_status = False
            return

        # 检查是否需要进行通话状态检查
        if not self.should_check_call_status:
            # 如果不需要继续检查，停止定时器
            self.call_status_timer.stop()
            return

        # 增加检查计数
        self.call_check_count += 1

        try:
            # 获取当前通话状态文本
            call_state = self.lte_manager.get_call_state_text()

            # 更新状态栏
            self.call_status_label.setText(f"通话: {call_state}")

            # 根据通话状态更新通话按钮状态
            calls = self.lte_manager.get_call_status()

            # 检查是否有应该显示的来电提示
            if calls and not self.incoming_call_dialog_visible:
                for call in calls:
                    if call.get('stat') == 4 and call.get('dir') == 1:  # 来电中(MT)
                        number = call.get('number', '未知号码')
                        # 不在通知中直接显示来电对话框，因为呼叫信号会通过call_received正常触发
                        break

            # 更新UI以反映当前的通话状态
            self.phone_sms_tab.update_call_ui_state(bool(calls))

            # 如果到达最大检查次数或没有活跃通话，停止定期检查
            if self.call_check_count >= self.max_call_checks or not calls:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 通话状态检查完成，停止定期检查")
                self.should_check_call_status = False
                self.call_status_timer.stop()

        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 检查通话状态出错: {str(e)}")
            # 出错时也停止检查
            self.should_check_call_status = False
            self.call_status_timer.stop()

    # 在phone_sms_tab.py中调用dial_button和end_call_button点击时也需要手动触发通话状态检查

    def update_status_bar(self):
        """更新状态栏显示的信息"""
        if not self.lte_manager.is_connected():
            return

        try:
            # 递增更新计数器，用于控制不同信息的更新频率
            self.update_counter += 1

            # 每次都更新信号强度
            signal_info = self.lte_manager.get_signal_strength()
            if signal_info:
                try:
                    if isinstance(signal_info, tuple) and len(signal_info) == 2:
                        signal_text, signal_desc = signal_info
                        if signal_desc:
                            self.status_signal.setText(f"信号: {signal_text} ({signal_desc})")
                        else:
                            self.status_signal.setText(f"信号: {signal_text}")
                    else:
                        self.status_signal.setText(f"信号: {signal_info}")
                except Exception as e:
                    print(f"处理信号强度信息出错: {str(e)}")
                    self.status_signal.setText(f"信号: {signal_info}")

            # 仅在首次连接或每10个周期更新一次运营商信息和电话号码
            if self.update_counter == 1 or self.update_counter % 10 == 0:
                # 更新运营商信息
                carrier_info = self.lte_manager.get_carrier_info()
                if carrier_info:
                    try:
                        if isinstance(carrier_info, tuple) and len(carrier_info) == 2:
                            carrier, network_type = carrier_info
                            self.status_carrier.setText(f"运营商: {carrier} ({network_type})")
                        else:
                            self.status_carrier.setText(f"运营商: {carrier_info}")
                    except Exception as e:
                        print(f"处理运营商信息出错: {str(e)}")
                        self.status_carrier.setText(f"运营商: {carrier_info}")

                # 更新电话号码
                phone_number = self.lte_manager.get_phone_number()
                if phone_number:
                    self.status_phone.setText(f"电话: {phone_number}")

            # 如果计数器达到30，重置它
            if self.update_counter >= 30:
                self.update_counter = 0

        except Exception as e:
            print(f"更新状态栏时出错: {str(e)}")
            traceback.print_exc()

    def on_status_changed(self, status):
        """处理状态变化"""
        try:
            # 在状态栏显示消息
            self.statusBar().showMessage(status, 5000)

            # 更新托盘图标中的连接状态
            if "Connected to LTE module" in status:
                self.update_connection_status(True)
                # 连接成功后强制立即进行第一次状态更新（使用延时确保连接流程完成后再更新）
                self.update_counter = 0  # 重置计数器
                QTimer.singleShot(500, self.update_status_bar)  # 0.5秒后更新状态栏
            elif "Disconnected from LTE module" in status:
                self.update_connection_status(False)
                # 立即更新状态栏为未连接状态
                self.status_carrier.setText("运营商: 未连接")
                self.status_phone.setText("电话: 不可用")
                self.status_network.setText("网络: 未连接")
                self.status_signal.setText("信号: 不可用")
                self.call_status_label.setText("通话: 无通话")
            elif "error" in status.lower() or "失败" in status or "failed" in status.lower():
                # 检测到错误状态
                self.show_error_status(status)
                # 出错时可能需要重新获取某些信息，强制下次执行完整更新
                self.update_counter = 9
        except Exception as e:
            print(f"状态更新出错: {str(e)}")
            self.show_error_status(f"状态更新出错: {str(e)}")

    def closeEvent(self, event):
        """处理应用关闭事件"""
        # 如果是通过退出菜单触发的关闭，直接关闭应用
        if self.is_exiting:
            self._cleanup_and_exit(event)
            return

        # 显示确认对话框，询问是否退出或最小化到托盘
        reply = QMessageBox.question(
            self,
            '关闭确认',
            '您希望退出程序还是最小化到系统托盘？\n\n点击"是"退出程序\n点击"否"最小化到系统托盘',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            # 用户选择退出
            self._cleanup_and_exit(event)
        else:
            # 用户选择最小化到托盘
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "LTE Tool",
                "应用程序已最小化到系统托盘。双击图标可恢复窗口。",
                QSystemTrayIcon.Information,
                2000
            )

    def _cleanup_and_exit(self, event):
        """清理资源并退出应用"""
        # 停止所有声音
        self.sound_manager.stop_ringtone()
        self.sound_manager.stop_incoming_call()

        # 关闭数据库连接
        self.database.close()

        # 断开LTE模块连接
        if self.lte_manager.is_connected():
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 正在关闭LTE模块连接...")
            self.lte_manager.disconnect()

        # 移除托盘图标
        if self.tray_icon.isVisible():
            self.tray_icon.hide()

        # 接受关闭事件
        event.accept()

    def _exit_application(self):
        """退出应用程序"""
        if self.lte_manager and self.lte_manager.is_connected():
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 正在关闭LTE模块连接...")
            self.lte_manager.disconnect()

        self.is_exiting = True
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - LTE Tool 应用程序退出")
        QApplication.quit()

    def update_connection_status(self, connected):
        """更新托盘图标中的连接状态"""
        try:
            if connected:
                # 使用运行图标表示连接成功
                self.tray_icon.setIcon(self.running_icon)
                self.tray_icon.setToolTip("LTE Tool - 已连接")
                self.connection_status_action.setText("已连接")
                # 在状态栏显示连接指示器
                self.statusBar().setStyleSheet("QStatusBar { background-color: rgba(60, 179, 113, 30); }")
                self.setWindowIcon(self.running_icon)  # 更新窗口图标
            else:
                # 使用默认图标表示未连接状态
                self.tray_icon.setIcon(self.default_icon)
                self.tray_icon.setToolTip("LTE Tool - 未连接")
                self.connection_status_action.setText("未连接")
                # 在状态栏显示未连接指示器
                self.statusBar().setStyleSheet("QStatusBar { background-color: rgba(100, 149, 237, 30); }")
                self.setWindowIcon(self.default_icon)  # 更新窗口图标
        except Exception as e:
            # 发生错误时使用错误图标
            print(f"更新连接状态出错: {str(e)}")
            try:
                self.tray_icon.setIcon(self.error_icon)
                self.tray_icon.setToolTip("LTE Tool - 连接错误")
                self.connection_status_action.setText("连接错误")
                self.statusBar().setStyleSheet("QStatusBar { background-color: rgba(220, 20, 60, 30); }")
                self.setWindowIcon(self.error_icon)  # 更新窗口图标
            except:
                print("无法设置错误图标状态")

    def show_error_status(self, error_message):
        """显示错误状态并更新图标"""
        try:
            self.statusBar().showMessage(f"错误: {error_message}", 5000)
            self.tray_icon.setIcon(self.error_icon)
            self.tray_icon.setToolTip(f"LTE Tool - 错误: {error_message[:30]}")
            self.setWindowIcon(self.error_icon)

            # 显示托盘通知
            self.tray_icon.showMessage(
                "LTE Tool 错误",
                error_message,
                QSystemTrayIcon.Warning,
                3000
            )
        except Exception as e:
            print(f"显示错误状态时出错: {str(e)}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    # 不再设置QuitOnLastWindowClosed为False，让应用在窗口关闭时可以正常退出
    # app.setQuitOnLastWindowClosed(False)
    window = LTEToolApp()
    window.show()
    sys.exit(app.exec_())
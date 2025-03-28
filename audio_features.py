import os
import time
import logging
from PyQt5.QtCore import QObject, pyqtSignal
import platform

# 配置日志记录
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("AudioFeatures")

class AudioFeatures(QObject):
    """音频功能模块，提供通话录音和音频播放功能"""

    status_changed = pyqtSignal(str)  # 状态变化信号

    def __init__(self, lte_manager):
        super().__init__()
        self.lte_manager = lte_manager
        self.recording = False
        self.playing = False
        self.auto_record_calls = False  # 是否自动录制通话
        self.auto_play_after_call = False  # 是否在通话结束后自动播放录音
        self.auto_play_on_answer = False  # 是否在接听电话时自动播放声音
        self.answer_play_audio_file = None  # 接听时播放的音频文件
        self.current_recording_file = None  # 当前录音文件路径

        # 获取用户 home 目录
        self.user_home = os.path.expanduser("~")

        # 创建默认存储路径：用户home/.LTE/REC/
        self.base_storage_path = os.path.join(self.user_home, ".LTE")
        self.storage_path = os.path.join(self.base_storage_path, "REC")
        self.ensure_storage_path()

        # 创建音频文件存储路径
        self.audio_storage_path = os.path.join(self.base_storage_path, "AUDIO")
        self.ensure_audio_storage_path()

        # 支持的音频格式
        self.supported_formats = [".amr", ".wav", ".mp3", ".pcm"]

    def ensure_storage_path(self):
        """确保存储路径存在"""
        try:
            if not os.path.exists(self.storage_path):
                os.makedirs(self.storage_path)
                logger.info(f"已创建存储路径: {self.storage_path}")
        except Exception as e:
            logger.error(f"创建存储路径失败: {str(e)}")

    def ensure_audio_storage_path(self):
        """确保音频文件存储路径存在"""
        try:
            if not os.path.exists(self.audio_storage_path):
                os.makedirs(self.audio_storage_path)
                logger.info(f"已创建音频存储路径: {self.audio_storage_path}")
        except Exception as e:
            logger.error(f"创建音频存储路径失败: {str(e)}")

    def set_storage_path(self, path):
        """设置存储路径"""
        if os.path.exists(path):
            self.storage_path = path
            logger.info(f"存储路径已设置为: {path}")
            return True
        else:
            logger.error(f"存储路径不存在: {path}")
            return False

    def set_auto_record_calls(self, enabled):
        """设置是否自动录制通话"""
        self.auto_record_calls = enabled
        logger.info(f"自动录制通话功能已{'启用' if enabled else '禁用'}")
        return True

    def set_auto_play_after_call(self, enabled):
        """设置是否在通话结束后自动播放录音"""
        self.auto_play_after_call = enabled
        logger.info(f"通话结束后自动播放录音功能已{'启用' if enabled else '禁用'}")
        return True

    def set_auto_play_on_answer(self, enabled, audio_file=None):
        """
        设置是否在接听电话时自动播放音频

        参数:
        - enabled: 是否启用该功能
        - audio_file: 要播放的音频文件路径，如果为None则使用当前设置的文件

        返回:
        - bool: 是否成功设置
        """
        self.auto_play_on_answer = enabled

        if audio_file:
            # 检查文件是否存在且格式支持
            if os.path.exists(audio_file) and any(audio_file.lower().endswith(fmt) for fmt in self.supported_formats):
                self.answer_play_audio_file = audio_file
                logger.info(f"接听电话自动播放音频功能已{'启用' if enabled else '禁用'}, 音频文件: {os.path.basename(audio_file)}")
                return True
            else:
                logger.error(f"音频文件不存在或格式不支持: {audio_file}")
                return False
        else:
            # 如果未指定文件但已有设置的文件
            if enabled and not self.answer_play_audio_file:
                logger.warning("启用接听电话自动播放音频功能，但未指定音频文件")
                return False

            logger.info(f"接听电话自动播放音频功能已{'启用' if enabled else '禁用'}")
            return True

    def play_on_answer(self, phone_number=None):
        """
        接听电话时自动播放音频

        参数:
        - phone_number: 可选，来电号码用于日志记录

        返回:
        - bool: 是否成功播放
        """
        if not self.auto_play_on_answer or not self.answer_play_audio_file:
            logger.info("接听电话自动播放功能未启用或未设置音频文件")
            return False

        if not os.path.exists(self.answer_play_audio_file):
            logger.error(f"接听自动播放音频文件不存在: {self.answer_play_audio_file}")
            return False

        # 先停止可能正在播放的音频
        if self.playing:
            self.stop_audio()
            time.sleep(0.5)  # 等待停止完成

        # 使用远程播放模式，让对方能听到声音
        logger.info(f"接听电话({phone_number})，自动播放音频: {os.path.basename(self.answer_play_audio_file)}")
        return self.play_audio(self.answer_play_audio_file, play_path=1)  # play_path=1表示远程播放，对方听得到

    def start_call_recording(self, phone_number=None):
        """
        开始通话录音

        参数:
        - phone_number: 电话号码，用于文件命名

        返回:
        - bool: 是否成功开始录音
        - str: 录音文件路径
        """
        if not phone_number:
            phone_number = self.lte_manager.call_number if hasattr(self.lte_manager, 'call_number') else "unknown"

        # 创建基于电话号码和时间的文件名
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"call_{phone_number}_{timestamp}.wav"

        # 完整文件路径
        file_path = os.path.join(self.storage_path, filename)

        # 存储当前录音文件路径用于可能的自动播放
        self.current_recording_file = file_path

        # 调用录音方法，使用双方声音混合录制模式(3)
        result = self.start_recording(filename=filename, record_path=3)

        return result, file_path

    def start_recording(self, filename=None, record_path=1):
        """
        开始录音

        参数:
        - filename: 录音文件名，不包含路径。如果未提供，则使用时间戳命名
        - record_path: 录音路径类型
            1 = 本地路径 (录制本地麦克风)
            2 = 远程路径 (录制通话对方声音)
            3 = 混合模式 (录制双方声音)

        返回:
        - bool: 是否成功开始录音
        """
        if self.recording:
            logger.warning("当前已有录音正在进行")
            return False

        if not self.lte_manager.is_connected():
            logger.error("未连接到LTE模块，无法开始录音")
            self.status_changed.emit("未连接到LTE模块，无法开始录音")
            return False

        # 如果未提供文件名，使用时间戳命名
        if not filename:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"recording_{timestamp}.wav"  # 默认使用wav格式

        # 确保文件格式正确
        if not any(filename.lower().endswith(fmt) for fmt in self.supported_formats):
            filename += ".wav"  # 默认使用wav格式

        # 组合完整路径
        file_path = os.path.join(self.storage_path, filename)
        # 使用模块内路径格式 (c:/ 对应模块内存储)
        module_path = f"c:/{os.path.basename(filename)}"

        # 记录当前录音文件路径
        self.current_recording_file = file_path

        # 发送录音命令
        command = f'AT+CREC={record_path},"{module_path}"'
        response = self.lte_manager.send_at_command(command)

        if "+CREC: 1" in response or "+CREC: 2" in response or "+CREC: 3" in response:
            self.recording = True
            logger.info(f"录音已开始: {file_path}")
            self.status_changed.emit(f"录音已开始: {os.path.basename(file_path)}")
            return True
        else:
            logger.error(f"开始录音失败: {response}")
            self.status_changed.emit(f"开始录音失败")
            self.current_recording_file = None
            return False

    def stop_recording(self):
        """
        停止录音

        返回:
        - bool: 是否成功停止录音
        """
        if not self.recording:
            logger.warning("当前没有录音正在进行")
            return False

        if not self.lte_manager.is_connected():
            logger.error("未连接到LTE模块，无法停止录音")
            self.status_changed.emit("未连接到LTE模块，无法停止录音")
            return False

        # 发送停止录音命令
        command = "AT+CREC=0"
        response = self.lte_manager.send_at_command(command)

        if "+CREC: 0" in response:
            self.recording = False
            logger.info("录音已停止")
            self.status_changed.emit("录音已停止")

            # 等待模块完成录音处理
            time.sleep(0.5)

            # 检查是否收到录音完成信号
            if "+CREC: crec stop" in response:
                logger.info("录音已完成处理")

            # 检查是否需要自动播放录音
            if self.auto_play_after_call and self.current_recording_file:
                logger.info(f"准备自动播放录音: {self.current_recording_file}")
                # 等待一下以确保录音文件已完成保存
                time.sleep(1)
                self.play_audio(self.current_recording_file)

            return True
        else:
            logger.error(f"停止录音失败: {response}")
            self.status_changed.emit("停止录音失败")
            return False

    def is_recording(self):
        """
        检查是否正在录音

        返回:
        - bool: 是否正在录音
        """
        if not self.lte_manager.is_connected():
            return False

        # 查询录音状态
        response = self.lte_manager.send_at_command("AT+CREC?")

        if "+CREC: 1" in response or "+CREC: 2" in response or "+CREC: 3" in response:
            self.recording = True
            return True
        else:
            self.recording = False
            return False

    def play_audio(self, filename, repeat=0, play_path=0):
        """
        播放音频文件

        参数:
        - filename: 音频文件名，可以是绝对路径或相对路径
        - repeat: 重复播放次数，0表示只播放一次，1-255表示重复播放的次数
        - play_path: 播放路径
            0 = 本地播放(默认)
            1 = 远程播放(通话时对方听到)
            2 = 双方都播放(本地和远程)

        返回:
        - bool: 是否成功开始播放
        """
        if self.playing:
            logger.warning("当前已有音频正在播放")
            self.stop_audio()  # 先停止当前播放

        if not self.lte_manager.is_connected():
            logger.error("未连接到LTE模块，无法播放音频")
            self.status_changed.emit("未连接到LTE模块，无法播放音频")
            return False

        # 确保文件存在且格式受支持
        if not os.path.exists(filename):
            # 尝试在存储路径中查找
            full_path = os.path.join(self.storage_path, filename)
            if not os.path.exists(full_path):
                # 尝试在音频路径中查找
                audio_path = os.path.join(self.audio_storage_path, filename)
                if not os.path.exists(audio_path):
                    logger.error(f"音频文件不存在: {filename}")
                    self.status_changed.emit(f"音频文件不存在: {os.path.basename(filename)}")
                    return False
                filename = audio_path
            else:
                filename = full_path

        if not any(filename.lower().endswith(fmt) for fmt in self.supported_formats):
            logger.error(f"不支持的音频格式: {filename}")
            self.status_changed.emit(f"不支持的音频格式: {os.path.basename(filename)}")
            return False

        # 转换为模块内路径格式
        module_path = f"c:/{os.path.basename(filename)}"

        # 根据文件类型选择播放命令 (对于wav文件可以使用AT+CCMXPLAYWAV)
        if filename.lower().endswith(".wav"):
            command = f'AT+CCMXPLAYWAV="{module_path}",{play_path}'
        else:
            # 其他格式使用AT+CCMXPLAY命令
            command = f'AT+CCMXPLAY="{module_path}",{play_path},{repeat}'

        response = self.lte_manager.send_at_command(command)

        success = False
        if "+CCMXPLAY:" in response and "OK" in response:
            success = True
        elif "+CCMXPLAYWAV:" in response and "OK" in response:
            success = True

        if success:
            self.playing = True
            logger.info(f"开始播放音频: {filename}")
            self.status_changed.emit(f"开始播放音频: {os.path.basename(filename)}")
            return True
        else:
            logger.error(f"播放音频失败: {response}")
            self.status_changed.emit("播放音频失败")
            return False

    def stop_audio(self):
        """
        停止音频播放

        返回:
        - bool: 是否成功停止播放
        """
        if not self.playing:
            logger.info("当前没有音频播放")
            return True

        if not self.lte_manager.is_connected():
            logger.error("未连接到LTE模块，无法停止播放")
            self.status_changed.emit("未连接到LTE模块，无法停止播放")
            return False

        # 检查是否正在播放WAV文件
        response_wav = self.lte_manager.send_at_command("AT+CCMXSTOPWAV")
        response_normal = self.lte_manager.send_at_command("AT+CCMXSTOP")

        success = False

        if "+CCMXSTOPWAV:" in response_wav and "OK" in response_wav:
            success = True
        elif "+CCMXSTOP:" in response_normal and "OK" in response_normal:
            success = True

        if success:
            self.playing = False
            logger.info("音频播放已停止")
            self.status_changed.emit("音频播放已停止")
            return True
        else:
            logger.error(f"停止音频播放失败: {response_wav}, {response_normal}")
            self.status_changed.emit("停止音频播放失败")
            return False

    def set_ringtone(self, filename):
        """
        设置来电铃声

        参数:
        - filename: 铃声文件名，可以是绝对路径或相对路径

        返回:
        - bool: 是否成功设置铃声
        """
        if not self.lte_manager.is_connected():
            logger.error("未连接到LTE模块，无法设置铃声")
            self.status_changed.emit("未连接到LTE模块，无法设置铃声")
            return False

        # 确保文件存在且格式受支持
        if not os.path.exists(filename):
            # 尝试在存储路径中查找
            full_path = os.path.join(self.storage_path, filename)
            if not os.path.exists(full_path):
                # 尝试在音频路径中查找
                audio_path = os.path.join(self.audio_storage_path, filename)
                if not os.path.exists(audio_path):
                    logger.error(f"铃声文件不存在: {filename}")
                    self.status_changed.emit(f"铃声文件不存在: {os.path.basename(filename)}")
                    return False
                filename = audio_path
            else:
                filename = full_path

        if not any(filename.lower().endswith(fmt) for fmt in self.supported_formats):
            logger.error(f"不支持的铃声格式: {filename}")
            self.status_changed.emit(f"不支持的铃声格式: {os.path.basename(filename)}")
            return False

        # 转换为模块内路径格式
        module_path = f"c:/{os.path.basename(filename)}"

        # 发送设置铃声命令
        command = f'AT+CRINGSET="{module_path}",1'
        response = self.lte_manager.send_at_command(command)

        if "OK" in response:
            logger.info(f"铃声已设置: {filename}")
            self.status_changed.emit(f"铃声已设置: {os.path.basename(filename)}")
            return True
        else:
            logger.error(f"设置铃声失败: {response}")
            self.status_changed.emit("设置铃声失败")
            return False

    def ring_switch(self, enable=True):
        """
        开启或关闭铃声

        参数:
        - enable: 是否启用铃声

        返回:
        - bool: 是否成功切换铃声状态
        """
        if not self.lte_manager.is_connected():
            logger.error("未连接到LTE模块，无法切换铃声状态")
            self.status_changed.emit("未连接到LTE模块，无法切换铃声状态")
            return False

        # 发送铃声开关命令
        status = 1 if enable else 0
        command = f"AT+CRTSWITCH={status}"
        response = self.lte_manager.send_at_command(command)

        if "OK" in response:
            state = "开启" if enable else "关闭"
            logger.info(f"铃声已{state}")
            self.status_changed.emit(f"铃声已{state}")
            return True
        else:
            logger.error(f"切换铃声状态失败: {response}")
            self.status_changed.emit("切换铃声状态失败")
            return False

    def generate_dtmf(self, dtmf_string, duration=1, time_base=100):
        """
        生成DTMF音

        参数:
        - dtmf_string: DTMF字符串，如"1,2,3,4"
        - duration: 持续时间因子，1-100
        - time_base: 时间基准，50-500ms

        返回:
        - bool: 是否成功生成DTMF音
        """
        if not self.lte_manager.is_connected():
            logger.error("未连接到LTE模块，无法生成DTMF音")
            self.status_changed.emit("未连接到LTE模块，无法生成DTMF音")
            return False

        # 确保DTMF字符串格式正确（数字、字母A-D、*、#，逗号分隔）
        valid_chars = set("0123456789ABCD*#,")
        if not all(c.upper() in valid_chars for c in dtmf_string):
            logger.error(f"无效的DTMF字符串: {dtmf_string}")
            self.status_changed.emit("无效的DTMF字符串")
            return False

        # 发送DTMF生成命令
        command = f'AT+CLDTMF={duration},"{dtmf_string}",{time_base},0'
        response = self.lte_manager.send_at_command(command)

        if "OK" in response:
            logger.info(f"DTMF音已生成: {dtmf_string}")
            self.status_changed.emit(f"DTMF音已生成")
            return True
        else:
            logger.error(f"生成DTMF音失败: {response}")
            self.status_changed.emit("生成DTMF音失败")
            return False

    def generate_tone(self, frequency=1000, period_on=200, period_off=200, duration=1000):
        """
        生成特定频率的音调

        参数:
        - frequency: 频率，20-4000Hz
        - period_on: 音调开启周期，50-25500ms
        - period_off: 音调关闭周期，0或40-25500ms
        - duration: 持续时间，50-500000ms

        返回:
        - bool: 是否成功生成音调
        """
        if not self.lte_manager.is_connected():
            logger.error("未连接到LTE模块，无法生成音调")
            self.status_changed.emit("未连接到LTE模块，无法生成音调")
            return False

        # 检查参数范围
        if not (20 <= frequency <= 4000):
            logger.error(f"频率超出范围(20-4000Hz): {frequency}")
            frequency = max(20, min(frequency, 4000))

        if not (50 <= period_on <= 25500):
            logger.error(f"开启周期超出范围(50-25500ms): {period_on}")
            period_on = max(50, min(period_on, 25500))

        if period_off != 0 and not (40 <= period_off <= 25500):
            logger.error(f"关闭周期超出范围(0或40-25500ms): {period_off}")
            period_off = max(40, min(period_off, 25500))

        if not (50 <= duration <= 500000):
            logger.error(f"持续时间超出范围(50-500000ms): {duration}")
            duration = max(50, min(duration, 500000))

        # 发送音调生成命令
        command = f"AT+SIMTONE=1,{frequency},{period_on},{period_off},{duration}"
        response = self.lte_manager.send_at_command(command)

        if "OK" in response:
            logger.info(f"音调已生成: {frequency}Hz")
            self.status_changed.emit(f"音调已生成: {frequency}Hz")
            return True
        else:
            logger.error(f"生成音调失败: {response}")
            self.status_changed.emit("生成音调失败")
            return False

    def stop_tone(self):
        """
        停止音调

        返回:
        - bool: 是否成功停止音调
        """
        if not self.lte_manager.is_connected():
            logger.error("未连接到LTE模块，无法停止音调")
            self.status_changed.emit("未连接到LTE模块，无法停止音调")
            return False

        # 发送停止音调命令
        command = "AT+SIMTONE=0"
        response = self.lte_manager.send_at_command(command)

        if "OK" in response:
            logger.info("音调已停止")
            self.status_changed.emit("音调已停止")
            return True
        else:
            logger.error(f"停止音调失败: {response}")
            self.status_changed.emit("停止音调失败")
            return False
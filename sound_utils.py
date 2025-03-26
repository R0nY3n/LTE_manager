import winsound
import threading
import time

class SoundManager:
    def __init__(self):
        """Initialize sound manager"""
        self.is_ringing = False
        self.ring_thread = None
        self.incoming_call_active = False
        self.incoming_call_thread = None

    def play_ringtone(self):
        """Play ringtone for incoming call"""
        if self.is_ringing:
            return

        self.is_ringing = True
        self.ring_thread = threading.Thread(target=self._ring_loop)
        self.ring_thread.daemon = True
        self.ring_thread.start()

    def _ring_loop(self):
        """Ring loop for incoming call"""
        try:
            while self.is_ringing:
                # Play ringtone (1000Hz for 500ms)
                winsound.Beep(1000, 500)
                time.sleep(1.0)
        except Exception as e:
            print(f"Ringtone error: {str(e)}")
        finally:
            self.is_ringing = False

    def stop_ringtone(self):
        """停止普通铃声"""
        # 首先设置停止标志
        self.is_ringing = False

        # 尝试立即停止系统音效
        try:
            winsound.PlaySound(None, winsound.SND_PURGE)
        except:
            pass

        # 尝试等待铃声线程结束，使用较短的超时时间
        if self.ring_thread and self.ring_thread.is_alive():
            try:
                self.ring_thread.join(timeout=0.5)
            except:
                pass

            # 如果线程仍在运行，创建新的线程引用使旧线程成为孤立线程
            if self.ring_thread.is_alive():
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 铃声线程没有正常停止，强制释放")
                self.ring_thread = None

        # 再次确认停止标志已设置
        self.is_ringing = False
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 普通铃声停止完成")

    def play_incoming_call(self):
        """播放来电铃声"""
        # 如果已经在播放，不重复启动
        if self.incoming_call_active:
            return

        # 确保任何之前的铃声线程都已经停止
        self.stop_incoming_call()

        # 启动新的铃声
        self.incoming_call_active = True
        self.incoming_call_thread = threading.Thread(target=self._incoming_call_loop)
        self.incoming_call_thread.daemon = True
        self.incoming_call_thread.start()
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 开始播放来电铃声")

    def _incoming_call_loop(self):
        """来电铃声循环"""
        try:
            # 记录启动时间，避免铃声线程持续太久
            start_time = time.time()
            max_duration = 120  # 最长播放时间（秒）

            while self.incoming_call_active and (time.time() - start_time) < max_duration:
                # 播放来电铃声，使用系统铃声
                try:
                    winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS)
                except:
                    # 如果系统铃声不可用，使用传统铃声
                    try:
                        winsound.Beep(1200, 300)
                        time.sleep(0.2)
                        winsound.Beep(1000, 300)
                    except:
                        # 如果Beep也失败，只等待
                        time.sleep(1.0)

                # 每次播放后检查是否应该停止
                if not self.incoming_call_active:
                    break

                # 铃声间隔
                time.sleep(1.0)

            # 如果退出是因为超时，打印日志
            if (time.time() - start_time) >= max_duration:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 来电铃声已达到最长播放时间，自动停止")

        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 来电铃声错误: {str(e)}")
        finally:
            # 确保停止标志被设置
            self.incoming_call_active = False
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 来电铃声线程正常退出")

    def stop_incoming_call(self):
        """停止来电铃声"""
        # 首先设置停止标志
        prev_state = self.incoming_call_active
        self.incoming_call_active = False

        # 打印状态日志，帮助调试
        if prev_state:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 正在停止来电铃声（之前状态：激活）")
        else:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 正在停止来电铃声（之前状态：已停止）")

        # 尝试立即停止系统声音
        try:
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 停止系统声音时出错: {str(e)}")

        # 等待线程结束
        if self.incoming_call_thread and self.incoming_call_thread.is_alive():
            try:
                # 使用较短的超时时间，避免长时间等待
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 等待来电铃声线程结束...")
                self.incoming_call_thread.join(timeout=0.5)

                # 检查线程是否已结束
                if not self.incoming_call_thread.is_alive():
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 来电铃声线程已正常结束")
                else:
                    # 如果线程仍然活动，创建新线程以避免阻塞
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 来电铃声线程未能正常停止，强制释放")
                    # 创建并设置新线程引用，使旧线程成为孤立线程（将被Python垃圾回收）
                    self.incoming_call_thread = None
            except Exception as e:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 等待来电铃声线程时出错: {str(e)}")
                # 重置线程引用
                self.incoming_call_thread = None

        # 确保标志重置为False（多重保障）
        self.incoming_call_active = False

        # 确认声音已停止
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - 来电铃声停止流程完成")

    def play_call_end(self):
        """Play call end sound"""
        try:
            # Play call end sound (800Hz for 200ms, then 600Hz for 300ms)
            winsound.Beep(800, 200)
            winsound.Beep(600, 300)
        except Exception as e:
            print(f"Call end sound error: {str(e)}")

    def play_message_received(self):
        """Play message received sound"""
        try:
            # Play message received sound (three beeps at 1200Hz for 200ms)
            winsound.Beep(1200, 200)
            time.sleep(0.1)
            winsound.Beep(1200, 200)
            time.sleep(0.1)
            winsound.Beep(1200, 200)
        except Exception as e:
            print(f"Message sound error: {str(e)}")

    def play_error(self):
        """Play error sound"""
        try:
            # Play error sound (400Hz for 400ms)
            winsound.Beep(400, 400)
        except Exception as e:
            print(f"Error sound error: {str(e)}")

    def play_success(self):
        """Play success sound"""
        try:
            # Play success sound (1000Hz for 200ms, then 1200Hz for 200ms)
            winsound.Beep(1000, 200)
            winsound.Beep(1200, 200)
        except Exception as e:
            print(f"Success sound error: {str(e)}")

    def play_dtmf(self):
        """播放DTMF按键提示音"""
        try:
            # 模拟DTMF音，播放简短高频音
            winsound.Beep(1400, 100)
        except Exception as e:
            print(f"DTMF音播放错误: {str(e)}")
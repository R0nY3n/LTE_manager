import winsound
import threading
import time

class SoundManager:
    def __init__(self):
        """Initialize sound manager"""
        self.is_ringing = False
        self.ring_thread = None

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
        """Stop ringtone"""
        self.is_ringing = False
        if self.ring_thread:
            self.ring_thread.join(timeout=2.0)

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
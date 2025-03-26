import sys
import time
from PyQt5.QtWidgets import QApplication
from incoming_call import show_incoming_call
from sound_utils import SoundManager
import serial

def simulate_incoming_call():
    """模拟来电测试程序"""
    print("来电模拟器")
    print("-" * 50)

    # 测试来电对话框
    print("测试1: 显示来电对话框")
    app = QApplication(sys.argv)

    # 创建声音管理器
    sound_manager = SoundManager()

    # 播放来电铃声
    sound_manager.play_incoming_call()
    print("播放来电铃声...")

    # 显示来电对话框
    caller_number = "+8613800138000"
    print(f"显示来电: {caller_number}")

    result = show_incoming_call(caller_number)

    # 停止来电铃声
    sound_manager.stop_incoming_call()

    # 显示结果
    print(f"用户选择了: {'接听' if result else '拒绝'}")

    # 测试完成
    print("=" * 50)
    print("测试完成")

def test_module_ring_function():
    """测试模块的来电功能"""
    print("LTE模块来电测试")
    print("-" * 50)

    # 获取COM口
    port = input("请输入AT命令COM口: ")
    if not port:
        print("未提供COM口，测试结束")
        return

    # 打开串口
    try:
        ser = serial.Serial(port, 115200, timeout=1)
        print(f"成功打开COM口 {port}")
    except Exception as e:
        print(f"无法打开COM口: {str(e)}")
        return

    # 发送AT命令检查模块
    try:
        ser.write(b"AT\r")
        time.sleep(0.5)
        response = ser.read(ser.in_waiting).decode('utf-8', errors='replace')
        print(f"AT响应: {response}")

        if "OK" not in response:
            print("模块没有响应AT命令，测试结束")
            ser.close()
            return
    except Exception as e:
        print(f"发送AT命令失败: {str(e)}")
        ser.close()
        return

    # 主菜单
    while True:
        print("\n选择测试选项:")
        print("1. 模拟来电 (向模块发送AT+CLIP命令)")
        print("2. 等待实际来电")
        print("3. 退出")

        choice = input("您的选择: ")

        if choice == '1':
            # 模拟来电
            phone_number = input("请输入要模拟的来电号码: ")
            if not phone_number:
                phone_number = "+8613800138000"

            # 发送RING和CLIP命令
            print(f"模拟来电: {phone_number}")
            ser.write(b"AT+CLIP=1\r")
            time.sleep(0.5)
            ser.read(ser.in_waiting)  # 清除响应

            # 发送RING和CLIP
            ser.write(b"RING\r\n")
            time.sleep(0.5)
            clip_cmd = f'AT+CLIP: "{phone_number}",129,"",0,"",0\r\n'
            ser.write(clip_cmd.encode())

            print("模拟来电信号已发送，查看应用是否有响应")

        elif choice == '2':
            # 等待实际来电
            print("请使用另一部手机拨打模块的电话号码...")
            print("按Enter键停止等待")
            input()

        elif choice == '3':
            # 退出
            break

        else:
            print("无效选择，请重试")

    # 关闭串口
    ser.close()
    print("测试结束")

if __name__ == "__main__":
    print("来电功能测试工具")
    print("=" * 50)
    print("1. 测试来电对话框")
    print("2. 测试LTE模块来电功能")

    choice = input("请选择测试类型: ")

    if choice == '1':
        simulate_incoming_call()
    elif choice == '2':
        test_module_ring_function()
    else:
        print("无效选择，测试结束")
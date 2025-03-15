# LTE Tool / LTE 工具

A Python+Qt application for managing communications with LTE modules. While specifically optimized for SIM7600CE-T modules, it's compatible with most modules supporting standard AT commands.

基于Python+Qt开发的LTE模块通信管理工具。虽然专为SIM7600CE-T模块优化，但兼容大多数支持标准AT命令的模块。

## Features / 功能特点

- Phone call management (make/receive calls, may not be supported by all modules)
- SMS management (send/receive/decode messages, including Chinese)
- Module configuration and status monitoring
- Serial port configuration
- System tray integration with connection status indicator
- Improved sound notifications for incoming messages
- Last used port memory for easier reconnection
- Auto-connect feature at startup
- Enhanced Chinese SMS support

---

- 电话管理（拨打/接听电话，部分模块可能不支持）
- 短信管理（发送/接收/解码消息，支持中文）
- 模块配置和状态监控
- 串口配置
- 系统托盘集成，带连接状态指示
- 改进的来电提示音
- 记住上次使用的端口，便于重新连接
- 启动时自动连接功能
- 增强的中文短信支持

## Requirements / 系统要求

- Python 3.6+
- PyQt5
- pyserial

## Installation / 安装方法

### English:
1. Clone this repository
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Or use the pre-built executable from the `dist` folder

### 中文:
1. 克隆此仓库
2. 安装依赖项:
   ```
   pip install -r requirements.txt
   ```
3. 或直接使用`dist`文件夹中的预编译可执行文件

## Usage / 使用方法

### English:
Run the main application:
```
python main.py
```

Or launch the executable `LTE_Manager.exe` from the `dist` folder.

### 中文:
运行主应用程序:
```
python main.py
```

或直接从`dist`文件夹启动可执行文件`LTE_Manager.exe`。

## Module Configuration / 模块配置

### SMS Configuration / 短信配置
#### English:
The application is configured to work with modules set to automatically push SMS notifications. By default, it supports the `AT+CNMI=2,2,0,0,0` mode, which provides direct SMS content delivery.

To configure your module for optimal SMS handling:

1. **Set SMS text mode**:
   ```
   AT+CMGF=1
   ```

2. **Configure SMS automatic notification**:
   ```
   AT+CNMI=2,2,0,0,0
   ```

   Parameters explanation:
   - 2: Enable SMS status reports (immediate notification for new messages)
   - 2: New message notifications sent directly to serial port with content
   - 0: Disable read status notifications
   - 0: Disable cell broadcast
   - 0: Disable reporting of unread messages (only report new messages)

3. **Verify configuration**:
   ```
   AT+CNMI?
   ```

   Expected response:
   ```
   +CNMI: 2,2,0,0,0
   ```

With this configuration, when a new SMS is received, the module will automatically output the complete message content:
```
+CMT: "+8613812345678","","23/03/13,15:30:00+32"
Hello, this is a test message!
```

#### 中文:
应用程序配置为与设置为自动推送短信通知的模块一起工作。默认情况下，它支持`AT+CNMI=2,2,0,0,0`模式，该模式提供直接的短信内容推送。

要为您的模块配置最佳短信处理：

1. **设置短信文本模式**:
   ```
   AT+CMGF=1
   ```

2. **配置短信自动通知**:
   ```
   AT+CNMI=2,2,0,0,0
   ```

   参数说明：
   - 2: 使能短信状态报告（新消息时直接通知）
   - 2: 新消息通知直接发送到串口并包含内容
   - 0: 禁用已读状态通知
   - 0: 关闭小区广播
   - 0: 关闭上报未读短信（只报告新消息）

3. **验证配置**:
   ```
   AT+CNMI?
   ```

   预期响应：
   ```
   +CNMI: 2,2,0,0,0
   ```

使用此配置，当收到新短信时，模块将自动输出完整的消息内容：
```
+CMT: "+8613812345678","","23/03/13,15:30:00+32"
Hello, this is a test message!
```

### Alternative SMS Configuration / 替代短信配置
#### English:
If you prefer to receive only notifications without content and manually read messages, you can use:
```
AT+CNMI=2,1,0,0,0
```

With this setting, when a new SMS is received, the module will output:
```
+CMTI: "SM",3
```
Where "SM" indicates storage in SIM card and "3" is the message index.

To read the message content, use:
```
AT+CMGR=3
```
(where 3 is the index from the notification)

#### 中文:
如果您希望只接收通知而不包含内容，然后手动读取消息，可以使用：
```
AT+CNMI=2,1,0,0,0
```

使用此设置，当收到新短信时，模块将输出：
```
+CMTI: "SM",3
```
其中"SM"表示存储在SIM卡中，"3"是消息索引。

要读取消息内容，请使用：
```
AT+CMGR=3
```
（其中3是通知中的索引）

## Features Description / 功能描述

### Phone & SMS Tab / 电话和短信标签页
#### English:
- Make and receive phone calls (if supported by your module)
- Send and receive SMS messages (with Chinese support)
- View call and message history
- Manage SMS storage

#### 中文:
- 拨打和接听电话（如果您的模块支持）
- 发送和接收短信（支持中文）
- 查看通话和短信历史记录
- 管理短信存储

### Settings Tab / 设置标签页
#### English:
- Configure serial ports (AT and NMEA)
- View module information (IMEI, IMSI, etc.)
- Monitor network status
- Enable auto-connect at startup

#### 中文:
- 配置串口（AT和NMEA）
- 查看模块信息（IMEI、IMSI等）
- 监控网络状态
- 启用启动时自动连接

### System Tray Features / 系统托盘功能
#### English:
- The application minimizes to system tray
- Icon indicates connection status (connected/disconnected)
- Right-click menu provides quick access to common functions
- Double-click on the tray icon to restore the application window

#### 中文:
- 应用程序可最小化到系统托盘
- 图标指示连接状态（已连接/未连接）
- 右键菜单提供对常用功能的快速访问
- 双击托盘图标可恢复应用程序窗口

## Recent Improvements / 最近改进
### English:
- Enhanced AT command response parsing to remove command echoes
- Added multiple beep sounds for message notifications
- Implemented port selection memory to remember last used ports
- Added connection status indicator in system tray
- Fixed icon display issues in system tray
- Added auto-connect feature at startup
- Improved Chinese SMS encoding/decoding
- Fixed phone number encoding for Chinese SMS
- Added compatibility with a wider range of AT command modules

### 中文:
- 增强AT命令响应解析，移除命令回显
- 添加多次蜂鸣声用于消息通知
- 实现端口选择记忆功能，记住上次使用的端口
- 在系统托盘中添加连接状态指示器
- 修复系统托盘图标显示问题
- 添加启动时自动连接功能
- 改进中文短信编码/解码
- 修复中文短信的电话号码编码问题
- 增加与更广泛AT命令模块的兼容性

## Compatibility / 兼容性

### English:
While optimized for SIM7600CE-T modules, this tool is designed to work with most modules that support standard AT commands. The phone functionality may not be available on all modules, but the SMS and configuration features should work on most AT command compatible devices.

### 中文:
虽然针对SIM7600CE-T模块进行了优化，但此工具设计为可与大多数支持标准AT命令的模块一起使用。电话功能可能并非在所有模块上都可用，但短信和配置功能应该在大多数兼容AT命令的设备上正常工作。

## Donation / 打赏支持

### English:
If you find this tool helpful, consider supporting the developer:

### 中文:
如果您觉得这个工具有用，可以考虑支持开发者:

<!--
Replace the following placeholders with your actual QR code images
将以下占位符替换为您的实际二维码图片
-->

| WeChat / 微信支付 | Alipay / 支付宝 |
| --- | --- |
| [WeChat QR Code] | [Alipay QR Code] |

## License / 许可证

### English:
This project is licensed under the Adaptive Community Source License (ACSL).

The ACSL is a community-oriented license that allows for free use, modification, and distribution of the software, while encouraging contributions back to the community. Key points:

1. You can use, modify, and distribute this software freely.
2. If you distribute modified versions, you should make your changes available to the community.
3. Commercial use is permitted, but commercial redistributions should contribute improvements back.
4. No warranty is provided; use at your own risk.

[Read the full ACSL license](https://anticapitalist.software/)

### 中文:
本项目采用自适应社区源代码许可证 (ACSL) 授权。

ACSL是一种面向社区的许可证，允许自由使用、修改和分发软件，同时鼓励向社区回馈贡献。主要要点：

1. 您可以自由使用、修改和分发此软件。
2. 如果您分发修改版本，应将您的更改提供给社区。
3. 允许商业使用，但商业再分发应将改进回馈给社区。
4. 不提供任何保证；使用风险自负。

[阅读完整license](https://anticapitalist.software/)
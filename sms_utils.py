import binascii

def text_to_ucs2(text):
    """Convert text to UCS2 (UTF-16BE) hex string for SMS sending"""
    try:
        # Encode text to UTF-16BE bytes
        utf16be_bytes = text.encode('utf-16be')

        # Convert bytes to hex string
        hex_str = binascii.hexlify(utf16be_bytes).decode('ascii').upper()

        return hex_str
    except Exception as e:
        print(f"UCS2 encoding error: {str(e)}")
        return None

def ucs2_to_text(hex_str):
    """Convert UCS2 (UTF-16BE) hex string to text for SMS display"""
    try:
        # Remove spaces if any
        hex_str = hex_str.replace(" ", "")

        # Make sure we have a valid hex string
        if not all(c in "0123456789ABCDEFabcdef" for c in hex_str):
            return hex_str  # Not a hex string, return as is

        # Make sure the length is even (each character is 2 bytes in UCS2)
        if len(hex_str) % 2 != 0:
            hex_str = hex_str + "0"  # Pad with zero if needed

        # 针对特定格式长短信的处理（以62117ED94F6053D14E86957F6587672C开头）
        if hex_str.startswith("62117ED94F6053D14E86957F6587672C"):
            # 这是一种特定格式的长短信，尝试提取关键信息
            # 通常格式是：固定标记 + "003A"(冒号) + URL内容
            parts = hex_str.split("003A", 1)
            if len(parts) > 1 and parts[1]:
                try:
                    # 提取并解码URL部分
                    url_hex = "003A" + parts[1]  # 加回冒号
                    url_bytes = binascii.unhexlify(url_hex)
                    url_text = url_bytes.decode('utf-16be', errors='replace')
                    return url_text
                except Exception as url_error:
                    print(f"URL extraction error: {str(url_error)}")
                    # 如果提取失败，尝试完整解码

        # For phone numbers in UCS2 format (e.g., 002B00380036...)
        if hex_str.startswith("002B") or all(c in "0123456789ABCDEF" for c in hex_str):
            # Check if it's likely a phone number (starts with +)
            if hex_str.startswith("002B"):  # "+" in UCS2
                try:
                    # Convert hex string to bytes
                    utf16be_bytes = binascii.unhexlify(hex_str)

                    # Decode bytes to text
                    text = utf16be_bytes.decode('utf-16be')
                    return text
                except:
                    # If it fails, try to extract the phone number directly
                    phone = ""
                    i = 0
                    while i < len(hex_str):
                        if i + 4 <= len(hex_str):
                            chunk = hex_str[i:i+4]
                            if chunk == "002B":  # "+"
                                phone += "+"
                            elif chunk.startswith("00") and chunk[2:4].isdigit():
                                phone += chunk[2:4]
                            i += 4
                        else:
                            break
                    if phone:
                        return phone

        # Try multiple decoding approaches
        try:
            # Standard UCS2 decoding
            utf16be_bytes = binascii.unhexlify(hex_str)
            text = utf16be_bytes.decode('utf-16be', errors='replace')
            return text
        except Exception as e1:
            print(f"Primary UCS2 decoding failed: {str(e1)}")

            try:
                # Try with different endianness
                utf16le_bytes = binascii.unhexlify(hex_str)
                text = utf16le_bytes.decode('utf-16le', errors='replace')
                return text
            except Exception as e2:
                print(f"Secondary UCS2 decoding failed: {str(e2)}")

                try:
                    # 尝试以每4位（2字节）为单位解析，移除非ASCII字符
                    result = ""
                    i = 0
                    while i < len(hex_str):
                        if i + 4 <= len(hex_str):
                            chunk = hex_str[i:i+4]
                            try:
                                # 检查是否可能是ASCII字符（大多数ASCII UCS2编码格式为00xx）
                                if chunk.startswith("00") and 32 <= int(chunk[2:4], 16) <= 126:
                                    char = chr(int(chunk[2:4], 16))
                                    result += char
                                # 对于非ASCII字符，尝试直接解码
                                else:
                                    char_bytes = binascii.unhexlify(chunk)
                                    char = char_bytes.decode('utf-16be', errors='ignore')
                                    if char:
                                        result += char
                            except:
                                pass
                            i += 4
                        else:
                            break

                    # 检测结果中的URL
                    url_match = None
                    if "http" in result:
                        url_match = result[result.find("http"):]
                        # 截断到第一个不合法URL字符处
                        for i, c in enumerate(url_match):
                            if c.isspace() or c in '",\'<>()[]{}':
                                url_match = url_match[:i]
                                break

                    # 如果找到URL，返回它
                    if url_match and len(url_match) > 10:  # 确保URL足够长
                        return url_match

                    # 否则返回处理的结果
                    if result:
                        return result
                except Exception as e3:
                    print(f"Chunk-by-chunk decoding failed: {str(e3)}")

                # 如果所有解码方法都失败，最后尝试查找URL模式
                try:
                    # 查找HTTP URL的常见模式
                    http_pattern = "00680074007400700073003A002F002F"  # "https://"
                    http_alt = "00680074007400700073003a002f002f"  # 小写冒号和斜杠

                    if http_pattern in hex_str or http_alt in hex_str:
                        start_idx = hex_str.find(http_pattern) if http_pattern in hex_str else hex_str.find(http_alt)
                        if start_idx >= 0:
                            url_hex = hex_str[start_idx:]
                            try:
                                url_bytes = binascii.unhexlify(url_hex)
                                url_text = url_bytes.decode('utf-16be', errors='replace')
                                return url_text
                            except:
                                pass
                except:
                    pass

                # 如果所有方法都失败，返回原始十六进制字符串
                return f"[Hex: {hex_str[:30]}...]"
    except Exception as e:
        print(f"UCS2 decoding error: {str(e)}")
        return f"[Decode error: {hex_str[:30]}...]"

def is_chinese_text(text):
    """Check if text contains Chinese characters"""
    for char in text:
        if '\u4e00' <= char <= '\u9fff':
            return True
    return False

def format_phone_number(number):
    """Format phone number for SMS sending (add +86 if needed)"""
    # Remove any spaces, dashes, or parentheses
    clean_number = ''.join(c for c in number if c.isdigit() or c == '+')

    # If it's a Chinese number without country code, add +86
    if clean_number.startswith('1') and len(clean_number) == 11:
        return f"+86{clean_number}"

    # If it doesn't have a + prefix, add it
    if not clean_number.startswith('+'):
        return f"+{clean_number}"

    return clean_number
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
            text = utf16be_bytes.decode('utf-16be')
            return text
        except Exception as e1:
            print(f"Primary UCS2 decoding failed: {str(e1)}")

            try:
                # Try with different endianness
                utf16le_bytes = binascii.unhexlify(hex_str)
                text = utf16le_bytes.decode('utf-16le')
                return text
            except Exception as e2:
                print(f"Secondary UCS2 decoding failed: {str(e2)}")

                try:
                    # Try decoding each 4-character chunk separately
                    result = ""
                    i = 0
                    while i < len(hex_str):
                        if i + 4 <= len(hex_str):
                            chunk = hex_str[i:i+4]
                            try:
                                char_bytes = binascii.unhexlify(chunk)
                                char = char_bytes.decode('utf-16be', errors='ignore')
                                if char:
                                    result += char
                            except:
                                pass
                            i += 4
                        else:
                            break

                    if result:
                        return result
                except Exception as e3:
                    print(f"Chunk-by-chunk decoding failed: {str(e3)}")

                    # If all decoding methods fail, return the original hex string
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
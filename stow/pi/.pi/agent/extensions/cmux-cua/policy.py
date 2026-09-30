import ctypes
import json
import sys


def jsonc(text):
    output = []
    i = 0
    quoted = False
    while i < len(text):
        char = text[i]
        if quoted:
            output.append(char)
            if char == "\\" and i + 1 < len(text):
                i += 1
                output.append(text[i])
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            output.append(char)
        elif text.startswith("//", i):
            end = text.find("\n", i + 2)
            i = len(text) if end == -1 else end
            output.append("\n")
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end == -1:
                raise ValueError("Unclosed comment")
            output.append(" ")
            i = end + 2
            continue
        else:
            output.append(char)
        i += 1
    text = "".join(output)
    output = []
    quoted = False
    i = 0
    while i < len(text):
        char = text[i]
        if quoted:
            output.append(char)
            if char == "\\" and i + 1 < len(text):
                i += 1
                output.append(text[i])
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            output.append(char)
        elif char == ",":
            end = i + 1
            while end < len(text) and text[end].isspace():
                end += 1
            if end >= len(text) or text[end] not in "}]":
                output.append(char)
        else:
            output.append(char)
        i += 1
    return json.loads("".join(output))


def configuration_enabled(path):
    try:
        with open(path, encoding="utf-8") as source:
            text = source.read(1024 * 1024 + 1)
    except FileNotFoundError:
        return None
    if len(text) > 1024 * 1024:
        raise ValueError("Configuration exceeds limit")
    data = jsonc(text)
    if not isinstance(data, dict):
        raise ValueError("Invalid configuration")
    computer_use = data.get("computerUse", {})
    if not isinstance(computer_use, dict):
        raise ValueError("Invalid Computer Use settings")
    enabled = computer_use.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("Invalid enabled setting")
    return enabled


def policy_disabled(domain):
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    pointer = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [pointer, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = pointer
    cf.CFPreferencesAppValueIsForced.argtypes = [pointer, pointer]
    cf.CFPreferencesAppValueIsForced.restype = ctypes.c_bool
    cf.CFPreferencesCopyAppValue.argtypes = [pointer, pointer]
    cf.CFPreferencesCopyAppValue.restype = pointer
    cf.CFGetTypeID.argtypes = [pointer]
    cf.CFGetTypeID.restype = ctypes.c_ulong
    cf.CFBooleanGetTypeID.restype = ctypes.c_ulong
    cf.CFBooleanGetValue.argtypes = [pointer]
    cf.CFBooleanGetValue.restype = ctypes.c_bool
    cf.CFRelease.argtypes = [pointer]
    key = cf.CFStringCreateWithCString(None, b"DisableComputerUse", 0x08000100)
    try:
        for name in dict.fromkeys([domain, "com.cmuxterm.app"]):
            app = cf.CFStringCreateWithCString(None, name.encode("utf-8"), 0x08000100)
            try:
                if not cf.CFPreferencesAppValueIsForced(key, app):
                    continue
                value = cf.CFPreferencesCopyAppValue(key, app)
                if value:
                    try:
                        return cf.CFGetTypeID(value) == cf.CFBooleanGetTypeID() and bool(cf.CFBooleanGetValue(value))
                    finally:
                        cf.CFRelease(value)
            finally:
                cf.CFRelease(app)
        return False
    finally:
        cf.CFRelease(key)


if __name__ == "__main__":
    try:
        enabled = next((value for value in (configuration_enabled(path) for path in sys.argv[2:]) if value is not None), True)
        print(json.dumps({"enabled": enabled, "policyDisabled": policy_disabled(sys.argv[1])}))
    except Exception:
        sys.exit(1)

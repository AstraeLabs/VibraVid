# 24.01.26

import os
import sys

if os.name == "nt":
    import msvcrt
else:
    import termios
    import tty


def get_key():
    """Cross-platform keyboard input handler"""
    if os.name == "nt":
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            codes = {72: "UP", 80: "DOWN", 75: "LEFT", 77: "RIGHT"}
            return codes.get(ord(ch2), None)

        if ch == "\r":
            return "ENTER"
        if ch == "\x1b":
            return "ESC"
        if ch == " ":
            return "SPACE"
        return ch

    else:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch1 = sys.stdin.read(1)

            if ch1 == "\x1b":
                ch2 = sys.stdin.read(1)
                if ch2 in ["[", "O"]:
                    ch3 = sys.stdin.read(1)
                    arrows = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}
                    result = arrows.get(ch3, None)
                    if result:
                        return result
                return "ESC"

            if ch1 == "\r" or ch1 == "\n":
                return "ENTER"
            if ch1 == " ":
                return "SPACE"
            return ch1

        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

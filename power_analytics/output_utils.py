import sys
import os
from typing import TextIO

EMOJI_FALLBACK = {
    "\u2705": "[OK]",
    "\u274C": "[ERROR]",
    "\u26A1": "[!]",
    "\U0001f4ca": "[=]",
    "\U0001f4cb": "[#]",
    "\U0001f50d": "[?]",
    "\U0001f4dd": "[*]",
    "\U0001f3af": "[>]",
    "\U0001f6a8": "[!]",
    "\U0001f534": "[E]",
    "\U0001f7e1": "[W]",
    "\U0001f535": "[I]",
    "\U0001f4c8": "[+]",
    "\U0001f4e6": "[>]",
    "\U0001f504": "[R]",
    "\U0001f5d1\ufe0f": "[-]",
    "\U0001f9fe": "[~]",
    "\u2728": "[*]",
    "\U0001f389": "[!]",
    "\u2b50": "[*]",
}


def get_console_encoding(stream: TextIO = None) -> str:
    stream = stream or sys.stdout
    encoding = getattr(stream, "encoding", None) or sys.getdefaultencoding()
    return encoding.upper() if encoding else "UTF-8"


def is_unicode_supported(stream: TextIO = None) -> bool:
    encoding = get_console_encoding(stream)
    return "UTF" in encoding or "UTF8" in encoding or "UNICODE" in encoding


def safe_str(text: str) -> str:
    if is_unicode_supported():
        return text

    result = text
    for emoji, fallback in EMOJI_FALLBACK.items():
        result = result.replace(emoji, fallback)

    try:
        result.encode(get_console_encoding(), errors="strict")
        return result
    except (UnicodeEncodeError, LookupError):
        return result.encode(get_console_encoding(), errors="replace").decode(
            get_console_encoding(), errors="replace"
        )


def safe_echo(message: str = None, file: TextIO = None, nl: bool = True, err: bool = False):
    import click

    if message is not None:
        message = safe_str(str(message))
    click.echo(message, file=file, nl=nl, err=err)


def safe_secho(message: str = None, file: TextIO = None, nl: bool = True,
               err: bool = False, **styles):
    import click

    if message is not None:
        message = safe_str(str(message))
    click.secho(message, file=file, nl=nl, err=err, **styles)


def enable_windows_utf8_mode():
    if os.name != "nt":
        return

    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32

        STD_OUTPUT_HANDLE = -11
        STD_ERROR_HANDLE = -12

        CP_UTF8 = 65001

        kernel32.SetConsoleOutputCP(CP_UTF8)
        kernel32.SetConsoleCP(CP_UTF8)

        if sys.stdout.encoding and "UTF" not in sys.stdout.encoding.upper():
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr.encoding and "UTF" not in sys.stderr.encoding.upper():
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def init_output():
    enable_windows_utf8_mode()

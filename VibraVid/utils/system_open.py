# 29.07.26
# by @ManoloZocco

"""Cross-platform helper to launch files and reveal them in OS file managers."""

import os
import platform
import subprocess


def open_file(path: str) -> tuple[bool, str]:
    """Launch a file with the system default application.

    Returns (success, message).
    """
    if not path or not os.path.exists(path):
        return False, f"File non trovato: {path}"

    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", path])
        elif system == "Windows":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])
        return True, f"File avviato: {os.path.basename(path)}"
    except Exception as e:
        return False, f"Impossibile aprire il file: {e}"


def open_folder(path: str) -> tuple[bool, str]:
    """Open the directory in the OS file manager, highlighting the file if supported.

    Returns (success, message).
    """
    if not path:
        return False, "Percorso non specificato."

    abs_path = os.path.abspath(path)
    dir_path = os.path.dirname(abs_path) if os.path.isfile(abs_path) else abs_path

    if not os.path.exists(dir_path) and not os.path.exists(abs_path):
        return False, f"Cartella non trovata: {dir_path}"

    system = platform.system()
    try:
        if system == "Darwin":
            if os.path.isfile(abs_path):
                subprocess.Popen(["open", "-R", abs_path])
            else:
                subprocess.Popen(["open", dir_path])
        elif system == "Windows":
            if os.path.isfile(abs_path):
                subprocess.Popen(["explorer.exe", f"/select,{abs_path}"])
            else:
                subprocess.Popen(["explorer.exe", dir_path])
        else:
            subprocess.Popen(["xdg-open", dir_path])
        return True, f"Cartella aperta: {dir_path}"
    except Exception as e:
        return False, f"Impossibile aprire la cartella: {e}"


def copy_to_clipboard(text: str) -> tuple[bool, str]:
    """Copy text to system clipboard (cross-platform).

    Returns (success, message).
    """
    if not text:
        return False, "Nessun testo da copiare."

    system = platform.system()
    try:
        if system == "Darwin":
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE, close_fds=True)
            p.communicate(input=text.encode("utf-8"))
            return True, "Copiato negli appunti!"
        elif system == "Windows":
            p = subprocess.Popen(["clip.exe"], stdin=subprocess.PIPE, close_fds=True)
            p.communicate(input=text.encode("utf-8"))
            return True, "Copiato negli appunti!"
        else:
            for cmd in [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]:
                try:
                    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, close_fds=True)
                    p.communicate(input=text.encode("utf-8"))
                    if p.returncode == 0:
                        return True, "Copiato negli appunti!"
                except FileNotFoundError:
                    continue
            return False, "Nessun gestore appunti trovato (installa wl-clipboard o xclip)."
    except Exception as e:
        return False, f"Impossibile copiare negli appunti: {e}"

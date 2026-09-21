import sys
import ctypes

from convert import cli
from ui import ConverterApp


def minimize_console():
    if sys.platform == "win32":
        window = ctypes.windll.kernel32.GetConsoleWindow()
        if window:
            ctypes.windll.user32.ShowWindow(window, 6)


if len(sys.argv) > 1:
    cli()
else:
    minimize_console()
    ConverterApp().mainloop()
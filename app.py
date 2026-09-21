import sys

from convert import cli
from ui import ConverterApp


if len(sys.argv) > 1:
    cli()
else:
    ConverterApp().mainloop()
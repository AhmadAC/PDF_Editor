
# app/tools/PrinterAccounting.py
"""
PDF_Editor – High-speed PDF Print Suite & Fuji Xerox ApeosPort Accounting Manager.
Facade redirecting to app.tools.printer_acct modules to maintain package limits.
"""
import sys
import os
from PySide6.QtWidgets import QApplication

from app.tools.printer_acct.manager import FujiAccountingManager

def main():
    app = QApplication.instance()
    owns_app = False
    if not app:
        app = QApplication(sys.argv)
        owns_app = True

    initial_pdf = None
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            if not arg.startswith("-") and os.path.isfile(arg):
                initial_pdf = os.path.abspath(arg)
                break

    window = FujiAccountingManager(initial_pdf=initial_pdf)
    window.showMaximized()

    if owns_app:
        sys.exit(app.exec())

if __name__ == "__main__":
    main()
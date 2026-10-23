# app/tools/printer_acct/manager_config.py
import os
import re
import socket
import subprocess
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMessageBox, QWidget

if TYPE_CHECKING:
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QLabel,
        QLineEdit,
        QPushButton,
    )
    _Base = QWidget
else:
    _Base = object


class ManagerConfigMixin(_Base):
    """Mixin handling printer discovery, socket tests, and CUPS configuration."""

    if TYPE_CHECKING:
        user_input: QLineEdit
        pass_input: QLineEdit
        acct_banner_lbl: QLabel
        ip_lbl: QLabel
        dest_ip_input: QLineEdit
        test_conn_btn: QPushButton
        cups_queue_combo: QComboBox
        send_pdf_btn: QPushButton
        printer_combo: QComboBox
        ip_input: QLineEdit
        sudo_input: QLineEdit
        account_input: QLineEdit
        convert_chk: QCheckBox

        def save_preferences(self) -> None: ...

    def update_acct_banner(self):
        u = self.user_input.text().strip()
        has_pin = bool(self.pass_input.text().strip())
        if not u and not has_pin:
            self.acct_banner_lbl.setText("Accounting: None configured")
        else:
            pin_mask = "****" if has_pin else "None"
            user_disp = u if u else "None"
            self.acct_banner_lbl.setText(f"Accounting: User [{user_disp}] | PIN [{pin_mask}]")

    def on_dest_mode_changed(self, idx: int):
        is_direct = (idx == 0)
        self.ip_lbl.setVisible(is_direct)
        self.dest_ip_input.setVisible(is_direct)
        self.test_conn_btn.setVisible(is_direct)
        self.cups_queue_combo.setVisible(not is_direct)
        if is_direct:
            self.send_pdf_btn.setText("Print")
        else:
            self.send_pdf_btn.setText("Print via CUPS Queue")

    def test_printer_ip_connection(self):
        ip = self.dest_ip_input.text().strip()
        if not ip:
            QMessageBox.warning(self, "Validation", "Please enter an IP address.")
            return
        try:
            with socket.create_connection((ip, 9100), timeout=1.5):
                QMessageBox.information(self, "Online", f"Printer at {ip}:9100 is ONLINE and ready!")
        except Exception as e:
            QMessageBox.critical(self, "Offline / Refused", f"Cannot connect to {ip}:9100:\n{e}")

    def load_printer_list(self):
        try:
            res = subprocess.run(["lpstat", "-e"], capture_output=True, text=True)
            queues = [q.strip() for q in res.stdout.splitlines() if q.strip()]
            if not queues:
                res_p = subprocess.run(["lpstat", "-p"], capture_output=True, text=True)
                queues = re.findall(r"printer\s+([^\s]+)", res_p.stdout)

            self.printer_combo.clear()
            self.printer_combo.addItems(queues)
            self.cups_queue_combo.clear()
            self.cups_queue_combo.addItems(queues)

            if "4th" in queues:
                self.printer_combo.setCurrentText("4th")
                self.cups_queue_combo.setCurrentText("4th")
            self.on_printer_selected()
        except Exception:
            self.printer_combo.addItem("4th")
            self.cups_queue_combo.addItem("4th")

    def on_printer_selected(self):
        queue = self.printer_combo.currentText()
        if not queue:
            return
        try:
            res = subprocess.run(["lpstat", "-v", queue], capture_output=True, text=True)
            m = re.search(r":\s*[a-zA-Z0-9_+.-]+://([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", res.stdout)
            if m:
                self.ip_input.setText(m.group(1))
                self.dest_ip_input.setText(m.group(1))
        except Exception:
            pass

    def run_sudo_cmd(self, cmd_args: list[str], input_data: str | None = None) -> str:
        sudo_pwd = self.sudo_input.text()
        if not sudo_pwd:
            raise ValueError("Please enter your Linux system (sudo) password in Tab 2.")

        cmd = ["sudo", "-S"] + cmd_args
        payload = f"{sudo_pwd}\n"
        if input_data:
            payload += input_data

        proc = subprocess.run(cmd, input=payload, capture_output=True, text=True)
        if proc.returncode != 0:
            err = proc.stderr.lower()
            if "incorrect password" in err or "authentication failure" in err:
                raise PermissionError("Incorrect Linux sudo password.")
            raise RuntimeError(proc.stderr.strip() or f"Command failed with code {proc.returncode}")
        return proc.stdout

    def send_direct_socket_test(self):
        ip = self.ip_input.text().strip() or self.dest_ip_input.text().strip()
        username = self.user_input.text().strip() or "testuser"
        passcode = self.pass_input.text().strip()

        if not ip:
            QMessageBox.warning(self, "Validation", "Printer IP Address is required.")
            return

        test_str = (
            "\x1b%-12345X@PJL\r\n"
            f'@PJL SET JOBATTR = "@JOAU={username}"\r\n'
            f'@PJL SET JOBATTR = "@JOAP={passcode}"\r\n'
            "@PJL ENTER LANGUAGE = POSTSCRIPT\r\n"
            "%!PS\r\n"
            "/Helvetica findfont 20 scalefont setfont\r\n"
            "100 700 moveto\r\n"
            f"(Direct Socket Test: {username} on ApeosPort) show\r\n"
            "showpage\r\n"
            "\x1b%-12345X"
        )
        payload = test_str.encode("latin-1")

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(5)
                s.connect((ip, 9100))
                s.sendall(payload)
            QMessageBox.information(self, "Sent", f"Test print sent directly to {ip}:9100.")
        except Exception as e:
            QMessageBox.critical(self, "Connection Error", f"Failed to send direct test:\n{e}")

    def apply_configuration(self):
        queue = self.printer_combo.currentText()
        ip = self.ip_input.text().strip()
        username = self.user_input.text().strip()
        passcode = self.pass_input.text().strip()
        account = self.account_input.text().strip()

        if not queue:
            QMessageBox.warning(self, "Error", "No printer queue selected.")
            return
        if not username:
            QMessageBox.warning(self, "Validation", "User ID cannot be empty.")
            return

        if self.convert_chk.isChecked() and ip:
            try:
                new_uri = f"socket://{ip}:9100"
                self.run_sudo_cmd(["lpadmin", "-p", queue, "-v", new_uri])
                self.run_sudo_cmd(["lpadmin", "-p", queue, "-o", "pdftops-renderer-default=pdftops"])
                self.run_sudo_cmd(["lpadmin", "-d", queue])
            except Exception as e:
                QMessageBox.critical(self, "Error Updating Queue", f"Could not update printer URI:\n{e}")
                return

        try:
            ppd_path = f"/etc/cups/ppd/{queue}.ppd"
            current_ppd = self.run_sudo_cmd(["cat", ppd_path])
            base_text = current_ppd.split("*Protocols: PJL")[0].rstrip()

            daid_entry = f'@PJL SET JOBATTR = <22>@DAID={account}<22><0A>' if account else ''
            user_entry = f'@PJL SET JOBATTR = <22>@JOAU={username}<22><0A>' if username else ''
            pin_entry = f'@PJL SET JOBATTR = <22>@JOAP={passcode}<22><0A>' if passcode else ''

            new_jcl = (
                "\n\n*Protocols: PJL\n"
                f'*JCLBegin: "<1B>%-12345X@PJL<0A>{user_entry}{pin_entry}{daid_entry}"\n'
                '*JCLToPSInterpreter: "@PJL ENTER LANGUAGE = POSTSCRIPT<0A>"\n'
                '*JCLToPDFInterpreter: "@PJL ENTER LANGUAGE = PDF<0A>"\n'
                '*JCLEnd: "<1B>%-12345X"\n'
            )
            full_text = base_text + new_jcl

            self.run_sudo_cmd(["tee", ppd_path], input_data=full_text)
            self.run_sudo_cmd(["systemctl", "restart", "cups"])

            self.save_preferences()

            QMessageBox.information(
                self,
                "Success",
                f"Queue '{queue}' configured:\n- URI set to socket://{ip}:9100\n- Injected PS & PDF accounting headers\n- Set as default printer\n- CUPS restarted."
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
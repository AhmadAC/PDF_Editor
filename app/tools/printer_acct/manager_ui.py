# app/tools/printer_acct/manager_ui.py
import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QGroupBox, QCheckBox, QTabWidget, QSplitter, QScrollArea, QProgressBar, QWidget
)

from .utils import NoWheelComboBox, NoWheelSpinBox, get_os_info
from .svg_assets import (
    get_svg_icon, SVG_GEAR, SVG_DOCUMENT, SVG_FOLDER, 
    SVG_CHEVRON_LEFT, SVG_CHEVRON_RIGHT, SVG_BOLT, SVG_TERMINAL, SVG_CHECK
)

try:
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtPdfWidgets import QPdfView
    HAS_QTPDF = True
except Exception:
    QPdfDocument, QPdfView = None, None
    HAS_QTPDF = False


def build_manager_ui(manager):
    root_layout = QVBoxLayout(manager)
    root_layout.setContentsMargins(12, 12, 12, 12)
    root_layout.setSpacing(8)

    # Header
    header_bar = QHBoxLayout()
    title_lbl = QLabel(f"<b>Print Suite & Manager</b> <span style='color: #7aa2f7;'>[{manager.os_title}]</span>")
    title_lbl.setStyleSheet("font-size: 11pt; color: #c0caf5;")
    header_bar.addWidget(title_lbl)
    header_bar.addStretch()

    manager.status_badge = QLabel("Engine: Ready")
    manager.status_badge.setStyleSheet("background: #24283b; color: #9ece6a; padding: 3px 8px; border-radius: 4px; font-weight: bold;")
    header_bar.addWidget(manager.status_badge)
    root_layout.addLayout(header_bar)

    manager.tabs = QTabWidget()
    root_layout.addWidget(manager.tabs)

    # Build Preview Tab
    manager.tab_preview = QWidget()
    _build_preview_tab(manager)
    manager.tabs.addTab(manager.tab_preview, get_svg_icon(SVG_DOCUMENT, "#c0caf5", 18), "Print & Preview")

    # Build Setup Tab
    manager.tab_setup = QWidget()
    _build_setup_tab(manager)
    manager.tabs.addTab(manager.tab_setup, get_svg_icon(SVG_GEAR, "#c0caf5", 18), "Printer & Accounting Setup")


def _build_preview_tab(manager):
    layout = QHBoxLayout(manager.tab_preview)
    splitter = QSplitter(Qt.Orientation.Horizontal)

    # Left Panel: Controls
    left_widget = QWidget()
    left_layout = QVBoxLayout(left_widget)
    left_layout.setSpacing(8)

    file_group = QGroupBox("Document Selection")
    f_layout = QVBoxLayout()
    manager.file_input = QLineEdit()
    manager.file_input.setPlaceholderText("Select or Drag & Drop a PDF here...")
    manager.file_input.textChanged.connect(manager.load_preview)
    f_layout.addWidget(manager.file_input)

    browse_btn = QPushButton("Browse PDF File...")
    browse_btn.setIcon(get_svg_icon(SVG_FOLDER, "#7aa2f7", 16))
    browse_btn.clicked.connect(manager.browse_pdf)
    f_layout.addWidget(browse_btn)
    file_group.setLayout(f_layout)
    left_layout.addWidget(file_group)

    dest_group = QGroupBox("Printer Destination & Mode")
    d_layout = QVBoxLayout()
    d_layout.setSpacing(6)

    dest_row = QHBoxLayout()
    dest_row.addWidget(QLabel("Target:"))
    manager.dest_mode_combo = NoWheelComboBox()
    manager.dest_mode_combo.addItems([
        "Direct Raw Socket 9100 (Instant Foxit Speed)",
        "CUPS Queue (Linux System Spooler)"
    ])
    manager.dest_mode_combo.currentIndexChanged.connect(manager.on_dest_mode_changed)
    dest_row.addWidget(manager.dest_mode_combo, 1)
    d_layout.addLayout(dest_row)

    ip_row = QHBoxLayout()
    manager.ip_lbl = QLabel("Printer IP:")
    ip_row.addWidget(manager.ip_lbl)
    manager.dest_ip_input = QLineEdit()
    manager.dest_ip_input.setPlaceholderText("e.g. 192.168.1.100")
    ip_row.addWidget(manager.dest_ip_input, 1)

    manager.test_conn_btn = QPushButton("Test")
    manager.test_conn_btn.setToolTip("Test Port 9100 connection")
    manager.test_conn_btn.clicked.connect(manager.test_printer_ip_connection)
    ip_row.addWidget(manager.test_conn_btn)
    d_layout.addLayout(ip_row)

    manager.cups_queue_combo = NoWheelComboBox()
    manager.cups_queue_combo.setVisible(False)
    d_layout.addWidget(manager.cups_queue_combo)

    manager.acct_banner_lbl = QLabel("Accounting: Not configured")
    manager.acct_banner_lbl.setStyleSheet("color: #7aa2f7; font-size: 8.5pt;")
    d_layout.addWidget(manager.acct_banner_lbl)

    dest_group.setLayout(d_layout)
    left_layout.addWidget(dest_group)

    page_group = QGroupBox("Page Range")
    pg_layout = QVBoxLayout()
    pg_layout.setSpacing(5)

    manager.page_scope_combo = NoWheelComboBox()
    manager.page_scope_combo.addItems(["All Pages", "Current Page Only", "Custom Range"])
    manager.page_scope_combo.currentIndexChanged.connect(manager.on_page_scope_changed)
    pg_layout.addWidget(manager.page_scope_combo)

    manager.custom_range_input = QLineEdit()
    manager.custom_range_input.setPlaceholderText("e.g. 1, 3, 5-8")
    manager.custom_range_input.setEnabled(False)
    pg_layout.addWidget(manager.custom_range_input)

    quick_range_row = QHBoxLayout()
    btn_all = QPushButton("All")
    btn_all.setObjectName("NavBtn")
    btn_all.clicked.connect(lambda: manager.page_scope_combo.setCurrentIndex(0))
    btn_odd = QPushButton("Odd")
    btn_odd.setObjectName("NavBtn")
    btn_odd.clicked.connect(manager.set_range_odd)
    btn_even = QPushButton("Even")
    btn_even.setObjectName("NavBtn")
    btn_even.clicked.connect(manager.set_range_even)
    quick_range_row.addWidget(btn_all)
    quick_range_row.addWidget(btn_odd)
    quick_range_row.addWidget(btn_even)
    pg_layout.addLayout(quick_range_row)
    page_group.setLayout(pg_layout)
    left_layout.addWidget(page_group)

    settings_group = QGroupBox("Layout & Quality Settings")
    s_layout = QVBoxLayout()
    s_layout.setSpacing(5)

    s_layout.addWidget(QLabel("2-Sided / Duplex:"))
    manager.duplex_combo = NoWheelComboBox()
    manager.duplex_combo.addItems(["1-Sided (Simplex)", "2-Sided (Flip on Long Edge)", "2-Sided (Flip on Short Edge)"])
    manager.duplex_combo.setCurrentIndex(1)
    manager.duplex_combo.currentIndexChanged.connect(lambda _: manager.jump_to_page(manager.current_page))
    s_layout.addWidget(manager.duplex_combo)

    s_layout.addWidget(QLabel("Paper Size:"))
    manager.paper_combo = NoWheelComboBox()
    manager.paper_combo.addItems(["A4", "A3", "Letter", "Legal"])
    manager.paper_combo.setCurrentText("A4")
    s_layout.addWidget(manager.paper_combo)

    s_layout.addWidget(QLabel("Color Mode:"))
    manager.color_combo = NoWheelComboBox()
    manager.color_combo.addItems(["Color", "Black & White (Monochrome)"])
    manager.color_combo.currentIndexChanged.connect(lambda _: manager.jump_to_page(manager.current_page))
    s_layout.addWidget(manager.color_combo)

    s_layout.addWidget(QLabel("Copies:"))
    copies_row = QHBoxLayout()
    copies_row.setContentsMargins(0, 0, 0, 0)
    manager.copies_spin = NoWheelSpinBox()
    manager.copies_spin.setRange(1, 99)
    manager.copies_spin.setValue(1)
    copies_row.addWidget(manager.copies_spin)

    manager.chk_collate = QCheckBox("Collate")
    manager.chk_collate.setChecked(True)
    copies_row.addWidget(manager.chk_collate)
    copies_row.addStretch()

    s_layout.addLayout(copies_row)
    settings_group.setLayout(s_layout)
    left_layout.addWidget(settings_group)
    left_layout.addStretch()

    manager.print_progress = QProgressBar()
    manager.print_progress.setVisible(False)
    manager.print_progress.setFixedHeight(14)
    left_layout.addWidget(manager.print_progress)

    manager.send_pdf_btn = QPushButton("Print")
    manager.send_pdf_btn.setObjectName("ActionBtn")
    manager.send_pdf_btn.setIcon(get_svg_icon(SVG_BOLT, "#1a1b26", 18))
    manager.send_pdf_btn.clicked.connect(manager.execute_print_job)
    left_layout.addWidget(manager.send_pdf_btn)

    splitter.addWidget(left_widget)

    # Right Panel: Preview
    right_widget = QWidget()
    right_layout = QVBoxLayout(right_widget)

    nav_bar = QHBoxLayout()
    manager.doc_name_lbl = QLabel("No document loaded.")
    nav_bar.addWidget(manager.doc_name_lbl)
    nav_bar.addStretch()

    manager.prev_btn = QPushButton("Prev")
    manager.prev_btn.setObjectName("NavBtn")
    manager.prev_btn.setIcon(get_svg_icon(SVG_CHEVRON_LEFT, "#c0caf5", 14))
    manager.prev_btn.clicked.connect(manager.go_prev_page)
    nav_bar.addWidget(manager.prev_btn)

    manager.page_indicator_lbl = QLabel("Page: - / -")
    manager.page_indicator_lbl.setStyleSheet("font-weight: bold; color: #7aa2f7; padding: 0 10px;")
    nav_bar.addWidget(manager.page_indicator_lbl)

    manager.next_btn = QPushButton("Next")
    manager.next_btn.setObjectName("NavBtn")
    manager.next_btn.setIcon(get_svg_icon(SVG_CHEVRON_RIGHT, "#c0caf5", 14))
    manager.next_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    manager.next_btn.clicked.connect(manager.go_next_page)
    nav_bar.addWidget(manager.next_btn)

    right_layout.addLayout(nav_bar)

    if HAS_QTPDF and QPdfDocument is not None and QPdfView is not None:
        manager.pdf_doc = QPdfDocument(manager)
        manager.pdf_view = QPdfView(manager)
        manager.pdf_view.setDocument(manager.pdf_doc)
        manager.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
        manager.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        manager.nav = manager.pdf_view.pageNavigator()
        manager.nav.currentPageChanged.connect(manager.on_qtpdf_page_changed)
        right_layout.addWidget(manager.pdf_view)
    else:
        manager.scroll_area = QScrollArea()
        manager.preview_lbl = QLabel("Loading preview engine...")
        manager.preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        manager.scroll_area.setWidget(manager.preview_lbl)
        manager.scroll_area.setWidgetResizable(True)
        right_layout.addWidget(manager.scroll_area)

    splitter.addWidget(right_widget)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)

    layout.addWidget(splitter)


def _build_setup_tab(manager):
    layout = QVBoxLayout(manager.tab_setup)
    layout.setSpacing(12)

    p_group = QGroupBox("Target Printer & Network Defaults")
    p_layout = QVBoxLayout()

    p_layout.addWidget(QLabel("CUPS Queue:"))
    manager.printer_combo = NoWheelComboBox()
    manager.printer_combo.currentIndexChanged.connect(manager.on_printer_selected)
    p_layout.addWidget(manager.printer_combo)

    p_layout.addWidget(QLabel("Default Printer IP Address:"))
    manager.ip_input = QLineEdit()
    manager.ip_input.setPlaceholderText("e.g. 192.168.1.100")
    manager.ip_input.textChanged.connect(lambda txt: manager.dest_ip_input.setText(txt))
    p_layout.addWidget(manager.ip_input)

    manager.convert_chk = QCheckBox("Switch CUPS queue from LPD to socket://<IP>:9100 on apply")
    manager.convert_chk.setChecked(True)
    p_layout.addWidget(manager.convert_chk)

    p_group.setLayout(p_layout)
    layout.addWidget(p_group)

    cred_group = QGroupBox("Accounting Credentials (Auditron / XSA)")
    c_layout = QVBoxLayout()

    c_layout.addWidget(QLabel("User ID / Login Name:"))
    manager.user_input = QLineEdit()
    manager.user_input.setPlaceholderText("Your printer accounting login")
    manager.user_input.textChanged.connect(manager.update_acct_banner)
    c_layout.addWidget(manager.user_input)

    c_layout.addWidget(QLabel("Passcode / PIN:"))
    manager.pass_input = QLineEdit()
    manager.pass_input.setPlaceholderText("Your printer accounting passcode")
    manager.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
    manager.pass_input.textChanged.connect(manager.update_acct_banner)

    p_row = QHBoxLayout()
    p_row.addWidget(manager.pass_input)
    manager.btn_toggle_pin = QPushButton("Show / Hide")
    manager.btn_toggle_pin.setCheckable(True)
    manager.btn_toggle_pin.toggled.connect(
        lambda c: manager.pass_input.setEchoMode(QLineEdit.EchoMode.Normal if c else QLineEdit.EchoMode.Password)
    )
    p_row.addWidget(manager.btn_toggle_pin)
    c_layout.addLayout(p_row)

    c_layout.addWidget(QLabel("Account ID (Optional):"))
    manager.account_input = QLineEdit()
    manager.account_input.setPlaceholderText("Leave blank unless required by administrator")
    manager.account_input.textChanged.connect(manager.update_acct_banner)
    c_layout.addWidget(manager.account_input)

    cred_group.setLayout(c_layout)
    layout.addWidget(cred_group)

    auth_group = QGroupBox("System Sudo Authorization (For Updating CUPS Config on Linux)")
    a_layout = QVBoxLayout()

    a_layout.addWidget(QLabel("Linux Sudo Password:"))
    manager.sudo_input = QLineEdit()
    manager.sudo_input.setEchoMode(QLineEdit.EchoMode.Password)
    manager.sudo_input.setPlaceholderText("Enter Linux sudo password to modify CUPS PPD")

    s_row = QHBoxLayout()
    s_row.addWidget(manager.sudo_input)
    manager.btn_toggle_sudo = QPushButton("Show / Hide")
    manager.btn_toggle_sudo.setCheckable(True)
    manager.btn_toggle_sudo.toggled.connect(
        lambda c: manager.sudo_input.setEchoMode(QLineEdit.EchoMode.Normal if c else QLineEdit.EchoMode.Password)
    )
    s_row.addWidget(manager.btn_toggle_sudo)
    a_layout.addLayout(s_row)

    btn_row = QHBoxLayout()
    manager.test_socket_btn = QPushButton("Test Socket PS")
    manager.test_socket_btn.setIcon(get_svg_icon(SVG_TERMINAL, "#c0caf5", 16))
    manager.test_socket_btn.clicked.connect(manager.send_direct_socket_test)
    btn_row.addWidget(manager.test_socket_btn)

    manager.save_btn = QPushButton("Save & Apply CUPS Settings")
    manager.save_btn.setObjectName("PrimaryBtn")
    manager.save_btn.setIcon(get_svg_icon(SVG_CHECK, "#1a1b26", 16))
    manager.save_btn.clicked.connect(manager.apply_configuration)
    btn_row.addWidget(manager.save_btn)

    a_layout.addLayout(btn_row)
    auth_group.setLayout(a_layout)
    layout.addWidget(auth_group)
    layout.addStretch()
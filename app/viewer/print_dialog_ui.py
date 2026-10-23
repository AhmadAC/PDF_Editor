# app/viewer/print_dialog_ui.py
"""PDF_Editor – UI generation and stylesheets for the Print Dialog."""
import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QProgressBar, QPushButton, QRadioButton, QScrollArea,
    QSizePolicy, QTabWidget, QVBoxLayout, QWidget
)
from app.constants import ACCENT, BORDER, TEXT_PRI, TEXT_SEC
from app.viewer.print_utils import NoWheelComboBox, NoWheelSpinBox, _get_os_badge


def apply_dialog_stylesheet(dialog):
    dialog.setStyleSheet("""
        QDialog#print_dialog { background-color: #1E1F22; color: #F0F0F0; }
        QDialog#print_dialog QScrollArea { background: transparent; border: none; }
        QDialog#print_dialog QTabWidget::pane { background-color: #26282D; border: 1px solid #3E4249; border-radius: 6px; top: -1px; }
        QDialog#print_dialog QTabBar::tab { background-color: #1E1F22; color: #A0A0A0; border: 1px solid #3E4249; border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; padding: 7px 18px; margin-right: 4px; font-weight: 600; font-size: 9.5pt; }
        QDialog#print_dialog QTabBar::tab:selected { background-color: #26282D; color: #FFFFFF; border-color: #3E4249; border-bottom: 2px solid #0078D4; }
        QDialog#print_dialog QTabBar::tab:hover:!selected { background-color: #2B2D33; color: #E0E0E0; }
        QDialog#print_dialog QGroupBox { background-color: #26282D; border: 1px solid #3E4249; border-radius: 6px; margin-top: 14px; padding: 14px 14px 12px 14px; font-weight: 600; color: #A0A0A0; }
        QDialog#print_dialog QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 10px; padding: 0 4px; background-color: #26282D; color: #CCCCCC; }
        QDialog#print_dialog QLineEdit, QDialog#print_dialog QComboBox, QDialog#print_dialog QSpinBox { background-color: #1E1F22; border: 1px solid #3E4249; border-radius: 4px; padding: 4px 8px; color: #F0F0F0; min-height: 26px; }
        QDialog#print_dialog QLineEdit:focus, QDialog#print_dialog QComboBox:focus, QDialog#print_dialog QSpinBox:focus { border: 1px solid #0078D4; }
        QDialog#print_dialog QPushButton { background-color: #383A40; border: 1px solid #4E5157; border-radius: 4px; padding: 4px 12px; color: #F0F0F0; min-height: 26px; font-size: 10pt; }
        QDialog#print_dialog QPushButton#nav_page_btn { min-height: 22px; max-height: 26px; padding: 2px 4px; font-size: 9pt; }
        QDialog#print_dialog QPushButton:hover { background-color: #43464D; border-color: #0078D4; }
        QDialog#print_dialog QPushButton#btn_primary { background-color: #0078D4; border: 1px solid #106EBE; color: #FFFFFF; font-weight: bold; font-size: 10.5pt; }
        QDialog#print_dialog QPushButton#btn_primary:hover { background-color: #106EBE; }
        QDialog#print_dialog QRadioButton { color: #F0F0F0; spacing: 10px; background: transparent; font-size: 10pt; }
        QDialog#print_dialog QRadioButton::indicator { width: 16px; height: 16px; border-radius: 9px; border: 1.5px solid #64748B; background-color: #1E1F22; }
        QDialog#print_dialog QRadioButton::indicator:hover { border-color: #0078D4; }
        QDialog#print_dialog QRadioButton::indicator:checked { border: 2px solid #0078D4; background-color: #1E1F22; image: none; }
        QDialog#print_dialog QRadioButton::indicator:checked { background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 #0078D4, stop:0.55 #0078D4, stop:0.6 transparent); }
        QDialog#print_dialog QCheckBox { color: #F0F0F0; spacing: 8px; background: transparent; }
        QDialog#print_dialog QLabel { background: transparent; color: #F0F0F0; }
        QDialog#print_dialog QScrollBar:vertical { width: 8px; background: transparent; margin: 0; }
        QDialog#print_dialog QScrollBar::handle:vertical { background: #4E5157; border-radius: 4px; min-height: 20px; }
        QDialog#print_dialog QScrollBar::handle:vertical:hover { background: #636770; }
        QDialog#print_dialog QScrollBar::add-line:vertical, QDialog#print_dialog QScrollBar::sub-line:vertical { height: 0px; }
    """)


def setup_print_dialog_ui(dialog):
    main_lay = QHBoxLayout(dialog)
    main_lay.setContentsMargins(14, 12, 14, 12)
    main_lay.setSpacing(14)

    left_box = QWidget()
    v_left = QVBoxLayout(left_box)
    v_left.setContentsMargins(0, 0, 0, 0)
    v_left.setSpacing(6)

    top_hdr = QHBoxLayout()
    lbl_doc = QLabel(f"<b>{os.path.basename(dialog.doc_path)}</b>")
    lbl_doc.setStyleSheet(f"font-size: 11pt; color: {TEXT_PRI};")
    top_hdr.addWidget(lbl_doc)
    top_hdr.addStretch()

    badge_txt = _get_os_badge()
    os_badge = QLabel(badge_txt)
    os_badge.setStyleSheet(f"background: #24283B; color: {ACCENT}; border: 1px solid {BORDER}; border-radius: 4px; padding: 3px 8px; font-weight: bold; font-size: 8.5pt;")
    top_hdr.addWidget(os_badge)
    v_left.addLayout(top_hdr)

    dialog.preview_scroll = QScrollArea()
    dialog.preview_scroll.setWidgetResizable(True)
    dialog.preview_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dialog.preview_scroll.setStyleSheet("background: #121316; border: 1px solid #333842; border-radius: 8px;")

    dialog.lbl_preview_img = QLabel("Loading preview...")
    dialog.lbl_preview_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dialog.lbl_preview_img.setStyleSheet("background: transparent;")
    dialog.preview_scroll.setWidget(dialog.lbl_preview_img)
    v_left.addWidget(dialog.preview_scroll, 1)

    nav_h = QHBoxLayout()
    nav_h.setContentsMargins(0, 2, 0, 2)
    nav_h.setSpacing(6)

    dialog.btn_first_page = QPushButton("<<")
    dialog.btn_first_page.setObjectName("nav_page_btn")
    dialog.btn_first_page.setFixedWidth(36)
    dialog.btn_first_page.setFixedHeight(26)
    dialog.btn_first_page.setToolTip("First Page (Page 1)")
    dialog.btn_first_page.clicked.connect(dialog._first_preview_page)
    nav_h.addWidget(dialog.btn_first_page)

    dialog.btn_prev_page = QPushButton("< Prev")
    dialog.btn_prev_page.setObjectName("nav_page_btn")
    dialog.btn_prev_page.setFixedWidth(56)
    dialog.btn_prev_page.setFixedHeight(26)
    dialog.btn_prev_page.setToolTip("Previous Page")
    dialog.btn_prev_page.clicked.connect(dialog._prev_preview_page)
    nav_h.addWidget(dialog.btn_prev_page)

    dialog.lbl_page_count = QLabel(f"Page {dialog.current_preview_page + 1} of {dialog.total_pages}")
    dialog.lbl_page_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dialog.lbl_page_count.setStyleSheet(f"color: {ACCENT}; font-weight: bold; font-size: 9.5pt;")
    nav_h.addWidget(dialog.lbl_page_count, 1)

    dialog.btn_next_page = QPushButton("Next >")
    dialog.btn_next_page.setObjectName("nav_page_btn")
    dialog.btn_next_page.setFixedWidth(56)
    dialog.btn_next_page.setFixedHeight(26)
    dialog.btn_next_page.setToolTip("Next Page")
    dialog.btn_next_page.clicked.connect(dialog._next_preview_page)
    nav_h.addWidget(dialog.btn_next_page)

    dialog.btn_last_page = QPushButton(">>")
    dialog.btn_last_page.setObjectName("nav_page_btn")
    dialog.btn_last_page.setFixedWidth(36)
    dialog.btn_last_page.setFixedHeight(26)
    dialog.btn_last_page.setToolTip(f"Last Page (Page {dialog.total_pages})")
    dialog.btn_last_page.clicked.connect(dialog._last_preview_page)
    nav_h.addWidget(dialog.btn_last_page)

    v_left.addLayout(nav_h)
    main_lay.addWidget(left_box, 6)

    right_container = QWidget()
    right_container.setMinimumWidth(440)
    right_container_layout = QVBoxLayout(right_container)
    right_container_layout.setContentsMargins(0, 0, 0, 0)
    right_container_layout.setSpacing(8)

    dialog.tab_widget = QTabWidget()
    dialog.tab_widget.setObjectName("print_tabs")

    tab_settings = QWidget()
    tab_settings_layout = QVBoxLayout(tab_settings)
    tab_settings_layout.setContentsMargins(0, 0, 0, 0)
    tab_settings_layout.setSpacing(0)

    scroll_settings = QScrollArea()
    scroll_settings.setWidgetResizable(True)
    scroll_settings.setFrameShape(QFrame.Shape.NoFrame)
    scroll_settings.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll_settings.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    box_settings = QWidget()
    box_settings.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding)
    v_settings = QVBoxLayout(box_settings)
    v_settings.setContentsMargins(8, 8, 12, 8)
    v_settings.setSpacing(12)

    grp_dest = QGroupBox("Printer Destination")
    f_dest = QFormLayout(grp_dest)
    f_dest.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
    f_dest.setVerticalSpacing(10)
    f_dest.setHorizontalSpacing(12)
    f_dest.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)

    dialog.cmb_printer = NoWheelComboBox()
    dialog.cmb_printer.setMinimumHeight(30)
    dialog.cmb_printer.currentIndexChanged.connect(dialog._on_printer_changed)
    f_dest.addRow("Printer:", dialog.cmb_printer)

    dialog.lbl_direct_socket_hint = QLabel(" ")
    dialog.lbl_direct_socket_hint.setWordWrap(True)
    dialog.lbl_direct_socket_hint.setStyleSheet(f"color: {ACCENT}; font-size: 9pt;")
    f_dest.addRow("", dialog.lbl_direct_socket_hint)
    v_settings.addWidget(grp_dest)

    grp_range = QGroupBox("Page Range")
    v_range = QVBoxLayout(grp_range)
    v_range.setSpacing(10)

    dialog.rad_all = QRadioButton(f"All Pages (1 - {dialog.total_pages})")
    dialog.rad_all.setMinimumHeight(24)
    dialog.rad_current = QRadioButton(f"Current Page (Page {dialog.current_preview_page + 1})")
    dialog.rad_current.setMinimumHeight(24)
    dialog.rad_custom = QRadioButton("Custom Range:")
    dialog.rad_custom.setMinimumHeight(24)

    dialog.bg_range = QButtonGroup(dialog)
    dialog.bg_range.addButton(dialog.rad_all, 0)
    dialog.bg_range.addButton(dialog.rad_current, 1)
    dialog.bg_range.addButton(dialog.rad_custom, 2)
    dialog.rad_all.setChecked(True)

    if dialog.initial_pages and len(dialog.initial_pages) > 0 and len(dialog.initial_pages) != dialog.total_pages:
        dialog.rad_custom.setChecked(True)

    v_range.addWidget(dialog.rad_all)
    v_range.addWidget(dialog.rad_current)

    custom_h = QHBoxLayout()
    custom_h.setContentsMargins(0, 0, 0, 0)
    custom_h.setSpacing(8)
    custom_h.addWidget(dialog.rad_custom)
    dialog.edit_range = QLineEdit()
    dialog.edit_range.setMinimumHeight(30)
    dialog.edit_range.setPlaceholderText("e.g. 1-3, 5, 8")
    if dialog.initial_pages:
        dialog.edit_range.setText(", ".join(str(p + 1) for p in dialog.initial_pages))
    custom_h.addWidget(dialog.edit_range, 1)
    v_range.addLayout(custom_h)

    quick_row = QHBoxLayout()
    quick_row.setContentsMargins(0, 0, 0, 0)
    quick_row.setSpacing(8)
    btn_odd = QPushButton("Odd Pages")
    btn_odd.setMinimumHeight(28)
    btn_odd.clicked.connect(dialog._set_range_odd)
    btn_even = QPushButton("Even Pages")
    btn_even.setMinimumHeight(28)
    btn_even.clicked.connect(dialog._set_range_even)
    quick_row.addWidget(btn_odd)
    quick_row.addWidget(btn_even)
    quick_row.addStretch()
    v_range.addLayout(quick_row)

    dialog.bg_range.idClicked.connect(lambda _: dialog._update_preview())
    dialog.edit_range.textChanged.connect(lambda _: dialog._update_preview() if dialog.rad_custom.isChecked() else None)
    v_settings.addWidget(grp_range)

    grp_opts = QGroupBox("Layout && Finishing")
    f_opts = QFormLayout(grp_opts)
    f_opts.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
    f_opts.setVerticalSpacing(10)
    f_opts.setHorizontalSpacing(12)
    f_opts.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)

    copies_h = QHBoxLayout()
    copies_h.setContentsMargins(0, 0, 0, 0)
    copies_h.setSpacing(10)
    dialog.spin_copies = NoWheelSpinBox()
    dialog.spin_copies.setMinimumHeight(30)
    dialog.spin_copies.setRange(1, 999)
    dialog.spin_copies.setValue(getattr(dialog, "saved_copies", 1))
    copies_h.addWidget(dialog.spin_copies)
    
    dialog.chk_collate = QCheckBox("Collate")
    dialog.chk_collate.setMinimumHeight(30)
    dialog.chk_collate.setChecked(getattr(dialog, "saved_collate", True))
    copies_h.addWidget(dialog.chk_collate)
    copies_h.addStretch()
    copies_w = QWidget()
    copies_w.setLayout(copies_h)
    f_opts.addRow("Copies:", copies_w)

    dialog.cmb_duplex = NoWheelComboBox()
    dialog.cmb_duplex.setMinimumHeight(30)
    dialog.cmb_duplex.addItems(["1-Sided (Simplex)", "2-Sided (Flip on Long Edge)", "2-Sided (Flip on Short Edge)"])
    idx_dup = dialog.cmb_duplex.findText(dialog.saved_duplex)
    if idx_dup >= 0:
        dialog.cmb_duplex.setCurrentIndex(idx_dup)
    dialog.cmb_duplex.currentIndexChanged.connect(lambda _: dialog._update_preview())
    f_opts.addRow("Duplex:", dialog.cmb_duplex)

    dialog.cmb_paper = NoWheelComboBox()
    dialog.cmb_paper.setMinimumHeight(30)
    dialog.cmb_paper.addItems(["A4", "A3", "Letter", "Legal", "A5"])
    idx_paper = dialog.cmb_paper.findText(dialog.saved_paper)
    if idx_paper >= 0:
        dialog.cmb_paper.setCurrentIndex(idx_paper)
    dialog.cmb_paper.currentIndexChanged.connect(lambda _: dialog._update_preview())
    f_opts.addRow("Paper Size:", dialog.cmb_paper)

    dialog.cmb_color = NoWheelComboBox()
    dialog.cmb_color.setMinimumHeight(30)
    dialog.cmb_color.addItems(["Color", "Black & White (Grayscale)"])
    idx_col = dialog.cmb_color.findText(dialog.saved_color)
    if idx_col >= 0:
        dialog.cmb_color.setCurrentIndex(idx_col)
    dialog.cmb_color.currentIndexChanged.connect(lambda _: dialog._update_preview())
    f_opts.addRow("Color Mode:", dialog.cmb_color)

    dialog.cmb_orient = NoWheelComboBox()
    dialog.cmb_orient.setMinimumHeight(30)
    dialog.cmb_orient.addItems(["Auto (Match PDF)", "Portrait", "Landscape"])
    dialog.cmb_orient.currentIndexChanged.connect(lambda _: dialog._update_preview())
    f_opts.addRow("Orientation:", dialog.cmb_orient)

    v_settings.addWidget(grp_opts)
    v_settings.addStretch()

    scroll_settings.setWidget(box_settings)
    tab_settings_layout.addWidget(scroll_settings)
    dialog.tab_widget.addTab(tab_settings, "Print Settings")

    tab_accounting = QWidget()
    tab_accounting_layout = QVBoxLayout(tab_accounting)
    tab_accounting_layout.setContentsMargins(0, 0, 0, 0)
    tab_accounting_layout.setSpacing(0)

    scroll_accounting = QScrollArea()
    scroll_accounting.setWidgetResizable(True)
    scroll_accounting.setFrameShape(QFrame.Shape.NoFrame)
    scroll_accounting.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll_accounting.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    box_accounting = QWidget()
    box_accounting.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding)
    v_accounting = QVBoxLayout(box_accounting)
    v_accounting.setContentsMargins(8, 8, 12, 8)
    v_accounting.setSpacing(12)

    grp_net = QGroupBox("Printer Network Address")
    f_net = QFormLayout(grp_net)
    f_net.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
    f_net.setVerticalSpacing(10)
    f_net.setHorizontalSpacing(12)
    f_net.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)

    ip_row = QHBoxLayout()
    ip_row.setContentsMargins(0, 0, 0, 0)
    ip_row.setSpacing(8)
    dialog.edit_ip = QLineEdit(dialog.saved_ip)
    dialog.edit_ip.setMinimumHeight(30)
    dialog.edit_ip.setPlaceholderText("e.g. 172.31.2.14")
    ip_row.addWidget(dialog.edit_ip, 1)

    dialog.btn_test_ip = QPushButton("Test Port 9100")
    dialog.btn_test_ip.setMinimumHeight(30)
    dialog.btn_test_ip.setToolTip("Verify high-speed port connectivity")
    dialog.btn_test_ip.clicked.connect(dialog._test_printer_ip)
    ip_row.addWidget(dialog.btn_test_ip)

    net_ip_widget = QWidget()
    net_ip_widget.setLayout(ip_row)
    f_net.addRow("Network IP:", net_ip_widget)

    lbl_ip_hint = QLabel("Used for direct, instant socket transmission on port 9100.")
    lbl_ip_hint.setStyleSheet(f"color: {TEXT_SEC}; font-size: 8.5pt;")
    f_net.addRow("", lbl_ip_hint)
    v_accounting.addWidget(grp_net)

    grp_acct = QGroupBox("Printer Accounting (Fuji Xerox ApeosPort / Auditron)")
    f_acct = QFormLayout(grp_acct)
    f_acct.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
    f_acct.setVerticalSpacing(10)
    f_acct.setHorizontalSpacing(12)
    f_acct.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)

    lbl_acct_hint = QLabel("Configure user credentials for print job tracking. Credentials are automatically injected into the Port 9100 stream and CUPS jobs via JCL/PJL attributes.")
    lbl_acct_hint.setWordWrap(True)
    lbl_acct_hint.setStyleSheet(f"color: {TEXT_SEC}; font-size: 9pt; padding-bottom: 6px;")
    f_acct.addRow(lbl_acct_hint)

    dialog.edit_acct_user = QLineEdit(dialog.acct_user)
    dialog.edit_acct_user.setMinimumHeight(30)
    dialog.edit_acct_user.setPlaceholderText("User ID or account name")
    f_acct.addRow("User ID:", dialog.edit_acct_user)

    pin_row = QHBoxLayout()
    pin_row.setContentsMargins(0, 0, 0, 0)
    pin_row.setSpacing(8)
    dialog.edit_acct_pin = QLineEdit(dialog.acct_pin)
    dialog.edit_acct_pin.setMinimumHeight(30)
    dialog.edit_acct_pin.setEchoMode(QLineEdit.EchoMode.Password)
    pin_row.addWidget(dialog.edit_acct_pin, 1)

    dialog.btn_toggle_pin = QPushButton("Show")
    dialog.btn_toggle_pin.setFixedWidth(64)
    dialog.btn_toggle_pin.setMinimumHeight(30)
    dialog.btn_toggle_pin.setCheckable(True)
    dialog.btn_toggle_pin.toggled.connect(
        lambda checked: (
            dialog.edit_acct_pin.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password),
            dialog.btn_toggle_pin.setText("Hide" if checked else "Show")
        )
    )
    pin_row.addWidget(dialog.btn_toggle_pin)

    pin_widget = QWidget()
    pin_widget.setLayout(pin_row)
    f_acct.addRow("Passcode / PIN:", pin_widget)

    dialog.edit_acct_id = QLineEdit(dialog.acct_id)
    dialog.edit_acct_id.setMinimumHeight(30)
    dialog.edit_acct_id.setPlaceholderText("Optional Account ID")
    f_acct.addRow("Account ID:", dialog.edit_acct_id)

    v_accounting.addWidget(grp_acct)
    v_accounting.addStretch()

    scroll_accounting.setWidget(box_accounting)
    tab_accounting_layout.addWidget(scroll_accounting)
    dialog.tab_widget.addTab(tab_accounting, "Printer Accounting")

    right_container_layout.addWidget(dialog.tab_widget, 1)

    bottom_box = QWidget()
    bottom_layout = QVBoxLayout(bottom_box)
    bottom_layout.setContentsMargins(0, 2, 8, 2)
    bottom_layout.setSpacing(6)

    dialog.prog_bar = QProgressBar()
    dialog.prog_bar.setFixedHeight(12)
    dialog.prog_bar.setVisible(False)
    bottom_layout.addWidget(dialog.prog_bar)

    dialog.lbl_status = QLabel("")
    dialog.lbl_status.setStyleSheet(f"color: {ACCENT}; font-size: 9pt;")
    bottom_layout.addWidget(dialog.lbl_status)

    act_h = QHBoxLayout()
    act_h.setContentsMargins(0, 0, 0, 0)
    act_h.setSpacing(10)
    act_h.addStretch()

    dialog.btn_cancel = QPushButton("Cancel")
    dialog.btn_cancel.setMinimumHeight(34)
    dialog.btn_cancel.setFixedWidth(100)
    dialog.btn_cancel.clicked.connect(dialog.reject)
    act_h.addWidget(dialog.btn_cancel)

    dialog.btn_print = QPushButton("Print")
    dialog.btn_print.setObjectName("btn_primary")
    dialog.btn_print.setMinimumHeight(34)
    dialog.btn_print.clicked.connect(dialog._start_print_job)
    act_h.addWidget(dialog.btn_print)

    bottom_layout.addLayout(act_h)
    right_container_layout.addWidget(bottom_box)

    main_lay.addWidget(right_container, 5)
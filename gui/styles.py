"""
CoastTideX 界面主题样式与色彩规范 (QSS Stylesheet)
提供现代、扁平、科技感的主题风格。
"""

DARK_THEME_QSS = """
/* 全局基础设置 */
QWidget {
    background-color: #1a1d24;
    color: #e2e8f0;
    font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 13px;
}

/* 标题栏与分组框 */
QGroupBox {
    border: 1px solid #2d3748;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 14px;
    background-color: #20242e;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: #38bdf8;
}

/* 按钮设计 */
QPushButton {
    background-color: #2563eb;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #3b82f6;
}
QPushButton:pressed {
    background-color: #1d4ed8;
}
QPushButton:disabled {
    background-color: #475569;
    color: #94a3b8;
}

QPushButton#btn_secondary {
    background-color: #334155;
    color: #f1f5f9;
}
QPushButton#btn_secondary:hover {
    background-color: #475569;
}

QPushButton#btn_success {
    background-color: #059669;
}
QPushButton#btn_success:hover {
    background-color: #10b981;
}

/* 输入框与下拉框 */
QLineEdit, QDateTimeEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #0f172a;
    border: 1px solid #334155;
    border-radius: 5px;
    padding: 6px 8px;
    color: #f8fafc;
    selection-background-color: #2563eb;
}
QLineEdit:focus, QDateTimeEdit:focus, QComboBox:focus {
    border: 1px solid #38bdf8;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: #0f172a;
    border: 1px solid #334155;
    selection-background-color: #2563eb;
    color: #f8fafc;
}

/* 选项卡 Tab Widget */
QTabWidget::pane {
    border: 1px solid #2d3748;
    background-color: #1a1d24;
    border-radius: 6px;
}
QTabBar::tab {
    background-color: #20242e;
    color: #94a3b8;
    border: 1px solid #2d3748;
    border-bottom: none;
    padding: 10px 20px;
    font-weight: 600;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 4px;
}
QTabBar::tab:selected {
    background-color: #1a1d24;
    color: #38bdf8;
    border-bottom: 2px solid #38bdf8;
}
QTabBar::tab:hover {
    background-color: #28303f;
    color: #e2e8f0;
}

/* 表格控件 */
QTableWidget {
    background-color: #0f172a;
    border: 1px solid #2d3748;
    border-radius: 6px;
    gridline-color: #1e293b;
    selection-background-color: #1e3a8a;
    selection-color: #ffffff;
}
QHeaderView::section {
    background-color: #1e293b;
    color: #94a3b8;
    padding: 6px;
    border: 1px solid #0f172a;
    font-weight: 600;
}

/* 滚动条 */
QScrollBar:vertical {
    border: none;
    background-color: #1a1d24;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #334155;
    border-radius: 4px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background-color: #475569;
}

/* 进度条 */
QProgressBar {
    border: 1px solid #334155;
    border-radius: 5px;
    text-align: center;
    background-color: #0f172a;
    color: #f8fafc;
}
QProgressBar::chunk {
    background-color: #0284c7;
    border-radius: 4px;
}

/* 单选框与复选框 */
QRadioButton, QCheckBox {
    spacing: 8px;
}
QRadioButton::indicator, QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 8px;
    border: 1px solid #475569;
    background-color: #0f172a;
}
QRadioButton::indicator:checked, QCheckBox::indicator:checked {
    background-color: #38bdf8;
    border: 2px solid #0284c7;
}

/* 状态栏与菜单栏 */
QStatusBar {
    background-color: #0f172a;
    color: #94a3b8;
    border-top: 1px solid #2d3748;
}
QMenuBar {
    background-color: #1a1d24;
    border-bottom: 1px solid #2d3748;
}
QMenuBar::item:selected {
    background-color: #28303f;
    border-radius: 4px;
}
QMenu {
    background-color: #20242e;
    border: 1px solid #334155;
}
QMenu::item:selected {
    background-color: #2563eb;
}
"""

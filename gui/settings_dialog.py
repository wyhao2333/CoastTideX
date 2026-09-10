"""
CoastTideX 设置与数据源管理对话框 (Settings Dialog)
提供对 FES2022b 网格、MDT 数据及 EGM2008 文件的可视化路径配置与连通性检验。
"""

import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QMessageBox, QGroupBox
)
from core.utils import load_app_config, save_app_config


class SettingsDialog(QDialog):
    """数据源配置弹窗"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("数据源路径与系统设置 - CoastTideX")
        self.resize(650, 360)
        self.config = load_app_config()

        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(14)

        # 1. 数据路径分组
        grp_paths = QGroupBox("模型与基准核心数据路径配置")
        layout_paths = QVBoxLayout(grp_paths)
        layout_paths.setSpacing(10)

        # FES2022b NS Grid
        layout_fes = QHBoxLayout()
        layout_fes.addWidget(QLabel("FES2022b 原生网格 (.nc):"))
        self.edit_fes = QLineEdit(self.config['paths'].get('fes_ns_grid', ''))
        btn_fes = QPushButton("浏览...")
        btn_fes.setObjectName("btn_secondary")
        btn_fes.clicked.connect(self._browse_fes)
        layout_fes.addWidget(self.edit_fes)
        layout_fes.addWidget(btn_fes)
        layout_paths.addLayout(layout_fes)

        # MDT NetCDF
        layout_mdt = QHBoxLayout()
        layout_mdt.addWidget(QLabel("CNES-CLS22 MDT (.nc):"))
        self.edit_mdt = QLineEdit(self.config['paths'].get('mdt_nc', ''))
        btn_mdt = QPushButton("浏览...")
        btn_mdt.setObjectName("btn_secondary")
        btn_mdt.clicked.connect(self._browse_mdt)
        layout_mdt.addWidget(self.edit_mdt)
        layout_mdt.addWidget(btn_mdt)
        layout_paths.addLayout(layout_mdt)

        # EGM2008 GeoTIFF
        layout_egm = QHBoxLayout()
        layout_egm.addWidget(QLabel("EGM2008 栅格 (.tif):"))
        self.edit_egm = QLineEdit(self.config['paths'].get('egm2008_tif', ''))
        btn_egm = QPushButton("浏览...")
        btn_egm.setObjectName("btn_secondary")
        btn_egm.clicked.connect(self._browse_egm)
        layout_egm.addWidget(self.edit_egm)
        layout_egm.addWidget(btn_egm)
        layout_paths.addLayout(layout_egm)

        main_layout.addWidget(grp_paths)

        # 2. 状态校验按钮
        layout_check = QHBoxLayout()
        self.btn_validate = QPushButton("🔍 校验所有数据文件有效性")
        self.btn_validate.setObjectName("btn_secondary")
        self.btn_validate.clicked.connect(self._validate_paths)
        layout_check.addWidget(self.btn_validate)
        layout_check.addStretch()
        main_layout.addLayout(layout_check)

        # 3. 底部确定/取消按钮
        layout_bottom = QHBoxLayout()
        layout_bottom.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("btn_secondary")
        btn_cancel.clicked.connect(self.reject)

        btn_save = QPushButton("保存设置")
        btn_save.clicked.connect(self._save_settings)

        layout_bottom.addWidget(btn_cancel)
        layout_bottom.addWidget(btn_save)
        main_layout.addLayout(layout_bottom)

    def _browse_fes(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择 FES2022b 网格文件", "", "NetCDF Files (*.nc)")
        if f:
            self.edit_fes.setText(f)

    def _browse_mdt(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择 CNES-CLS22 MDT 文件", "", "NetCDF Files (*.nc)")
        if f:
            self.edit_mdt.setText(f)

    def _browse_egm(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择 EGM2008 GeoTIFF 文件", "", "GeoTIFF Files (*.tif *.tiff)")
        if f:
            self.edit_egm.setText(f)

    def _validate_paths(self):
        fes_path = self.edit_fes.text().strip()
        mdt_path = self.edit_mdt.text().strip()
        egm_path = self.edit_egm.text().strip()

        # 处理相对路径
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not os.path.isabs(egm_path):
            egm_path = os.path.join(base_dir, egm_path)

        msg = []
        msg.append(f"• FES2022b 网格: {'✅ 正常存在' if os.path.exists(fes_path) else '❌ 不存在'}")
        msg.append(f"• CNES-CLS22 MDT: {'✅ 正常存在' if os.path.exists(mdt_path) else '❌ 不存在'}")
        msg.append(f"• EGM2008 GeoTIFF: {'✅ 正常存在' if os.path.exists(egm_path) else '❌ 不存在'}")

        QMessageBox.information(self, "数据源完整性校验", "\n".join(msg))

    def _save_settings(self):
        self.config['paths']['fes_ns_grid'] = self.edit_fes.text().strip()
        self.config['paths']['mdt_nc'] = self.edit_mdt.text().strip()
        self.config['paths']['egm2008_tif'] = self.edit_egm.text().strip()

        save_app_config(self.config)
        QMessageBox.information(self, "提示", "设置已成功保存！")
        self.accept()

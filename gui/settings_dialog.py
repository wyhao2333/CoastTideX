"""
CoastTideX 设置与数据源管理对话框 (Settings Dialog v1.4)
提供对 FES2022b 网格、MDT 数据、双重 DeltaN 栅格及 Hybrid MDT 权威来源掩膜的可视化路径配置与深层数据校验。
"""

import os
import numpy as np
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QMessageBox, QGroupBox, QScrollArea, QWidget
)
from core.utils import load_app_config, save_app_config, resolve_project_path


def _deep_validate_file(path: str, file_type: str) -> tuple[bool, str]:
    """
    深度校验数据文件的有效性：
    检查文件是否存在、格式是否损坏、CRS、网格维度、关键变量及有效数据内容。
    """
    if not path or not os.path.exists(path):
        return False, "❌ 文件不存在"
    try:
        if file_type == 'netcdf_mdt':
            import xarray as xr
            ds = xr.open_dataset(path)
            for var in ['mdt', 'latitude', 'longitude']:
                if var not in ds.variables and var not in ds.coords:
                    ds.close()
                    return False, f"❌ NetCDF 缺少关键变量: {var}"
            shape = ds['mdt'].shape
            ds.close()
            return True, f"✅ 正常 (变量完整, shape={shape})"
        elif file_type == 'netcdf_fes':
            import netCDF4 as nc
            ds = nc.Dataset(path)
            n_vars = len(ds.variables)
            ds.close()
            return True, f"✅ 正常 (NetCDF有效, {n_vars} 个变量)"
        elif file_type == 'raster':
            import rasterio
            with rasterio.open(path) as src:
                crs = src.crs.to_string() if src.crs else "无CRS"
                w, h = src.width, src.height
                data = src.read(1, masked=True)
                valid_cnt = np.count_nonzero(~data.mask) if np.ma.is_masked(data) else np.count_nonzero(np.isfinite(data))
                if valid_cnt == 0:
                    return False, "❌ 栅格全为空或NoData"
            return True, f"✅ 正常 ({w}x{h}, {crs})"
        elif file_type == 'source_mask':
            import rasterio
            with rasterio.open(path) as src:
                w, h = src.width, src.height
                data = src.read(1)
                unique_vals = np.unique(data)
                allowed = {0, 1, 2, 3}
                if not set(unique_vals).issubset(allowed):
                    return False, f"❌ 包含非法类别: {unique_vals} (仅允许 0, 1, 2, 3)"
            return True, f"✅ 正常 (有效类别={sorted(list(unique_vals))})"
        else:
            return (True, "✅ 正常存在") if os.path.exists(path) else (False, "❌ 文件不存在")
    except Exception as e:
        return False, f"❌ 读取校验异常: {e}"


class SettingsDialog(QDialog):
    """数据源配置弹窗 (v1.4)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("数据源路径与系统设置 - CoastTideX v1.4")
        self.resize(720, 520)
        self.config = load_app_config()

        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout_paths = QVBoxLayout(container)
        layout_paths.setSpacing(10)

        # 1. 潮波与海洋网格分组
        grp_ocean = QGroupBox("1. 潮波与海面地形数据 (FES2022b / MDT)")
        layout_oc = QVBoxLayout(grp_ocean)
        layout_oc.setSpacing(8)

        # FES2022b NS Grid
        layout_fes = QHBoxLayout()
        layout_fes.addWidget(QLabel("FES2022b 原生网格 (.nc):"))
        self.edit_fes = QLineEdit(self.config['paths'].get('fes_ns_grid', ''))
        btn_fes = QPushButton("浏览...")
        btn_fes.setObjectName("btn_secondary")
        btn_fes.clicked.connect(self._browse_fes)
        layout_fes.addWidget(self.edit_fes)
        layout_fes.addWidget(btn_fes)
        layout_oc.addLayout(layout_fes)

        # MDT NetCDF
        layout_mdt = QHBoxLayout()
        layout_mdt.addWidget(QLabel("CNES-CLS22 MDT (.nc):"))
        self.edit_mdt = QLineEdit(self.config['paths'].get('mdt_nc', ''))
        btn_mdt = QPushButton("浏览...")
        btn_mdt.setObjectName("btn_secondary")
        btn_mdt.clicked.connect(self._browse_mdt)
        layout_mdt.addWidget(self.edit_mdt)
        layout_mdt.addWidget(btn_mdt)
        layout_oc.addLayout(layout_mdt)

        layout_paths.addWidget(grp_ocean)

        # 2. 大地水准面与基准转换数据分组
        grp_geoid = QGroupBox("2. 大地水准面与差值栅格配置 (Canonical Keys)")
        layout_gd = QVBoxLayout(grp_geoid)
        layout_gd.setSpacing(8)

        # EGM2008 GeoTIFF
        layout_egm = QHBoxLayout()
        layout_egm.addWidget(QLabel("EGM2008 起伏 (.tif):"))
        self.edit_egm = QLineEdit(self.config['paths'].get('egm2008_tif', ''))
        btn_egm = QPushButton("浏览...")
        btn_egm.setObjectName("btn_secondary")
        btn_egm.clicked.connect(self._browse_egm)
        layout_egm.addWidget(self.edit_egm)
        layout_egm.addWidget(btn_egm)
        layout_gd.addLayout(layout_egm)

        # Delta-N GeoTIFF (GOCO06s - EGM2008)
        layout_dn_goco = QHBoxLayout()
        layout_dn_goco.addWidget(QLabel("GOCO06s-EGM2008 ΔN (.tif):"))
        goco_val = self.config['paths'].get('delta_n_goco06s_egm2008_tif', self.config['paths'].get('delta_n_tif', ''))
        self.edit_delta_n_goco = QLineEdit(goco_val)
        btn_dn_goco = QPushButton("浏览...")
        btn_dn_goco.setObjectName("btn_secondary")
        btn_dn_goco.clicked.connect(self._browse_delta_n_goco)
        layout_dn_goco.addWidget(self.edit_delta_n_goco)
        layout_dn_goco.addWidget(btn_dn_goco)
        layout_gd.addLayout(layout_dn_goco)

        # Delta-N GeoTIFF (EIGEN-6C4 - EGM2008)
        layout_dn_eigen = QHBoxLayout()
        layout_dn_eigen.addWidget(QLabel("EIGEN6C4-EGM2008 ΔN (.tif):"))
        eigen_val = self.config['paths'].get('delta_n_eigen6c4_egm2008_tif', '')
        self.edit_delta_n_eigen = QLineEdit(eigen_val)
        btn_dn_eigen = QPushButton("浏览...")
        btn_dn_eigen.setObjectName("btn_secondary")
        btn_dn_eigen.clicked.connect(self._browse_delta_n_eigen)
        layout_dn_eigen.addWidget(self.edit_delta_n_eigen)
        layout_dn_eigen.addWidget(btn_dn_eigen)
        layout_gd.addLayout(layout_dn_eigen)

        # Hybrid MDT Source Mask
        layout_mask = QHBoxLayout()
        layout_mask.addWidget(QLabel("Hybrid MDT 来源掩膜 (.tif):"))
        mask_val = self.config['paths'].get('hybrid_mdt_source_mask', '')
        self.edit_source_mask = QLineEdit(mask_val)
        btn_mask = QPushButton("浏览...")
        btn_mask.setObjectName("btn_secondary")
        btn_mask.clicked.connect(self._browse_source_mask)
        layout_mask.addWidget(self.edit_source_mask)
        layout_mask.addWidget(btn_mask)
        layout_gd.addLayout(layout_mask)

        layout_paths.addWidget(grp_geoid)
        scroll.setWidget(container)
        main_layout.addWidget(scroll)

        # 3. 状态校验按钮
        layout_check = QHBoxLayout()
        self.btn_validate = QPushButton("🔍 深度校验所有数据文件有效性 (CRS/Shape/变量)")
        self.btn_validate.setObjectName("btn_secondary")
        self.btn_validate.setFixedHeight(36)
        self.btn_validate.clicked.connect(self._validate_paths)
        layout_check.addWidget(self.btn_validate)
        layout_check.addStretch()
        main_layout.addLayout(layout_check)

        # 4. 底部确定/取消按钮
        layout_bottom = QHBoxLayout()
        layout_bottom.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("btn_secondary")
        btn_cancel.clicked.connect(self.reject)

        btn_save = QPushButton("保存配置")
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

    def _browse_delta_n_goco(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择 GOCO06s-EGM2008 ΔN 栅格", "", "GeoTIFF Files (*.tif *.tiff)")
        if f:
            self.edit_delta_n_goco.setText(f)

    def _browse_delta_n_eigen(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择 EIGEN-6C4-EGM2008 ΔN 栅格", "", "GeoTIFF Files (*.tif *.tiff)")
        if f:
            self.edit_delta_n_eigen.setText(f)

    def _browse_source_mask(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择 Hybrid MDT 权威来源掩膜", "", "GeoTIFF Files (*.tif *.tiff)")
        if f:
            self.edit_source_mask.setText(f)

    def _validate_paths(self):
        fes_path = resolve_project_path(self.edit_fes.text().strip())
        mdt_path = resolve_project_path(self.edit_mdt.text().strip())
        egm_path = resolve_project_path(self.edit_egm.text().strip(), prefer_resource=True)
        goco_path = resolve_project_path(self.edit_delta_n_goco.text().strip(), prefer_resource=True)
        eigen_path = resolve_project_path(self.edit_delta_n_eigen.text().strip(), prefer_resource=True)
        mask_path = resolve_project_path(self.edit_source_mask.text().strip(), prefer_resource=True)

        msg = []
        _, res = _deep_validate_file(fes_path, 'netcdf_fes')
        msg.append(f"• FES2022b 网格: {res}")

        _, res = _deep_validate_file(mdt_path, 'netcdf_mdt')
        msg.append(f"• CNES-CLS22 MDT: {res}")

        _, res = _deep_validate_file(egm_path, 'raster')
        msg.append(f"• EGM2008 起伏栅格: {res}")

        _, res = _deep_validate_file(goco_path, 'raster')
        msg.append(f"• GOCO06s-EGM2008 ΔN: {res}")

        if eigen_path:
            _, res = _deep_validate_file(eigen_path, 'raster')
            msg.append(f"• EIGEN-6C4-EGM2008 ΔN: {res}")
        else:
            msg.append("• EIGEN-6C4-EGM2008 ΔN: ⚠️ 未指定 (若在地中海/黑海计算非MSL将严格报错)")

        if mask_path:
            _, res = _deep_validate_file(mask_path, 'source_mask')
            msg.append(f"• Hybrid MDT 权威来源掩膜: {res}")
        else:
            msg.append("• Hybrid MDT 权威来源掩膜: ℹ️ 未配置 (系统将自动采用几何多边形作为 Fallback)")

        QMessageBox.information(self, "数据源深度校验结果", "\n".join(msg))

    def _save_settings(self):
        paths = self.config.setdefault('paths', {})
        paths['fes_ns_grid'] = self.edit_fes.text().strip()
        paths['mdt_nc'] = self.edit_mdt.text().strip()
        paths['egm2008_tif'] = self.edit_egm.text().strip()

        # 统一写入 canonical 键名，并保留旧版键名兼容
        goco_dn = self.edit_delta_n_goco.text().strip()
        paths['delta_n_goco06s_egm2008_tif'] = goco_dn
        paths['delta_n_tif'] = goco_dn
        paths['delta_n_eigen6c4_egm2008_tif'] = self.edit_delta_n_eigen.text().strip()

        mask_p = self.edit_source_mask.text().strip()
        if mask_p:
            paths['hybrid_mdt_source_mask'] = mask_p
        elif 'hybrid_mdt_source_mask' in paths:
            paths['hybrid_mdt_source_mask'] = ""

        save_app_config(self.config)
        QMessageBox.information(self, "提示", "设置已成功保存！")
        self.accept()


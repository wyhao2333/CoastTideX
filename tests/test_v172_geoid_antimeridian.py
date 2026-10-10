"""Real GeoTIFF seam regression tests; no external FES/model data required."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
import xarray as xr
from scipy.interpolate import RegularGridInterpolator
from rasterio.transform import from_origin
from scipy.ndimage import map_coordinates

from core.datum_engine import (
    DatumTransformer, _longitude_seam_geometry, _sample_geoid_grid,
)
from core.batch_datum_converter import BatchDEMDatumConverter, compute_conversion_signature
from core.dem_datum_converter import DEMDatumConverter, QC_MDT_EXTRAPOLATED, QC_MDT_NODATA
from cli import build_parser


class TestGeoidLongitudeSeam(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.step = 0.085
        self.transform = from_origin(-180 - self.step / 2, 1.5, self.step, 1)
        self.data = np.tile(np.linspace(10, 30, 4236, dtype=np.float32), (3, 1))
        self.data += np.arange(3, dtype=np.float32)[:, None] * 4
        self.grid = self.write_grid('global.tif', self.data, self.transform)

    def write_grid(self, name, data, transform, crs='EPSG:4326', nodata=None):
        path = self.root / name
        with rasterio.open(path, 'w', driver='GTiff', height=data.shape[0],
                           width=data.shape[1], count=1, dtype='float32',
                           crs=crs, transform=transform, nodata=nodata) as ds:
            ds.write(data.astype(np.float32), 1)
        return path

    def transformer(self, grid=None):
        grid = str(grid or self.grid)
        return DatumTransformer(delta_n_goco_path=grid, delta_n_eigen_path=grid,
                                egm2008_path=grid, source_mask_path='missing_mask.tif')

    def test_actual_gap_and_longitude_equivalence(self):
        trans = self.transformer()
        # Actual 0.025-degree gap, unlike the 0.085-degree interior spacing.
        lons = np.array([179.98, 179.9875, 179.995, -180.005, 539.995])
        expected = 34 + (14 - 34) * ((lons[:3] - 179.975) / 0.025)
        expected = np.concatenate([expected, [expected[-1], expected[-1]]])
        np.testing.assert_allclose(trans.get_delta_n(lons, 0), expected, atol=2e-6)
        self.assertAlmostEqual(trans.get_delta_n(179.9875, 0), 24, places=5)
        self.assertEqual(trans.get_delta_n(180, 0), trans.get_delta_n(-180, 0))

    def test_latitude_interpolation_without_latitude_wrap(self):
        trans = self.transformer()
        self.assertAlmostEqual(trans.get_delta_n(179.9875, 0.5), 22, places=5)
        vals = trans.get_delta_n(179.99, [2, -2, 91, -91, np.nan])
        self.assertTrue(np.isnan(vals).all())
        self.assertTrue(np.isnan(trans.get_delta_n(np.nan, 0)))

    def test_interior_values_bitwise_unchanged(self):
        trans = self.transformer()
        rng = np.random.default_rng(172)
        lons = rng.uniform(-180, 179.97, 1000)
        lats = rng.uniform(-1, 1, 1000)
        inv = ~self.transform
        cols, rows = inv * (lons, lats)
        expected = map_coordinates(self.data, [rows - .5, cols - .5], order=1,
                                   mode='constant', cval=np.nan)
        np.testing.assert_array_equal(trans.get_delta_n(lons, lats), expected)

    def test_egm2008_uses_same_seam_and_keeps_scalar_api(self):
        trans = self.transformer()
        self.assertIsInstance(trans.get_egm2008_undulation(179.9875, 0), float)
        self.assertAlmostEqual(trans.get_egm2008_undulation(179.9875, 0), 24, places=5)
        np.testing.assert_allclose(trans.get_egm2008_undulation([179.9875, -180], [0, 0]),
                                   [24, 14], atol=2e-6)

    def test_eigen_branch_uses_its_own_grid(self):
        trans = self.transformer()
        # Force the branch to test its sampler; this is not a real geoid assignment.
        with patch.object(trans, '_get_ref_geoid_and_qc', return_value=(
                np.array(['EIGEN-6C4']), np.array(['NORMAL']))):
            self.assertAlmostEqual(trans.get_delta_n(179.9875, 0), 24, places=5)
        self.assertIsNone(trans._delta_n_goco_data)

    def test_finite_nodata_is_not_interpolated_or_filled(self):
        data = self.data.copy()
        data[:, 0] = -9999
        grid = self.write_grid('nodata.tif', data, self.transform, nodata=-9999)
        trans = self.transformer(grid)
        self.assertTrue(np.isnan(trans.get_delta_n(179.99, 0)))
        self.assertTrue(np.isnan(trans.get_delta_n(-180, 0)))
        self.assertTrue(np.isnan(trans.get_egm2008_undulation(179.99, 0)))
        data = self.data.copy()
        data[:, 100] = np.nan
        grid = self.write_grid('interior_nodata.tif', data, self.transform)
        self.assertTrue(np.isnan(self.transformer(grid).get_delta_n(-171.5, 0)))

    def test_regional_projected_and_rotated_grids_do_not_wrap(self):
        fixtures = [
            ('regional', self.data[:, :8], self.transform, 'EPSG:4326'),
            ('projected', self.data, self.transform, 'EPSG:3857'),
            ('rotated', self.data, rasterio.Affine(.085, .001, -180, 0, -1, 1.5), 'EPSG:4326'),
        ]
        for name, data, transform, crs in fixtures:
            with self.subTest(name=name):
                grid = self.write_grid(name + '.tif', data, transform, crs=crs)
                with rasterio.open(grid) as src:
                    self.assertIsNone(_longitude_seam_geometry(src))
                inv = ~transform
                col, row = inv * (179.99, 0)
                expected = map_coordinates(data, [[row - .5], [col - .5]],
                                           order=1, mode='constant', cval=np.nan)
                actual = self.transformer(grid).get_delta_n(179.99, 0)
                np.testing.assert_array_equal([actual], expected)

    def test_shifted_centers_and_duplicate_endpoint(self):
        # Pixel-centered global grids can leave a seam on either side of +/-180.
        data = np.tile(np.array([10, 20, 30, 40], dtype=np.float32), (3, 1))
        for first in [-135, 0]:
            with self.subTest(first=first):
                t = from_origin(first - 45, 1.5, 90, 1)
                grid = self.write_grid('shifted.tif', data, t)
                with rasterio.open(grid) as src:
                    geometry = _longitude_seam_geometry(src)
                query = np.array([first - 45.0])
                vals = _sample_geoid_grid(data, ~t, query, np.array([0.]), geometry)
                np.testing.assert_allclose(vals, [25], atol=1e-6)
        duplicate = np.column_stack([data, data[:, 0]])
        t = from_origin(-225, 1.5, 90, 1)
        grid = self.write_grid('duplicate.tif', duplicate, t)
        with rasterio.open(grid) as src:
            self.assertEqual(_longitude_seam_geometry(src)[3], 0)
        self.assertEqual(self.transformer(grid).get_delta_n(-180, 0), 10)

    def test_new_signature_invalidates_v171_resume(self):
        old = compute_conversion_signature(schema_version='1.7.1')
        new = compute_conversion_signature()
        self.assertNotEqual(old, new)
        self.assertEqual(new, compute_conversion_signature(schema_version='1.7.2'))

    def test_direct_mdt_seam_preserves_land_and_latitude_bounds(self):
        lons = np.array([-135., -45., 45., 135.])
        lats = np.array([-1., 0., 1.])
        data = np.tile([10., 20., 30., 40.], (3, 1))
        path = self.root / 'mdt.nc'
        def write(values):
            xr.Dataset({'mdt': (('time', 'latitude', 'longitude'), values[None])},
                       coords={'time': [0], 'latitude': lats, 'longitude': lons}).to_netcdf(path)
        write(data)
        trans = DatumTransformer(mdt_path=str(path))
        np.testing.assert_allclose(trans.get_mdt([180, -180, 179.99, -179.99], [0]*4),
                                   [25, 25, 25 + 30 * .01 / 90, 25 - 30 * .01 / 90])
        self.assertTrue(np.isnan(trans.get_mdt(179.99, 2)))
        expected = RegularGridInterpolator((lats, lons), data)([[0, -45], [.5, 45]])
        np.testing.assert_array_equal(trans.get_mdt([-45, 45], [0, .5]), expected)
        data[:, 0] = np.nan
        write(data)
        self.assertTrue(np.isnan(DatumTransformer(mdt_path=str(path)).get_mdt(179.99, 0)))

    def test_direct_mdt_regional_and_duplicate_endpoint(self):
        lats = [-1., 0., 1.]
        for lons, data, expected in [
            ([0., 90., 180., 270., 360.], [10., 20., 30., 40., 10.], 25.),
            ([0., 90., 180.], [10., 20., 30.], np.nan),
        ]:
            with self.subTest(lons=lons):
                path = self.root / 'mdt_other.nc'
                xr.Dataset({'mdt': (('time', 'latitude', 'longitude'),
                                    np.tile(data, (1, 3, 1)))},
                           coords={'time': [0], 'latitude': lats, 'longitude': lons}).to_netcdf(path)
                actual = DatumTransformer(mdt_path=str(path)).get_mdt(-45, 0)
                np.testing.assert_array_equal([actual], [expected])


class TestDefaultMDT500(unittest.TestCase):
    def test_default_transport_and_explicit_override(self):
        parser = build_parser()
        single = parser.parse_args(['convert-dem', '--input', 'dem.tif'])
        batch = parser.parse_args(['convert-dem-batch', '--input-dir', 'in', '--output-dir', 'out'])
        self.assertEqual(single.max_dist_km, 500.0)
        self.assertEqual(batch.max_dist_km, 500.0)
        explicit = parser.parse_args(['convert-dem', '--input', 'dem.tif', '--max-dist-km', '100'])
        self.assertEqual(explicit.max_dist_km, 100.0)
        self.assertEqual(BatchDEMDatumConverter().max_extrapolation_distance_km, 500.0)
        self.assertNotEqual(compute_conversion_signature(),
                            compute_conversion_signature(max_extrapolation_distance_km=100.0))

    def test_default_accepts_300km_support_but_rejects_over_500km(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lons = np.arange(-8., 8.01, .25)
            lats = np.arange(-2., 2.01, .25)
            data = np.full((1, len(lats), len(lons)), np.nan)
            data[:, :, np.abs(lons) <= .25] = 1.2
            nc = root / 'mdt.nc'
            xr.Dataset({'mdt': (('time', 'latitude', 'longitude'), data)},
                       coords={'time': [0], 'latitude': lats, 'longitude': lons}).to_netcdf(nc)
            trans = DatumTransformer(source_mask_path='missing_mask.tif')
            trans.set_synthetic_fixture(delta_goco=0., delta_eigen=0.)
            default = DEMDatumConverter(mdt_path=str(nc), transformer=trans)
            limited = DEMDatumConverter(mdt_path=str(nc), transformer=trans,
                                        max_extrapolation_distance_km=100.)
            _, mdt, _, qc = default.convert_points([3., 5.], [0., 0.], [5., 5.])
            self.assertEqual(qc[0], QC_MDT_EXTRAPOLATED)
            self.assertAlmostEqual(mdt[0], 1.2, places=6)
            self.assertEqual(qc[1], QC_MDT_NODATA)
            self.assertTrue(np.isnan(mdt[1]))
            _, mdt_old, _, qc_old = limited.convert_points([3.], [0.], [5.])
            self.assertEqual(qc_old[0], QC_MDT_NODATA)
            self.assertTrue(np.isnan(mdt_old[0]))

            source, output = root / 'source.tif', root / 'output.tif'
            with rasterio.open(source, 'w', driver='GTiff', height=1, width=2,
                               count=1, dtype='float32', crs='EPSG:4326',
                               transform=from_origin(2, .5, 2, 1), nodata=-9999) as ds:
                ds.write(np.full((1, 2), 5, dtype=np.float32), 1)
            summary = default.convert_raster(str(source), str(output))
            self.assertEqual(summary.max_extrapolation_distance_km, 500.)
            with rasterio.open(output) as ds:
                self.assertEqual(float(ds.tags()['MAX_EXTRAPOLATION_DISTANCE_KM']), 500.)
                values = ds.read(1)
                self.assertAlmostEqual(float(values[0, 0]), 3.8, places=5)
                self.assertEqual(values[0, 1], ds.nodata)


if __name__ == '__main__':
    unittest.main()

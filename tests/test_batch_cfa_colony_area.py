from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from analyze_cfa_plate_one import colony_mask
from batch_cfa_colony_area import (
    deduplicate_images,
    find_input_images,
    html_table,
    make_contact_sheet,
    sample_label,
    write_internal_control_report,
    write_sample_mask_qc,
)


class BatchHelperTests(unittest.TestCase):
    def test_find_input_images_recurses_and_excludes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sample_dir = root / "sample"
            output_dir = root / "_colonyarea_results"
            legacy_output_dir = root / "_colonyarea_test"
            sample_dir.mkdir()
            output_dir.mkdir()
            legacy_output_dir.mkdir()

            wanted_tif = sample_dir / "plate_1.tif"
            wanted_tiff = sample_dir / "plate_2.tiff"
            wanted_png = sample_dir / "plate_3.png"
            wanted_jpg = sample_dir / "plate_4.jpg"
            wanted_jpeg = sample_dir / "plate_5.jpeg"
            ignored_gif = sample_dir / "plate_6.gif"
            output_tif = output_dir / "generated_crop.tif"
            output_png = output_dir / "generated_mask.png"
            legacy_output_tif = legacy_output_dir / "generated_crop.tif"

            files = [
                wanted_tif,
                wanted_tiff,
                wanted_png,
                wanted_jpg,
                wanted_jpeg,
                ignored_gif,
                output_tif,
                output_png,
                legacy_output_tif,
            ]
            for path in files:
                path.write_bytes(b"not a real image")

            images = find_input_images(root, excluded_dirs=[output_dir])

            self.assertEqual(images, [wanted_tif, wanted_tiff, wanted_png, wanted_jpg, wanted_jpeg])

    def test_html_table_escapes_values(self) -> None:
        table = html_table([{"sample": "<sample>", "value": "1&2"}], ["sample", "value"])

        self.assertIn("&lt;sample&gt;", table)
        self.assertIn("1&amp;2", table)

    def test_deduplicate_images_skips_exact_duplicate_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first = root / "sample_a" / "plate_1.jpg"
            duplicate = root / "sample_b" / "plate_1_copy.jpg"
            different = root / "sample_c" / "plate_2.jpg"
            for path in [first, duplicate, different]:
                path.parent.mkdir(parents=True, exist_ok=True)
            first.write_bytes(b"same image bytes")
            duplicate.write_bytes(b"same image bytes")
            different.write_bytes(b"different image bytes")

            unique_images, duplicate_rows = deduplicate_images([first, duplicate, different])

            self.assertEqual(unique_images, [first, different])
            self.assertEqual(len(duplicate_rows), 1)
            self.assertEqual(duplicate_rows[0]["kept_image_path"], str(first))
            self.assertEqual(duplicate_rows[0]["skipped_image_path"], str(duplicate))

    def test_sample_label_uses_relative_parent_path(self) -> None:
        root = Path("/tmp/input")

        self.assertEqual(sample_label(root, root / "condition_a" / "plate_1.tif"), "condition_a")
        nested_image = root / "experiment_1" / "condition_a" / "plate_1.tif"
        self.assertEqual(sample_label(root, nested_image), "experiment_1/condition_a")
        self.assertEqual(sample_label(root, root / "plate_1.tif"), "input")

    def test_internal_control_report_is_written_without_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            write_internal_control_report(
                output_dir=output_dir,
                image_rows=[],
                sample_rows=[],
                qc_rows=[],
                failed_rows=[],
                visual_artifacts=False,
            )

            report = (output_dir / "internal_control_qc.html").read_text()

            self.assertIn("Colony Area Internal Control QC", report)
            self.assertIn("Visual QC Not Generated", report)

    def test_sample_mask_qc_writes_browsable_sample_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            image_dir = output_dir / "per_image" / "experiment" / "#3" / "plate_1"
            image_dir.mkdir(parents=True)
            grid_path = image_dir / "plate_1_auto_grid_qc.png"
            overlay_path = image_dir / "plate_1_auto_overlay_montage.png"
            mask_path = image_dir / "plate_1_auto_mask_montage.png"
            for path in [grid_path, overlay_path, mask_path]:
                Image.new("RGB", (20, 10), "white").save(path)

            write_sample_mask_qc(
                output_dir=output_dir,
                artifact_rows=[
                    {
                        "sample": "experiment/#3",
                        "image": "plate_1.png",
                        "grid_path": str(grid_path),
                        "overlay_path": str(overlay_path),
                        "mask_path": str(mask_path),
                    }
                ],
                sample_rows=[
                    {
                        "sample": "experiment/#3",
                        "image_count": 1,
                        "well_count": 6,
                        "mean_area_percent": "12.0000",
                        "sd_area_percent": "1.0000",
                        "min_area_percent": "10.0000",
                        "max_area_percent": "14.0000",
                    }
                ],
                qc_rows=[],
            )

            top_index = (output_dir / "sample_mask_qc" / "index.html").read_text()
            sample_page = (output_dir / "sample_mask_qc" / "experiment" / "#3" / "index.html").read_text()

            self.assertIn("experiment/%233/index.html", top_index)
            self.assertIn("mask_contact_sheet.png", sample_page)
            self.assertTrue((output_dir / "sample_mask_qc" / "experiment" / "#3" / "mask_contact_sheet.png").exists())

    def test_contact_sheet_uses_only_needed_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            image_path = root / "well.png"
            output_path = root / "contact.png"
            Image.new("RGB", (20, 10), "white").save(image_path)

            make_contact_sheet([("sample", image_path)], output_path, columns=4, thumb_width=40)

            with Image.open(output_path) as output_image:
                self.assertEqual(output_image.size, (40, 52))

    def test_colony_mask_counts_compact_edge_colonies(self) -> None:
        rgb = np.full((200, 200, 3), 220, dtype=np.uint8)
        yy, xx = np.ogrid[:200, :200]
        edge_colony = (xx - 196) ** 2 + (yy - 100) ** 2 <= 4**2
        rgb[edge_colony] = np.array([45, 35, 140], dtype=np.uint8)

        mask, roi = colony_mask(rgb)

        self.assertGreater(int((mask & edge_colony).sum()), 0)
        self.assertGreater(int((roi & edge_colony).sum()), 0)

    def test_colony_mask_keeps_stained_edge_streaks(self) -> None:
        rgb = np.full((200, 200, 3), 220, dtype=np.uint8)
        yy, xx = np.ogrid[:200, :200]
        edge_streak = ((xx - 193) / 3.0) ** 2 + ((yy - 100) / 24.0) ** 2 <= 1.0
        rgb[edge_streak] = np.array([45, 35, 140], dtype=np.uint8)

        mask, roi = colony_mask(rgb)

        self.assertGreater(int((mask & edge_streak).sum()), int(edge_streak.sum() * 0.75))
        self.assertGreater(int((roi & edge_streak).sum()), int(edge_streak.sum() * 0.75))

    def test_colony_mask_removes_crescent_rim_stain(self) -> None:
        rgb = np.full((200, 200, 3), 220, dtype=np.uint8)
        yy, xx = np.ogrid[:200, :200]
        distance = np.sqrt((xx - 99.5) ** 2 + (yy - 99.5) ** 2)
        rim_stain = (distance > 88) & (distance < 97) & (xx > 118)
        rgb[rim_stain] = np.array([45, 35, 140], dtype=np.uint8)

        mask, _ = colony_mask(rgb)

        self.assertLess(int((mask & rim_stain).sum()), int(rim_stain.sum() * 0.20))


if __name__ == "__main__":
    unittest.main()

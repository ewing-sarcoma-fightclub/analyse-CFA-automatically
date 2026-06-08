#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import os
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

import numpy as np
from PIL import Image, ImageDraw

from analyze_cfa_plate_one import analyze_image


IMAGE_SUFFIXES = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
DEFAULT_OUTPUT_DIR_NAME = "_colonyarea_results"


def path_is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def find_input_images(root: Path, excluded_dirs: list[Path] | None = None) -> list[Path]:
    excluded_dirs = [path.resolve() for path in excluded_dirs or []]
    images: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        resolved_path = path.resolve()
        if any(path_is_under(resolved_path, excluded_dir) for excluded_dir in excluded_dirs):
            continue
        relative_parts = path.relative_to(root).parts
        if any(part.startswith("_colonyarea") for part in relative_parts):
            continue
        images.append(path)
    return sorted(images)


def image_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def deduplicate_images(images: list[Path]) -> tuple[list[Path], list[dict]]:
    unique_images: list[Path] = []
    first_by_hash: dict[str, Path] = {}
    group_by_hash: dict[str, str] = {}
    duplicate_rows: list[dict] = []

    for image_path in images:
        digest = image_sha256(image_path)
        if digest not in first_by_hash:
            first_by_hash[digest] = image_path
            unique_images.append(image_path)
            continue

        group_id = group_by_hash.setdefault(digest, f"duplicate_group_{len(group_by_hash) + 1:02d}")
        duplicate_rows.append(
            {
                "duplicate_group_id": group_id,
                "sha256": digest,
                "kept_image_path": str(first_by_hash[digest]),
                "skipped_image_path": str(image_path),
            }
        )

    return unique_images, duplicate_rows


def sample_label(root: Path, image_path: Path) -> str:
    relative_parent = image_path.parent.relative_to(root)
    if str(relative_parent) == ".":
        return root.name
    return str(relative_parent)


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize_values(values: list[float]) -> tuple[float, float, float, float]:
    arr = np.array(values, dtype=float)
    return float(arr.mean()), float(arr.std(ddof=1)) if arr.size > 1 else 0.0, float(arr.min()), float(arr.max())


def make_contact_sheet(entries: list[tuple[str, Path]], output_path: Path, columns: int = 4, thumb_width: int = 430) -> None:
    thumbs: list[Image.Image] = []
    for label, image_path in entries:
        with Image.open(image_path) as opened_image:
            image = opened_image.convert("RGB")
        scale = thumb_width / image.width
        thumb_height = max(1, int(round(image.height * scale)))
        image = image.resize((thumb_width, thumb_height), Image.Resampling.LANCZOS)

        labeled = Image.new("RGB", (thumb_width, thumb_height + 32), "white")
        labeled.paste(image, (0, 32))
        draw = ImageDraw.Draw(labeled)
        draw.text((8, 9), label, fill=(0, 0, 0))
        thumbs.append(labeled)

    if not thumbs:
        return

    sheet_columns = min(columns, len(thumbs))
    rows = (len(thumbs) + sheet_columns - 1) // sheet_columns
    cell_width = max(thumb.width for thumb in thumbs)
    cell_height = max(thumb.height for thumb in thumbs)
    sheet = Image.new("RGB", (sheet_columns * cell_width, rows * cell_height), "white")
    for index, thumb in enumerate(thumbs):
        sheet.paste(thumb, ((index % sheet_columns) * cell_width, (index // sheet_columns) * cell_height))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path)


def html_table(rows: list[dict], columns: list[str], escape_values: bool = True) -> str:
    if not rows:
        return "<p>No rows.</p>"

    parts = ["<table>", "<thead><tr>"]
    for column in columns:
        parts.append(f"<th>{html.escape(column)}</th>")
    parts.append("</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>")
        for column in columns:
            value = str(row.get(column, ""))
            if escape_values:
                value = html.escape(value)
            parts.append(f"<td>{value}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def html_link(from_dir: Path, target: Path) -> str:
    relative_path = Path(os.path.relpath(target, start=from_dir)).as_posix()
    return html.escape(quote(relative_path, safe="/"))


def write_sample_mask_qc(
    output_dir: Path,
    artifact_rows: list[dict],
    sample_rows: list[dict],
    qc_rows: list[dict],
) -> None:
    if not artifact_rows:
        return

    qc_dir = output_dir / "sample_mask_qc"
    qc_dir.mkdir(parents=True, exist_ok=True)

    rows_by_sample: dict[str, list[dict]] = defaultdict(list)
    flags_by_sample: dict[str, list[dict]] = defaultdict(list)
    sample_summary = {row["sample"]: row for row in sample_rows}
    for row in artifact_rows:
        rows_by_sample[row["sample"]].append(row)
    for row in qc_rows:
        flags_by_sample[row["sample"]].append(row)

    index_rows: list[dict] = []
    for sample in sorted(rows_by_sample):
        sample_dir = qc_dir / sample
        sample_dir.mkdir(parents=True, exist_ok=True)
        sample_artifacts = sorted(rows_by_sample[sample], key=lambda row: row["image"])
        sample_flags = flags_by_sample.get(sample, [])

        make_contact_sheet(
            [(row["image"], Path(row["mask_path"])) for row in sample_artifacts],
            sample_dir / "mask_contact_sheet.png",
            columns=2,
            thumb_width=560,
        )
        make_contact_sheet(
            [(row["image"], Path(row["overlay_path"])) for row in sample_artifacts],
            sample_dir / "overlay_contact_sheet.png",
            columns=2,
            thumb_width=560,
        )
        make_contact_sheet(
            [(row["image"], Path(row["grid_path"])) for row in sample_artifacts],
            sample_dir / "grid_contact_sheet.png",
            columns=2,
            thumb_width=560,
        )

        image_links = []
        for row in sample_artifacts:
            image_links.append(
                {
                    "image": row["image"],
                    "grid": f'<a href="{html_link(sample_dir, Path(row["grid_path"]))}">grid</a>',
                    "overlay": f'<a href="{html_link(sample_dir, Path(row["overlay_path"]))}">overlay</a>',
                    "mask": f'<a href="{html_link(sample_dir, Path(row["mask_path"]))}">mask</a>',
                }
            )

        sample_summary_row = sample_summary.get(sample, {})
        sample_page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Mask QC - {html.escape(sample)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 28px; color: #1f2933; }}
    h1, h2 {{ margin-bottom: 0.35rem; }}
    section {{ margin: 2rem 0; }}
    img {{ display: block; max-width: 100%; border: 1px solid #d0d7de; margin-bottom: 1rem; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 6px 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f6f8fa; }}
    .links a {{ margin-right: 1rem; }}
  </style>
</head>
<body>
  <p><a href="{html_link(sample_dir, qc_dir / "index.html")}">sample mask QC index</a></p>
  <h1>{html.escape(sample)}</h1>
  <section>
    <h2>Sample Summary</h2>
    {html_table([sample_summary_row], ["sample", "image_count", "well_count", "mean_area_percent", "sd_area_percent", "min_area_percent", "max_area_percent"]) if sample_summary_row else "<p>No summary row.</p>"}
  </section>
  <section>
    <h2>Mask Contact Sheet</h2>
    <p>Black pixels are counted as colony area; white pixels are excluded.</p>
    <a href="mask_contact_sheet.png"><img src="mask_contact_sheet.png" alt="Mask contact sheet"></a>
  </section>
  <section>
    <h2>Overlay Contact Sheet</h2>
    <p>Red pixels are counted as colony area on the original well image.</p>
    <a href="overlay_contact_sheet.png"><img src="overlay_contact_sheet.png" alt="Overlay contact sheet"></a>
  </section>
  <section>
    <h2>Grid Contact Sheet</h2>
    <p>Red circles should sit on the well rims.</p>
    <a href="grid_contact_sheet.png"><img src="grid_contact_sheet.png" alt="Grid contact sheet"></a>
  </section>
  <section>
    <h2>Per-Image QC Files</h2>
    {html_table(image_links, ["image", "grid", "overlay", "mask"], escape_values=False)}
  </section>
  <section>
    <h2>QC Flags</h2>
    {html_table(sample_flags, ["level", "sample", "image", "well", "metric", "value", "reason"])}
  </section>
</body>
</html>
"""
        (sample_dir / "index.html").write_text(sample_page, encoding="utf-8")

        summary = sample_summary.get(sample, {})
        index_rows.append(
            {
                "sample": f'<a href="{html_link(qc_dir, sample_dir / "index.html")}">{html.escape(sample)}</a>',
                "image_count": summary.get("image_count", len(sample_artifacts)),
                "well_count": summary.get("well_count", ""),
                "mean_area_percent": summary.get("mean_area_percent", ""),
                "qc_flags": len(sample_flags),
            }
        )

    index_page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Sample Mask QC Index</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 28px; color: #1f2933; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 6px 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f6f8fa; }}
  </style>
</head>
<body>
  <h1>Sample Mask QC Index</h1>
  <p>Open each sample page to inspect mask, overlay, and grid contact sheets for that sample.</p>
  {html_table(index_rows, ["sample", "image_count", "well_count", "mean_area_percent", "qc_flags"], escape_values=False)}
</body>
</html>
"""
    (qc_dir / "index.html").write_text(index_page, encoding="utf-8")


def write_internal_control_report(
    output_dir: Path,
    image_rows: list[dict],
    sample_rows: list[dict],
    qc_rows: list[dict],
    failed_rows: list[dict],
    visual_artifacts: bool,
) -> None:
    report_path = output_dir / "internal_control_qc.html"
    visual_sections = ""
    if visual_artifacts:
        visual_sections = """
        <section>
          <h2>Visual QC By Sample</h2>
          <p>Open this folder to inspect mask, overlay, and grid contact sheets separately for each sample.</p>
          <p><a href="sample_mask_qc/index.html">sample mask QC index</a></p>
        </section>
        <section>
          <h2>Visual QC: Plate Detection</h2>
          <p>Red circles should be centered on the six stained wells in every input image.</p>
          <a href="grid_qc_contact_sheet.png"><img src="grid_qc_contact_sheet.png" alt="Grid QC contact sheet"></a>
        </section>
        <section>
          <h2>Visual QC: Colony Mask Overlay</h2>
          <p>Red pixels are the colonies counted for area measurement. Use this sheet to catch over- or under-detection by well.</p>
          <a href="overlay_contact_sheet.png"><img src="overlay_contact_sheet.png" alt="Colony mask overlay contact sheet"></a>
        </section>
        <section>
          <h2>Visual QC: Binary Masks</h2>
          <p>Black pixels are counted as colony area; white pixels are excluded.</p>
          <a href="mask_contact_sheet.png"><img src="mask_contact_sheet.png" alt="Binary mask contact sheet"></a>
        </section>
        <section>
          <h2>Visual QC: Deskewed Inputs</h2>
          <p>Plate edges should look horizontal after deskewing.</p>
          <a href="deskew_contact_sheet.png"><img src="deskew_contact_sheet.png" alt="Deskew contact sheet"></a>
        </section>
        """
    else:
        visual_sections = """
        <section>
          <h2>Visual QC Not Generated</h2>
          <p>This run used <code>--no-artifacts</code>, so mask overlays and contact sheets were skipped.</p>
        </section>
        """

    report = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Colony Area Internal Control QC</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 28px; color: #1f2933; }}
    h1, h2 {{ margin-bottom: 0.35rem; }}
    section {{ margin: 2rem 0; }}
    img {{ display: block; max-width: 100%; border: 1px solid #d0d7de; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 6px 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f6f8fa; }}
    code {{ background: #f6f8fa; padding: 2px 4px; }}
    .links a {{ margin-right: 1rem; }}
  </style>
</head>
<body>
  <h1>Colony Area Internal Control QC</h1>
  <p>This report is generated automatically for user-side quality control after colony area calculation.</p>

  <section>
    <h2>Run Summary</h2>
    <p>
      Processed images: <strong>{len(image_rows)}</strong><br>
      Failed images: <strong>{len(failed_rows)}</strong><br>
      QC flags: <strong>{len(qc_rows)}</strong>
    </p>
    <p class="links">
      <a href="colony_area_well_results.csv">well results CSV</a>
      <a href="colony_area_image_summary.csv">image summary CSV</a>
      <a href="colony_area_sample_summary.csv">sample summary CSV</a>
      <a href="qc_flags.csv">QC flags CSV</a>
      <a href="failed_images.csv">failed images CSV</a>
      <a href="duplicate_images.csv">duplicate images CSV</a>
    </p>
  </section>

  {visual_sections}

  <section>
    <h2>Sample Summary</h2>
    {html_table(sample_rows, ["sample", "image_count", "well_count", "mean_area_percent", "sd_area_percent", "min_area_percent", "max_area_percent"])}
  </section>

  <section>
    <h2>QC Flags</h2>
    {html_table(qc_rows, ["level", "sample", "image", "well", "metric", "value", "reason"])}
  </section>

  <section>
    <h2>Image Summary</h2>
    {html_table(image_rows, ["sample", "image", "mean_area_percent", "sd_area_percent", "min_area_percent", "max_area_percent", "well_cv", "deskew_angle_degrees", "grid_source", "grid_score"])}
  </section>
</body>
</html>
"""
    report_path.write_text(report, encoding="utf-8")


def run_batch(root: Path, output_dir: Path | None = None, save_artifacts: bool = True, deduplicate: bool = True) -> dict:
    root = root.expanduser().resolve()
    output_dir = (output_dir.expanduser().resolve() if output_dir else root / DEFAULT_OUTPUT_DIR_NAME)
    artifact_dir = output_dir / "per_image"
    output_dir.mkdir(parents=True, exist_ok=True)
    if save_artifacts:
        artifact_dir.mkdir(parents=True, exist_ok=True)

    discovered_images = find_input_images(root, excluded_dirs=[output_dir])
    if not discovered_images:
        raise FileNotFoundError(f"No supported image files found under {root}. Expected .tif, .tiff, .png, .jpg, or .jpeg.")

    duplicate_rows: list[dict] = []
    if deduplicate:
        images, duplicate_rows = deduplicate_images(discovered_images)
    else:
        images = discovered_images

    well_rows: list[dict] = []
    image_rows: list[dict] = []
    failed_rows: list[dict] = []
    deskew_contact_entries: list[tuple[str, Path]] = []
    grid_contact_entries: list[tuple[str, Path]] = []
    overlay_contact_entries: list[tuple[str, Path]] = []
    mask_contact_entries: list[tuple[str, Path]] = []
    artifact_rows: list[dict] = []

    for index, image_path in enumerate(images, start=1):
        sample = sample_label(root, image_path)
        label = f"{sample}/{image_path.stem}"
        image_output_dir = artifact_dir / sample / image_path.stem
        print(f"[{index}/{len(images)}] {label}")

        try:
            result = analyze_image(image_path, image_output_dir, save_artifacts=save_artifacts)
        except Exception as exc:  # noqa: BLE001 - batch report should preserve all failures.
            failed_rows.append({"sample": sample, "image": image_path.name, "image_path": str(image_path), "error": str(exc)})
            continue

        areas = [well["colony_area_percent"] for well in result["wells"]]
        mean_area, sd_area, min_area, max_area = summarize_values(areas)
        image_rows.append(
            {
                "sample": sample,
                "image": image_path.name,
                "image_path": str(image_path),
                "mean_area_percent": f"{mean_area:.4f}",
                "sd_area_percent": f"{sd_area:.4f}",
                "min_area_percent": f"{min_area:.4f}",
                "max_area_percent": f"{max_area:.4f}",
                "well_cv": f"{(sd_area / mean_area):.4f}" if mean_area else "",
                "deskew_angle_degrees": f"{result['deskew_angle_degrees']:.4f}",
                "deskew_score": f"{result['deskew_score']:.4f}",
                "grid_source": result["grid_source"],
                "grid_score": f"{result['grid_score']:.4f}",
                "diameter": f"{result['diameter']:.4f}",
                "x_spacing_1": f"{result['x_spacings'][0]:.4f}",
                "x_spacing_2": f"{result['x_spacings'][1]:.4f}",
                "y_spacing": f"{result['y_spacing']:.4f}",
                "discovery_pixels": result["discovery_pixels"],
                "crop_left": result["crop_left"],
                "crop_top": result["crop_top"],
                "crop_right": result["crop_right"],
                "crop_bottom": result["crop_bottom"],
                "result_path": result["result_path"],
            }
        )

        for well in result["wells"]:
            well_rows.append(
                {
                    "sample": sample,
                    "image": image_path.name,
                    "image_path": str(image_path),
                    "well": well["well"],
                    "center_x": f"{well['center_x']:.4f}",
                    "center_y": f"{well['center_y']:.4f}",
                    "diameter": f"{well['diameter']:.4f}",
                    "roi_pixels": well["roi_pixels"],
                    "mask_pixels": well["mask_pixels"],
                    "colony_area_percent": f"{well['colony_area_percent']:.4f}",
                }
            )

        if save_artifacts:
            base = image_path.stem
            deskew_contact_entries.append((f"{label} angle {result['deskew_angle_degrees']:.2f}", image_output_dir / f"{base}_deskewed_preview.png"))
            grid_contact_entries.append((label, image_output_dir / f"{base}_auto_grid_qc.png"))
            overlay_contact_entries.append((label, image_output_dir / f"{base}_auto_overlay_montage.png"))
            mask_contact_entries.append((label, image_output_dir / f"{base}_auto_mask_montage.png"))
            artifact_rows.append(
                {
                    "sample": sample,
                    "image": image_path.name,
                    "grid_path": str(image_output_dir / f"{base}_auto_grid_qc.png"),
                    "overlay_path": str(image_output_dir / f"{base}_auto_overlay_montage.png"),
                    "mask_path": str(image_output_dir / f"{base}_auto_mask_montage.png"),
                }
            )

    write_csv(
        output_dir / "colony_area_well_results.csv",
        well_rows,
        [
            "sample",
            "image",
            "image_path",
            "well",
            "center_x",
            "center_y",
            "diameter",
            "roi_pixels",
            "mask_pixels",
            "colony_area_percent",
        ],
    )
    write_csv(
        output_dir / "colony_area_image_summary.csv",
        image_rows,
        [
            "sample",
            "image",
            "image_path",
            "mean_area_percent",
            "sd_area_percent",
            "min_area_percent",
            "max_area_percent",
            "well_cv",
            "deskew_angle_degrees",
            "deskew_score",
            "grid_source",
            "grid_score",
            "diameter",
            "x_spacing_1",
            "x_spacing_2",
            "y_spacing",
            "discovery_pixels",
            "crop_left",
            "crop_top",
            "crop_right",
            "crop_bottom",
            "result_path",
        ],
    )
    write_csv(output_dir / "failed_images.csv", failed_rows, ["sample", "image", "image_path", "error"])
    write_csv(
        output_dir / "duplicate_images.csv",
        duplicate_rows,
        ["duplicate_group_id", "sha256", "kept_image_path", "skipped_image_path"],
    )

    sample_values: dict[str, list[float]] = defaultdict(list)
    sample_image_counts: dict[str, set[str]] = defaultdict(set)
    for row in well_rows:
        sample_values[row["sample"]].append(float(row["colony_area_percent"]))
        sample_image_counts[row["sample"]].add(row["image"])

    sample_rows: list[dict] = []
    for sample in sorted(sample_values):
        mean_area, sd_area, min_area, max_area = summarize_values(sample_values[sample])
        sample_rows.append(
            {
                "sample": sample,
                "image_count": len(sample_image_counts[sample]),
                "well_count": len(sample_values[sample]),
                "mean_area_percent": f"{mean_area:.4f}",
                "sd_area_percent": f"{sd_area:.4f}",
                "min_area_percent": f"{min_area:.4f}",
                "max_area_percent": f"{max_area:.4f}",
            }
        )
    write_csv(
        output_dir / "colony_area_sample_summary.csv",
        sample_rows,
        ["sample", "image_count", "well_count", "mean_area_percent", "sd_area_percent", "min_area_percent", "max_area_percent"],
    )

    qc_rows = build_qc_flags(image_rows, well_rows)
    write_csv(output_dir / "qc_flags.csv", qc_rows, ["level", "sample", "image", "well", "metric", "value", "reason"])

    if save_artifacts:
        make_contact_sheet(deskew_contact_entries, output_dir / "deskew_contact_sheet.png", columns=4, thumb_width=430)
        make_contact_sheet(grid_contact_entries, output_dir / "grid_qc_contact_sheet.png", columns=4, thumb_width=430)
        make_contact_sheet(overlay_contact_entries, output_dir / "overlay_contact_sheet.png", columns=4, thumb_width=430)
        make_contact_sheet(mask_contact_entries, output_dir / "mask_contact_sheet.png", columns=4, thumb_width=430)
        write_sample_mask_qc(output_dir, artifact_rows, sample_rows, qc_rows)

    write_internal_control_report(output_dir, image_rows, sample_rows, qc_rows, failed_rows, visual_artifacts=save_artifacts)

    print(f"Discovered {len(discovered_images)} supported image files.")
    if deduplicate:
        print(f"Skipped {len(duplicate_rows)} exact duplicate image files.")
    print(f"Processed {len(image_rows)} images; failed {len(failed_rows)} images.")
    print(f"Results written to {output_dir}")
    print(f"Internal control report: {output_dir / 'internal_control_qc.html'}")
    return {
        "root": str(root),
        "output_dir": str(output_dir),
        "processed_images": len(image_rows),
        "failed_images": len(failed_rows),
        "input_images": len(discovered_images),
        "duplicate_images": len(duplicate_rows),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate colony area from TIFF, PNG, or JPEG images in a directory."
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="Directory containing TIFF, PNG, or JPEG files. Subdirectories are scanned recursively.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=f"Output directory. Default: DIRECTORY/{DEFAULT_OUTPUT_DIR_NAME}",
    )
    parser.add_argument(
        "--no-artifacts",
        action="store_true",
        help="Skip preview images and contact sheets; CSVs and per-image text results are still written.",
    )
    parser.add_argument(
        "--keep-duplicates",
        action="store_true",
        help="Process exact duplicate image files as separate records. By default, duplicates are skipped.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_batch(
            args.directory,
            args.output,
            save_artifacts=not args.no_artifacts,
            deduplicate=not args.keep_duplicates,
        )
    except FileNotFoundError as exc:
        print(exc)
        return 1
    return 0 if result["failed_images"] == 0 else 1


def build_qc_flags(image_rows: list[dict], well_rows: list[dict]) -> list[dict]:
    flags: list[dict] = []
    if not image_rows:
        return flags

    diameters = np.array([float(row["diameter"]) for row in image_rows], dtype=float)
    median_diameter = float(np.median(diameters))
    y_spacings = np.array([float(row["y_spacing"]) for row in image_rows], dtype=float)
    median_y_spacing = float(np.median(y_spacings))
    all_areas = np.array([float(row["colony_area_percent"]) for row in well_rows], dtype=float)
    area_q1, area_q3 = np.percentile(all_areas, [25, 75]) if all_areas.size else (0.0, 0.0)
    area_iqr = float(area_q3 - area_q1)
    low_area_fence = float(area_q1 - 2.5 * area_iqr)
    high_area_fence = float(area_q3 + 2.5 * area_iqr)

    for row in image_rows:
        diameter = float(row["diameter"])
        y_spacing = float(row["y_spacing"])
        x_spacing_1 = float(row["x_spacing_1"])
        x_spacing_2 = float(row["x_spacing_2"])
        mean_area = float(row["mean_area_percent"])
        well_cv = float(row["well_cv"]) if row["well_cv"] else 0.0
        discovery_pixels = int(row["discovery_pixels"])
        deskew_angle = float(row["deskew_angle_degrees"])
        grid_source = row["grid_source"]
        grid_score = float(row["grid_score"])

        def add_image_flag(metric: str, value: str, reason: str) -> None:
            flags.append(
                {
                    "level": "image",
                    "sample": row["sample"],
                    "image": row["image"],
                    "well": "",
                    "metric": metric,
                    "value": value,
                    "reason": reason,
                }
            )

        if abs(diameter - median_diameter) / median_diameter > 0.08:
            add_image_flag("diameter", f"{diameter:.4f}", f"Diameter differs from median {median_diameter:.2f} by >8%.")
        if abs(y_spacing - median_y_spacing) / median_y_spacing > 0.08:
            add_image_flag("y_spacing", f"{y_spacing:.4f}", f"Y spacing differs from median {median_y_spacing:.2f} by >8%.")
        if abs(x_spacing_1 - x_spacing_2) / max(x_spacing_1, x_spacing_2) > 0.12:
            add_image_flag("x_spacing_balance", f"{x_spacing_1:.2f}/{x_spacing_2:.2f}", "Left and right x spacings differ by >12%.")
        if discovery_pixels < 20000 and grid_source != "rim":
            add_image_flag("discovery_pixels", str(discovery_pixels), "Low stain-pixel count; grid detection may be unstable.")
        if abs(deskew_angle) > 3.0:
            add_image_flag("deskew_angle_degrees", f"{deskew_angle:.4f}", "Large plate-edge deskew angle; inspect straightened image.")
        if grid_source != "rim":
            add_image_flag("grid_source", grid_source, "Rim-based grid was not confident; stain-based fallback was used.")
        elif grid_score < 14.0:
            add_image_flag("grid_score", f"{grid_score:.4f}", "Lower rim-grid confidence; inspect circle placement.")
        if well_cv > 0.35 and mean_area > 1:
            add_image_flag("well_cv", f"{well_cv:.4f}", "High variation between wells within the same image.")

    for row in well_rows:
        area = float(row["colony_area_percent"])
        reason = ""
        if area < 0.5:
            reason = "Very low colony area; possible segmentation miss."
        elif area > 92:
            reason = "Near-saturated colony mask; inspect dense/confluent well."
        elif area_iqr > 0 and (area < low_area_fence or area > high_area_fence):
            reason = f"Outside broad IQR fence [{low_area_fence:.2f}, {high_area_fence:.2f}]."

        if reason:
            flags.append(
                {
                    "level": "well",
                    "sample": row["sample"],
                    "image": row["image"],
                    "well": row["well"],
                    "metric": "colony_area_percent",
                    "value": f"{area:.4f}",
                    "reason": reason,
                }
            )

    return flags


if __name__ == "__main__":
    raise SystemExit(main())

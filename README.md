# CFA Colony Area Quantification

Automated colony formation assay (CFA) quantification for TIFF, PNG, or JPEG images of stained 6-well plates.

The pipeline detects the lower stained plate area, straightens the image from plate edges, finds the six wells, segments stained colony area in each well, and writes CSV measurements plus visual internal-control outputs.

For a step-by-step walkthrough, see [TUTORIAL.md](TUTORIAL.md).

## Author

- [itismeangie](https://github.com/itismeangie)

## What the Pipeline Produces

For each run, the output directory contains:

- `colony_area_well_results.csv`: one row per well.
- `colony_area_image_summary.csv`: one row per image.
- `colony_area_sample_summary.csv`: grouped summary by relative sample folder.
- `qc_flags.csv`: geometry, grid-confidence, and extreme-area warnings.
- `failed_images.csv`: images that could not be processed.
- `duplicate_images.csv`: exact duplicate files skipped by the default de-duplication step.
- `internal_control_qc.html`: open this first for visual QC.
- `sample_mask_qc/`: per-sample mask, overlay, and grid audit pages.
- `grid_qc_contact_sheet.png`: red well circles over every image.
- `overlay_contact_sheet.png`: red colony-mask overlay for every well.
- `mask_contact_sheet.png`: binary colony masks for every well.
- `deskew_contact_sheet.png`: deskewed image previews.
- `per_image/`: detailed per-image crops, masks, overlays, and text outputs.

The visual QC report is intended as an internal control so users can quickly verify that the well detection and masks match the stained colonies.
For sample-by-sample mask review, open `sample_mask_qc/index.html` and choose the sample you want to audit.

## Example Visual QC Outputs

The tool generates high-resolution per-image QC panels and an all-image HTML report so users can inspect whether well detection and colony masks match the stained wells.

**Plate/well detection:** red circles should sit on the well rims.

![Example grid detection](docs/assets/example_grid_detection.png)

**Colony mask overlay:** red pixels are counted as colony area.

![Example colony mask overlay](docs/assets/example_colony_overlay.png)

**Binary masks:** black pixels are counted, white pixels are excluded.

![Example binary masks](docs/assets/example_binary_masks.png)

## Installation

Clone the repository, then install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Alternatively, install the project in editable mode:

```bash
python -m pip install -e .
```

## Usage

Run the script on a directory containing `.tif`, `.tiff`, `.png`, `.jpg`, or `.jpeg` files:

```bash
python calculate_colony_area.py /path/to/image_folder
```

By default, results are written to:

```text
/path/to/image_folder/_colonyarea_results
```

To choose a separate output folder:

```bash
python calculate_colony_area.py /path/to/image_folder -o /path/to/output_folder
```

If installed with `pip install -e .`, the command-line entry point is:

```bash
cfa-colony-area /path/to/image_folder -o /path/to/output_folder
```

To write CSVs and per-image text results without visual contact sheets:

```bash
python calculate_colony_area.py /path/to/image_folder --no-artifacts
```

The default mode is recommended because it produces `internal_control_qc.html` with visual masks and overlays.

Exact duplicate image files are skipped by default so copied files are not counted as independent images. To intentionally process duplicate files as separate records:

```bash
python calculate_colony_area.py /path/to/image_folder --keep-duplicates
```

## Input Organization

The input directory is scanned recursively for `.tif`, `.tiff`, `.png`, `.jpg`, and `.jpeg` files. The relative parent folder path is used as the sample label. For one-level folders this is just the folder name; for nested folders this keeps the experiment context.

Example:

```text
input/
  experiment_1/
    M ctrl/
      img057.tif
      img058.jpg
  experiment_2/
    M2/
      img074.png
      img075.tif
```

The output summary will use `experiment_1/M ctrl` and `experiment_2/M2` as sample names.

## Method Summary

1. Detect stained pixels in the lower portion of the image to localize the plate region.
2. Estimate and correct image horizon using low-saturation plastic plate edges.
3. Detect six well positions using circular rim/plate-edge responses.
4. Fall back to stain-based well detection if rim detection is not confident.
5. Crop each well, threshold blue/purple colony stain across a broad well-interior ROI, remove elongated rim artifacts while preserving compact edge colonies, and calculate colony area as:

```text
100 * colony_mask_pixels / well_roi_pixels
```

6. Write CSVs and visual QC artifacts.

## Quality Control

Always inspect `internal_control_qc.html` after a run.

Check:

- Red well circles are centered on the stained wells.
- Red mask overlays cover central and edge colonies without including large background or long rim streaks.
- Binary masks match the visible stained colony area.
- `sample_mask_qc/index.html` for per-sample mask and overlay contact sheets.
- `qc_flags.csv` for low grid confidence, fallback grid detection, or near-saturated wells.

## Repository Hygiene

Raw TIFF/JPEG/PNG/PDF/XLSX/ZIP files and generated output folders are ignored by `.gitignore`. Keep large raw image data outside the code repository or use Git LFS if raw data must be versioned.

## Development Checks

Run the lightweight test suite:

```bash
python -m unittest discover -s tests -v
```

Check that the scripts compile:

```bash
python -m py_compile analyze_cfa_plate_one.py batch_cfa_colony_area.py calculate_colony_area.py
```

## Limitations

- The pipeline is tuned for stained 6-well plate images where the lower half contains the target six wells.
- Strong glare, very faint rims, nonstandard plate geometry, or extreme cropping may require manual review.
- The segmentation thresholds are tuned for blue/purple colony stain and should be revalidated for different stains or imaging conditions.

## License

MIT License. See [LICENSE](LICENSE).

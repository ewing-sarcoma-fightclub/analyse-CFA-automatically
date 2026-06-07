# Tutorial: Quantify Colony Area From Plate Images

This tutorial shows how to run the CFA colony-area tool on a folder of stained
6-well plate images and how to audit the output masks.

## Visual Overview

The tool is designed to produce measurements and human-auditable QC images in
the same run.

| Well detection | Colony overlay | Binary mask |
|---|---|---|
| ![Example grid detection](docs/assets/example_grid_detection.png) | ![Example colony mask overlay](docs/assets/example_colony_overlay.png) | ![Example binary masks](docs/assets/example_binary_masks.png) |

In the QC outputs, red circles mark detected wells, red overlay pixels mark
colonies counted as area, and black mask pixels are the final counted colony
area.

## 1. Prepare Input Images

Put TIFF, PNG, or JPEG plate images in one input folder. Subfolders are allowed;
the relative parent folder becomes the sample name in the output tables.

Example:

```text
input_images/
  MHH-ES-1/
    shCtrl/
      img001.tif
      img002.tif
    SF3B4_CDS/
      img003.tif
      img004.tif
```

Supported extensions:

```text
.tif .tiff .png .jpg .jpeg
```

The images should show the stained lower part of a 6-well plate. The algorithm
automatically crops to the stained plate region, straightens the plate edge, and
detects the six wells.

## 2. Install

From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Optional editable install:

```bash
python -m pip install -e .
```

## 3. Run The Analysis

Run directly with Python:

```bash
python calculate_colony_area.py /path/to/input_images -o /path/to/output
```

Or, after editable install:

```bash
cfa-colony-area /path/to/input_images -o /path/to/output
```

If `-o` is omitted, output is written to:

```text
/path/to/input_images/_colonyarea_results
```

By default, exact duplicate image files are skipped so copied files are not
counted twice. To intentionally keep duplicate files as separate records:

```bash
python calculate_colony_area.py /path/to/input_images -o /path/to/output --keep-duplicates
```

## 4. Review The Output

Open this file first:

```text
/path/to/output/internal_control_qc.html
```

This report links to all measurement tables and visual QC panels.

Important output files:

```text
colony_area_well_results.csv       one row per well
colony_area_image_summary.csv      one row per image
colony_area_sample_summary.csv     one row per sample folder
qc_flags.csv                       images/wells requiring review
duplicate_images.csv               exact duplicates skipped
failed_images.csv                  images that failed processing
sample_mask_qc/index.html          per-sample QC navigator
```

## 5. Audit The Masks

Inspect these visual outputs before using the measurements:

- `grid_qc_contact_sheet.png`: red circles should sit on the six well rims.
- `overlay_contact_sheet.png`: red pixels are counted as colony area.
- `mask_contact_sheet.png`: black pixels are counted; white pixels are excluded.
- `deskew_contact_sheet.png`: plate edges should look horizontal.
- `sample_mask_qc/index.html`: sample-by-sample mask and overlay review.

### Well Detection QC

Red circles should be centered on the well rims. If circles are shifted away
from the stained wells, review that image before using the measurement.

![Example grid detection](docs/assets/example_grid_detection.png)

### Colony Overlay QC

Red pixels are the segmented colonies counted by the algorithm. The overlay
should cover both central colonies and edge colonies without including obvious
background or long plate-rim streaks.

![Example colony mask overlay](docs/assets/example_colony_overlay.png)

### Binary Mask QC

Black pixels are included in the colony-area numerator; white pixels are
excluded.

![Example binary masks](docs/assets/example_binary_masks.png)

## 6. Interpret Measurements

The primary measurement is:

```text
colony_area_percent = 100 * colony_mask_pixels / well_roi_pixels
```

Use `colony_area_well_results.csv` for well-level statistics and
`colony_area_image_summary.csv` for image-level summaries. Always exclude or
manually review samples flagged in `qc_flags.csv` if the mask or well detection
does not match the visible colonies.

## 7. Run Development Checks

```bash
python -m py_compile analyze_cfa_plate_one.py batch_cfa_colony_area.py calculate_colony_area.py
python -m unittest discover -s tests -v
```

## Notes And Limitations

- The method is tuned for blue/purple stained colony-formation assays in
  6-well plates.
- Very strong glare, poor focus, nonstandard cropping, or unusual stain color
  can require manual QC or threshold adjustment.
- The visual QC reports are part of the intended workflow; do not rely only on
  CSV numbers.

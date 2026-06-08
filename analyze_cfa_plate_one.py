#!/usr/bin/env python3
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage, signal


COLONY_ROI_SCALE = 0.995
COLONY_WEAK_EXPANSION_PIXELS = 3


def kmeans_1d(values: np.ndarray, k: int, iterations: int = 100) -> np.ndarray:
    centers = np.percentile(values, np.linspace(0, 100, k + 2)[1:-1]).astype(float)
    for _ in range(iterations):
        labels = np.abs(values[:, None] - centers[None, :]).argmin(axis=1)
        updated = np.array(
            [values[labels == idx].mean() if np.any(labels == idx) else centers[idx] for idx in range(k)],
            dtype=float,
        )
        updated.sort()
        if np.all(np.abs(updated - centers) < 0.01):
            break
        centers = updated
    return centers


def otsu_threshold(values: np.ndarray, bins: int = 256) -> float:
    hist, edges = np.histogram(values, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2.0
    below_weight = np.cumsum(hist).astype(float)
    above_weight = np.cumsum(hist[::-1])[::-1].astype(float)
    below_mean = np.cumsum(hist * centers) / (below_weight + 1e-9)
    above_mean = (np.cumsum((hist * centers)[::-1]) / (above_weight[::-1] + 1e-9))[::-1]
    variance = below_weight[:-1] * above_weight[1:] * (below_mean[:-1] - above_mean[1:]) ** 2
    if variance.size == 0:
        return float(np.median(values))
    return float(centers[:-1][int(variance.argmax())])


def initial_stain_mask(rgb: np.ndarray) -> np.ndarray:
    channels = rgb.astype(np.int16)
    red = channels[:, :, 0]
    green = channels[:, :, 1]
    blue = channels[:, :, 2]
    maxc = channels.max(axis=2)
    minc = channels.min(axis=2)
    saturation = maxc - minc

    blue_colony = (blue > green + 18) & (blue >= red - 10)
    purple_colony = (blue > green + 14) & (red > green + 10)
    mask = (blue_colony | purple_colony) & (saturation > 50) & (green < 145) & (maxc < 190) & (maxc > 45)

    height, width = green.shape
    mask[: int(height * 0.45), :] = False
    mask[:, :50] = False
    mask[:, width - 50 :] = False
    return filter_components(mask, min_area=3, max_area=2000, remove_skinny=True)


def stain_bbox(rgb: np.ndarray, pad: int = 180) -> tuple[int, int, int, int]:
    mask = initial_stain_mask(rgb)
    ys, xs = np.nonzero(mask)
    height, width = mask.shape
    if xs.size < 1000:
        return 0, int(height * 0.45), width, height

    left = int(np.percentile(xs, 1)) - pad
    top = int(np.percentile(ys, 1)) - pad
    right = int(np.percentile(xs, 99)) + pad
    bottom = int(np.percentile(ys, 99)) + pad
    return max(0, left), max(0, top), min(width, right), min(height, bottom)


def plate_edge_data(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int] | None:
    left, top, right, bottom = stain_bbox(rgb)
    crop = rgb[top:bottom, left:right].astype(float)
    if crop.size == 0:
        return None

    gray = crop.mean(axis=2)
    saturation = crop.max(axis=2) - crop.min(axis=2)
    maxc = crop.max(axis=2)
    minc = crop.min(axis=2)
    gy = np.abs(ndimage.sobel(gray, axis=0))
    gx = np.abs(ndimage.sobel(gray, axis=1))

    # Use transparent plastic plate edges: low saturation, reasonably bright,
    # and stronger vertical gradient than horizontal gradient.
    candidates = gy * (gy > gx * 0.75) * (saturation < 45) * (maxc > 110) * (minc > 65)
    nonzero = candidates[candidates > 0]
    if nonzero.size < 500:
        return None

    threshold = np.percentile(nonzero, 92)
    yy, xx = np.nonzero(candidates > threshold)
    if xx.size < 500:
        return None
    weights = candidates[yy, xx]
    return xx + left, yy + top, weights, rgb.shape[1], rgb.shape[0]


def plate_edge_alignment_score(edge_data: tuple[np.ndarray, np.ndarray, np.ndarray, int, int], angle_degrees: float) -> float:
    x, y, weights, width, height = edge_data
    center_x = width / 2.0
    center_y = height / 2.0
    angle = np.deg2rad(angle_degrees)
    projected_y = -(x - center_x) * np.sin(angle) + (y - center_y) * np.cos(angle) + center_y
    bins = np.round(projected_y).astype(int)
    valid = (bins >= 0) & (bins < height)
    if not np.any(valid):
        return 0.0

    histogram = np.bincount(bins[valid], weights=weights[valid], minlength=height)
    smoothed = ndimage.gaussian_filter1d(histogram, 1.2)
    top_lines = np.sort(smoothed)[-16:]
    return float((top_lines * top_lines).sum() / max(smoothed.sum(), 1.0))


def estimate_plate_edge_deskew_angle(rgb: np.ndarray) -> tuple[float, float]:
    edge_data = plate_edge_data(rgb)
    if edge_data is None:
        return 0.0, 0.0

    coarse_angles = np.linspace(-5.0, 5.0, 101)
    coarse_scores = np.array([plate_edge_alignment_score(edge_data, angle) for angle in coarse_angles])
    coarse_best = float(coarse_angles[int(coarse_scores.argmax())])

    fine_angles = np.linspace(coarse_best - 0.25, coarse_best + 0.25, 51)
    fine_scores = np.array([plate_edge_alignment_score(edge_data, angle) for angle in fine_angles])
    best_index = int(fine_scores.argmax())
    return float(fine_angles[best_index]), float(fine_scores[best_index])


def deskew_image(image: Image.Image) -> tuple[Image.Image, float, float]:
    rgb = np.asarray(image.convert("RGB"))
    angle, score = estimate_plate_edge_deskew_angle(rgb)
    if abs(angle) < 0.05:
        return image, 0.0, score
    deskewed = image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(255, 255, 255))
    return deskewed, angle, score


def filter_components(mask: np.ndarray, min_area: int, max_area: int, remove_skinny: bool) -> np.ndarray:
    labels, count = ndimage.label(mask)
    if count == 0:
        return mask

    areas = np.bincount(labels.ravel())
    slices = ndimage.find_objects(labels)
    keep = np.zeros(count + 1, dtype=bool)
    for label, component_slice in enumerate(slices, start=1):
        if component_slice is None:
            continue
        area = areas[label]
        if area < min_area or area > max_area:
            continue

        if remove_skinny:
            ys, xs = np.nonzero(labels[component_slice] == label)
            box_h = ys.max() - ys.min() + 1
            box_w = xs.max() - xs.min() + 1
            longest = max(box_h, box_w)
            fill = area / float(box_h * box_w)
            skinny = min(box_h, box_w) / float(longest) < 0.22
            sparse_edge = longest > 36 and fill < 0.18
            if skinny or sparse_edge:
                continue

        keep[label] = True
    keep[0] = False
    return keep[labels]


def detect_grid_from_stain(rgb: np.ndarray) -> tuple[list[float], list[float], float, np.ndarray]:
    discovery_mask = initial_stain_mask(rgb)
    ys, xs = np.nonzero(discovery_mask)
    if xs.size < 1000:
        raise RuntimeError("Could not detect enough stained colony pixels in the lower half of the image.")

    x_centers = kmeans_1d(xs.astype(float), 3)
    y_centers = kmeans_1d(ys.astype(float), 2)
    x_spacing = float(np.min(np.diff(x_centers)))
    y_spacing = float(np.median(np.diff(y_centers)))
    well_diameter = min(x_spacing, y_spacing) * 0.90
    return x_centers.tolist(), y_centers.tolist(), well_diameter, discovery_mask


def ring_kernel(radius: float, thickness: float = 2.0) -> np.ndarray:
    pad = int(np.ceil(radius + thickness + 2))
    yy, xx = np.ogrid[-pad : pad + 1, -pad : pad + 1]
    distance = np.sqrt(xx * xx + yy * yy)
    kernel = (np.abs(distance - radius) <= thickness).astype(float)
    kernel -= kernel.mean()
    norm = np.sqrt(float((kernel * kernel).sum()))
    return kernel / norm if norm else kernel


def plate_rim_response(rgb: np.ndarray, downsample: int = 4) -> tuple[np.ndarray, np.ndarray]:
    height, width = rgb.shape[:2]
    small = Image.fromarray(rgb).resize((width // downsample, height // downsample), Image.Resampling.LANCZOS)
    arr = np.asarray(small).astype(float)
    gray = arr.mean(axis=2)
    saturation = arr.max(axis=2) - arr.min(axis=2)
    maxc = arr.max(axis=2)

    gray_edges = np.hypot(ndimage.sobel(gray, axis=0), ndimage.sobel(gray, axis=1))
    saturation_edges = np.hypot(ndimage.sobel(saturation, axis=0), ndimage.sobel(saturation, axis=1))
    plastic_weight = ((saturation < 85) & (maxc > 70)).astype(float) * 0.8 + 0.2
    edge_image = (gray_edges + 0.25 * saturation_edges) * plastic_weight
    edge_image = ndimage.gaussian_filter(edge_image, 0.7)
    edge_image = (edge_image - edge_image.mean()) / (edge_image.std() + 1e-6)

    best_response = np.full_like(edge_image, -999.0)
    best_radius = np.zeros_like(edge_image)
    for radius in np.arange(42, 64, 2):
        response = signal.fftconvolve(edge_image, ring_kernel(radius)[::-1, ::-1], mode="same")
        baseline = np.percentile(response, 50)
        high = np.percentile(response, 99)
        response = (response - baseline) / (high - baseline + 1e-6)
        update = response > best_response
        best_response[update] = response[update]
        best_radius[update] = radius

    left, top, right, bottom = stain_bbox(rgb, pad=130)
    valid = np.zeros_like(best_response, dtype=bool)
    valid_top = max(0, top // downsample - 30)
    valid_bottom = min(best_response.shape[0], bottom // downsample + 30)
    valid_left = max(0, left // downsample - 30)
    valid_right = min(best_response.shape[1], right // downsample + 30)
    valid[valid_top:valid_bottom, valid_left:valid_right] = True
    valid[: int(best_response.shape[0] * 0.43), :] = False
    best_response[~valid] = 0
    return best_response, best_radius


def local_max_score(response: np.ndarray, x: float, y: float, window: int = 8) -> float:
    height, width = response.shape
    left = max(0, int(round(x - window)))
    right = min(width, int(round(x + window + 1)))
    top = max(0, int(round(y - window)))
    bottom = min(height, int(round(y + window + 1)))
    if left >= right or top >= bottom:
        return 0.0
    return float(response[top:bottom, left:right].max())


def unique_peak_values(values: np.ndarray, scores: np.ndarray, tolerance: float = 8.0) -> list[float]:
    ordered = np.argsort(scores)[::-1]
    unique: list[float] = []
    for index in ordered:
        value = float(values[index])
        if all(abs(value - existing) > tolerance for existing in unique):
            unique.append(value)
    return sorted(unique)


def select_rim_grid(response: np.ndarray) -> tuple[list[float], list[float], float] | None:
    maxima = ndimage.maximum_filter(response, size=25)
    peak_y, peak_x = np.nonzero((response == maxima) & (response > 1.4))
    if peak_x.size < 4:
        return None

    peak_scores = response[peak_y, peak_x]
    order = np.argsort(peak_scores)[::-1][:80]
    peak_x = peak_x[order]
    peak_y = peak_y[order]
    peak_scores = peak_scores[order]
    unique_x = unique_peak_values(peak_x, peak_scores)
    unique_y = unique_peak_values(peak_y, peak_scores)

    x_triplets: list[tuple[float, float, float]] = []
    for triplet in itertools.combinations(unique_x, 3):
        spacing = np.diff(triplet)
        median_spacing = float(np.median(spacing))
        if median_spacing < 85 or median_spacing > 140:
            continue
        if (max(spacing) - min(spacing)) / max(spacing) > 0.18:
            continue
        x_triplets.append(tuple(float(value) for value in triplet))

    y_pairs: list[tuple[float, float]] = []
    for top, bottom in itertools.combinations(unique_y, 2):
        spacing = float(bottom - top)
        if 85 <= spacing <= 150:
            y_pairs.append((float(top), float(bottom)))

    best: tuple[float, tuple[float, float, float], tuple[float, float], list[float]] | None = None
    for x_centers in x_triplets:
        for y_centers in y_pairs:
            cell_scores = [local_max_score(response, x, y) for y in y_centers for x in x_centers]
            strong_cells = sum(score > 2.0 for score in cell_scores)
            if strong_cells < 4 or min(cell_scores) < 0.75:
                continue

            x_spacing = np.diff(x_centers)
            y_spacing = y_centers[1] - y_centers[0]
            geometry_penalty = abs(x_spacing[0] - x_spacing[1]) * 0.01 + abs(float(np.mean(x_spacing)) - y_spacing) * 0.005
            score = sum(cell_scores) + 0.5 * min(cell_scores) - geometry_penalty
            if best is None or score > best[0]:
                best = (score, x_centers, y_centers, cell_scores)

    if best is None:
        return None

    score, x_centers, y_centers, _ = best
    return list(x_centers), list(y_centers), float(score)


def detect_grid_from_rims(rgb: np.ndarray) -> tuple[list[float], list[float], float, float] | None:
    downsample = 4
    response, _ = plate_rim_response(rgb, downsample=downsample)
    selected = select_rim_grid(response)
    if selected is None:
        return None

    x_small, y_small, score = selected
    x_centers = [center * downsample for center in x_small]
    y_centers = [center * downsample for center in y_small]
    spacing_values = [*np.diff(x_centers), y_centers[1] - y_centers[0]]
    well_diameter = float(min(spacing_values) * 0.90)
    return x_centers, y_centers, well_diameter, score


def detect_grid(rgb: np.ndarray) -> tuple[list[float], list[float], float, np.ndarray, str, float]:
    discovery_mask = initial_stain_mask(rgb)
    rim_grid = detect_grid_from_rims(rgb)
    if rim_grid is not None:
        x_centers, y_centers, well_diameter, score = rim_grid
        return x_centers, y_centers, well_diameter, discovery_mask, "rim", score

    x_centers, y_centers, well_diameter, discovery_mask = detect_grid_from_stain(rgb)
    return x_centers, y_centers, well_diameter, discovery_mask, "stain", 0.0


def crop_with_padding(image: Image.Image, left: int, top: int, right: int, bottom: int) -> Image.Image:
    if left >= 0 and top >= 0 and right <= image.width and bottom <= image.height:
        return image.crop((left, top, right, bottom))

    out = Image.new("RGB", (right - left, bottom - top), "white")
    source_box = (max(left, 0), max(top, 0), min(right, image.width), min(bottom, image.height))
    cropped = image.crop(source_box)
    out.paste(cropped, (source_box[0] - left, source_box[1] - top))
    return out


def colony_mask(rgb: np.ndarray, ellipse_scale: float = COLONY_ROI_SCALE) -> tuple[np.ndarray, np.ndarray]:
    channels = rgb.astype(np.int16)
    red = channels[:, :, 0]
    green = channels[:, :, 1]
    blue = channels[:, :, 2]
    maxc = channels.max(axis=2)
    minc = channels.min(axis=2)
    saturation = maxc - minc

    height, width = green.shape
    yy, xx = np.ogrid[:height, :width]
    cx = (width - 1) / 2.0
    cy = (height - 1) / 2.0
    radius = min(width, height) * ellipse_scale / 2.0
    well_roi = ((xx - cx) / radius) ** 2 + ((yy - cy) / radius) ** 2 <= 1.0

    blue_excess = blue - green
    red_excess = red - green
    darkness = 255.0 - (0.30 * red + 0.59 * green + 0.11 * blue)
    stain_density = blue_excess + 0.35 * np.maximum(red_excess, 0) + 0.45 * darkness
    roi_density = stain_density[well_roi]
    strong_threshold = max(otsu_threshold(roi_density), float(np.percentile(roi_density, 48)), 65.0)
    weak_threshold = max(strong_threshold * 0.72, 45.0)

    chromatic = (
        (blue_excess > 8)
        & ((blue >= red - 16) | (red_excess > 5))
        & (saturation > 22)
        & (green < 182)
        & (maxc < 225)
    )
    strong = ((stain_density > strong_threshold) & chromatic) | ((maxc < 90) & (saturation > 14) & (blue >= green - 5))
    weak = ((stain_density > weak_threshold) & chromatic) | ((maxc < 105) & (saturation > 12) & (blue >= green - 8))
    strong &= well_roi
    weak &= well_roi
    expansion = ndimage.binary_dilation(
        strong,
        structure=np.ones((3, 3)),
        iterations=COLONY_WEAK_EXPANSION_PIXELS,
    )
    mask = strong | (weak & expansion)
    mask = ndimage.binary_closing(mask, structure=np.ones((2, 2)))
    cleaned = filter_components(mask, min_area=3, max_area=mask.size, remove_skinny=False)
    cleaned = remove_outer_rim_fragments(cleaned, well_roi)
    return cleaned, well_roi


def remove_outer_rim_fragments(mask: np.ndarray, roi: np.ndarray) -> np.ndarray:
    labels, count = ndimage.label(mask)
    if count == 0:
        return mask

    edge_iterations = max(6, int(round(min(mask.shape) * 0.025)))
    roi_eroded = ndimage.binary_erosion(roi, iterations=edge_iterations)
    outer_annulus = roi & ~roi_eroded
    height, width = mask.shape
    center_x = (width - 1) / 2.0
    center_y = (height - 1) / 2.0
    radius = float(np.sqrt(max(float(roi.sum()), 1.0) / np.pi))
    cleaned = mask.copy()
    slices = ndimage.find_objects(labels)
    areas = np.bincount(labels.ravel())
    for label, component_slice in enumerate(slices, start=1):
        if component_slice is None:
            continue

        component = labels[component_slice] == label
        local_outer = outer_annulus[component_slice]
        edge_fraction = float((component & local_outer).sum()) / float(areas[label])

        ys, xs = np.nonzero(component)
        box_h = ys.max() - ys.min() + 1
        box_w = xs.max() - xs.min() + 1
        longest = max(box_h, box_w)
        shortest = min(box_h, box_w)
        aspect = shortest / float(longest)
        fill = areas[label] / float(box_h * box_w)
        global_y = ys + component_slice[0].start
        global_x = xs + component_slice[1].start
        normalized_radius = np.sqrt((global_x - center_x) ** 2 + (global_y - center_y) ** 2) / radius
        outer_75_fraction = float((normalized_radius > 0.75).mean())
        outer_85_fraction = float((normalized_radius > 0.85).mean())
        area_fraction = float(areas[label]) / float(mask.size)
        radial_std = float(normalized_radius.std())
        angles = np.sort(np.arctan2(global_y - center_y, global_x - center_x))
        gaps = np.diff(np.concatenate([angles, [angles[0] + 2 * np.pi]]))
        angular_span = float(2 * np.pi - gaps.max())
        well_size = min(mask.shape)
        very_skinny = edge_fraction > 0.70 and longest > well_size * 0.45 and aspect < 0.16 and fill < 0.18
        smooth_rim_band = (
            area_fraction > 0.003
            and outer_85_fraction > 0.80
            and radial_std < 0.045
            and angular_span > 0.65
            and (fill < 0.24 or aspect < 0.22 or edge_fraction > 0.30)
        )
        huge_edge_smear = areas[label] > mask.size * 0.04 and fill < 0.12 and edge_fraction > 0.80 and radial_std < 0.06
        crescent_rim_stain = (
            area_fraction > 0.005
            and outer_75_fraction > 0.85
            and outer_85_fraction > 0.70
            and radial_std < 0.055
            and angular_span > 0.65
            and longest > well_size * 0.18
            and fill < 0.28
        )
        if very_skinny or smooth_rim_band or huge_edge_smear or crescent_rim_stain:
            cleaned[labels == label] = False

    return cleaned


def label_above(image: Image.Image, label: str) -> Image.Image:
    out = Image.new("RGB", (image.width, image.height + 28), "white")
    out.paste(image, (0, 28))
    draw = ImageDraw.Draw(out)
    draw.text((8, 7), label, fill=(0, 0, 0))
    return out


def montage(images: list[Image.Image], columns: int = 3) -> Image.Image:
    rows = (len(images) + columns - 1) // columns
    cell_w = max(img.width for img in images)
    cell_h = max(img.height for img in images)
    out = Image.new("RGB", (columns * cell_w, rows * cell_h), "white")
    for idx, image in enumerate(images):
        out.paste(image, ((idx % columns) * cell_w, (idx // columns) * cell_h))
    return out


def draw_grid_qc(image: Image.Image, x_centers: list[float], y_centers: list[float], diameter: float) -> Image.Image:
    qc = image.copy()
    draw = ImageDraw.Draw(qc)
    radius = diameter / 2.0
    for idx, (cy, cx) in enumerate(((y, x) for y in y_centers for x in x_centers), start=1):
        box = (cx - radius, cy - radius, cx + radius, cy + radius)
        draw.ellipse(box, outline=(255, 0, 0), width=6)
        draw.text((cx - radius + 12, cy - radius + 12), str(idx), fill=(255, 0, 0))
    return qc


def analyze_image(input_path: Path, output_dir: Path, save_artifacts: bool = True) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(input_path) as opened_image:
        original_image = opened_image.convert("RGB")
    image, deskew_angle, deskew_score = deskew_image(original_image)
    rgb = np.asarray(image)
    x_centers, y_centers, diameter, discovery_mask, grid_source, grid_score = detect_grid(rgb)
    base = input_path.stem

    margin = int(diameter * 0.35)
    crop_left = int(min(x_centers) - diameter / 2 - margin)
    crop_top = int(min(y_centers) - diameter / 2 - margin)
    crop_right = int(max(x_centers) + diameter / 2 + margin)
    crop_bottom = int(max(y_centers) + diameter / 2 + margin)
    if save_artifacts:
        image.save(output_dir / f"{base}_deskewed_preview.png")

        lower_plate_crop = crop_with_padding(image, crop_left, crop_top, crop_right, crop_bottom)
        lower_plate_crop.save(output_dir / f"{base}_auto_lower_plate_crop.tif")
        lower_plate_crop.save(output_dir / f"{base}_auto_lower_plate_crop_preview.png")

        grid_qc = draw_grid_qc(image, x_centers, y_centers, diameter)
        crop_with_padding(grid_qc, crop_left, crop_top, crop_right, crop_bottom).save(output_dir / f"{base}_auto_grid_qc.png")

        mask_preview = Image.fromarray(np.where(discovery_mask, 0, 255).astype(np.uint8), mode="L").convert("RGB")
        crop_with_padding(mask_preview, crop_left, crop_top, crop_right, crop_bottom).save(
            output_dir / f"{base}_auto_discovery_mask.png"
        )

    overlays: list[Image.Image] = []
    masks: list[Image.Image] = []
    mask_stack: list[Image.Image] = []
    rows = ["Well #\tCenter X\tCenter Y\tDiameter\tColony Area Percent"]
    well_results: list[dict] = []
    radius = diameter / 2.0
    for index, (cy, cx) in enumerate(((y, x) for y in y_centers for x in x_centers), start=1):
        left = int(round(cx - radius))
        top = int(round(cy - radius))
        right = int(round(cx + radius))
        bottom = int(round(cy + radius))
        well = crop_with_padding(image, left, top, right, bottom)
        well_rgb = np.asarray(well)
        mask, roi = colony_mask(well_rgb)
        area_percent = 100.0 * mask.sum() / roi.sum()
        rows.append(f"{index}\t{cx:.1f}\t{cy:.1f}\t{diameter:.1f}\t{area_percent:.2f}")
        well_results.append(
            {
                "well": index,
                "center_x": cx,
                "center_y": cy,
                "diameter": diameter,
                "colony_area_percent": area_percent,
                "roi_pixels": int(roi.sum()),
                "mask_pixels": int(mask.sum()),
            }
        )

        if save_artifacts:
            overlay_arr = well_rgb.copy()
            overlay_arr[mask] = np.array([255, 0, 0], dtype=np.uint8)
            overlays.append(label_above(Image.fromarray(overlay_arr), f"Well {index}: {area_percent:.2f}%"))

            mask_img = Image.fromarray(np.where(mask, 0, 255).astype(np.uint8), mode="L")
            masks.append(label_above(mask_img.convert("RGB"), f"Well {index}: {area_percent:.2f}%"))
            mask_stack.append(mask_img)

    if save_artifacts:
        montage(overlays).save(output_dir / f"{base}_auto_overlay_montage.png")
        montage(masks).save(output_dir / f"{base}_auto_mask_montage.png")
        mask_stack[0].save(output_dir / f"{base}_auto_mask_stack.tif", save_all=True, append_images=mask_stack[1:])

    result_path = output_dir / f"results_{base}_auto_colony_area.txt"
    result_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return {
        "input_path": str(input_path),
        "base": base,
        "image_width": image.width,
        "image_height": image.height,
        "deskew_angle_degrees": deskew_angle,
        "deskew_score": deskew_score,
        "grid_source": grid_source,
        "grid_score": grid_score,
        "x_centers": x_centers,
        "y_centers": y_centers,
        "x_spacings": np.diff(x_centers).tolist(),
        "y_spacing": float(y_centers[1] - y_centers[0]),
        "diameter": diameter,
        "discovery_pixels": int(discovery_mask.sum()),
        "crop_left": crop_left,
        "crop_top": crop_top,
        "crop_right": crop_right,
        "crop_bottom": crop_bottom,
        "result_path": str(result_path),
        "wells": well_results,
    }


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: analyze_cfa_plate_one.py INPUT_IMAGE OUTPUT_DIR", file=sys.stderr)
        return 2

    result = analyze_image(Path(sys.argv[1]), Path(sys.argv[2]), save_artifacts=True)
    print(result["result_path"])
    print("Well #\tCenter X\tCenter Y\tDiameter\tColony Area Percent")
    for well in result["wells"]:
        print(
            f"{well['well']}\t{well['center_x']:.1f}\t{well['center_y']:.1f}\t"
            f"{well['diameter']:.1f}\t{well['colony_area_percent']:.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

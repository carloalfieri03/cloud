"""
To load test the architecture since from coco there are no large images this script generates large size images synthetically. 
To do so ensures to generate large size images for both hard,easy,medium to detect classes. 
Doing so we can mix different conditions in load testing. 

The method used to generate such images is explained inside generate_single_large_image function

The key arguments to set in the main part are  target_sizes_mb=[8, 15, 20] and images_per_difficulty=10. 
In this parametrization 8,15,20mb are the target size that we want to achieve 
and for each of these target we will have 10 images from model detection difficulty classes. 
N.B. We will have some images that are duplicates but edited differenlty to be of size big, if you look at them you see that are patched differenlty to increase size. 

Is better to set a higher target size than the desirable since is hard to be very precise with this parametrization. 
Here what was obtained in a run was

Size range: 7.3MB - 19.4MB
Average: 11.8MB

Each run can yield different results. 

OUTPUT:
in the all_test_images all the images previously downloaded and generated are stored.
From there we can retrieve them to upload into s3. 

The csv file contains already in the image key the realitve paths of all the images.
The synthetic images are named with large_{class_difficulty}+their original id . 

"""
import cv2
import numpy as np
import shutil
import json
from PIL import Image
import io
import pandas as pd 
from pathlib import Path
import csv

fieldnames = ["image_key", "size_category", "difficulty", "file_size_kb"]


def generate_single_large_image(source_path, output_path, target_bytes, tolerance=0.1):
    """
    Generate an image with approximately target_bytes file size.
    
    Strategy:
    1. Start with upscaled version of source
    2. Add noise to increase complexity
    3. Adjust JPEG quality to hit target size
    4. Use iterative refinement via binary search to check if compressed is same as target
    """
    
    # Read source image
    src_img = cv2.imread(str(source_path))
    if src_img is None:
        raise ValueError(f"Cannot read {source_path}")
    
    h, w = src_img.shape[:2]
    
    # METHOD 1: Determine required resolution
    # A high-quality JPEG typically compresses at ~2-5:1 ratio
    # So for target_bytes, we need roughly target_bytes * compression_ratio bytes raw
    # We'll overshoot and then adjust quality
    
    # Calculate how much we need to scale up
    raw_size_bytes = h * w * 3  # 3 channels RGB
    target_raw_bytes = target_bytes * 3  # Assume 3:1 compression for high quality
    
    scale_factor = np.sqrt(target_raw_bytes / raw_size_bytes)
    scale_factor = max(1.0, min(scale_factor, 8.0))  # Cap at 8x to avoid ridiculous sizes
    
    # Upscale the image
    new_w = int(w * scale_factor)
    new_h = int(h * scale_factor)
    
    if scale_factor > 1.0:
        img = cv2.resize(src_img, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
    else:
        img = src_img.copy()
    
    # METHOD 2: Add controlled noise to increase complexity
    # This makes the image harder to compress, increasing file size
    noise_level = 15  
    noise = np.random.normal(0, noise_level, img.shape).astype(np.int16)
    img_noisy = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    
    # Blend original and noisy (keep 70% original structure)
    img = cv2.addWeighted(img, 0.7, img_noisy, 0.3, 0)
    
    # METHOD 3: Add high-frequency texture patterns in random locations
    # This further increases compression difficulty
    for _ in range(5):  # Add 5 texture patches
        patch_size = np.random.randint(new_w // 8, new_w // 4)
        x = np.random.randint(0, max(1, new_w - patch_size))
        y = np.random.randint(0, max(1, new_h - patch_size))
        
        # Create high-frequency pattern
        texture = np.random.randint(0, 255, (patch_size, patch_size, 3), dtype=np.uint8)
        # Apply high-pass filter to make it more complex
        texture = cv2.GaussianBlur(texture, (3, 3), 0)
        texture = cv2.addWeighted(np.random.randint(0, 255, texture.shape, dtype=np.uint8), 
                                   0.8, texture, 0.2, 0)
        
        # Overlay texture
        img[y:y+patch_size, x:x+patch_size] = cv2.addWeighted(
            img[y:y+patch_size, x:x+patch_size], 0.6, texture, 0.4, 0
        )
    
    # METHOD 4: Binary search for the right JPEG quality
    quality_low, quality_high = 50, 100
    best_quality = 95
    
    for _ in range(8):  # 8 iterations of binary search
        quality = (quality_low + quality_high) // 2
        
        # Encode with current quality
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
        _, buffer = cv2.imencode('.jpg', img, encode_params)
        current_size = len(buffer)
        
        if abs(current_size - target_bytes) / target_bytes < tolerance:
            best_quality = quality
            break
        elif current_size < target_bytes:
            quality_low = quality + 1
        else:
            quality_high = quality - 1
            best_quality = quality
    
    # Save with best quality found
    encode_params = [cv2.IMWRITE_JPEG_QUALITY, best_quality]
    _, buffer = cv2.imencode('.jpg', img, encode_params)
    
    with open(output_path, 'wb') as f:
        f.write(buffer)
    
    actual_size_mb = len(buffer) / (1024 * 1024)
    
    # Add debug info
    print(f"    Resolution: {new_w}x{new_h}, Quality: {best_quality}, "
          f"Scale: {scale_factor:.1f}x, Target: {target_bytes/(1024*1024):.1f}MB")
    
    return actual_size_mb


def generate_large_images_by_difficulty(
    metadata_csv,          # path to images_metadata.csv
    source_base_dir,       # base dir containing size/medium/images/
    output_dir,            # where to save generated images
    target_sizes_mb=[10, 15,25],   # generate multiple sizes per source ( better to use larger than required since it doesent generate extremely prcise)
    images_per_difficulty=10
):
    """
    For each difficulty (easy, medium, hard), pick up to
    'images_per_difficulty' medium-sized images, then upscale them
    to create large versions. Saves them in the correct folders
    and appends rows to the metadata CSV.
    """
    # 1. Load metadata
    df = pd.read_csv(metadata_csv)

    # Filter medium images with known difficulty (exclude 'unknown')
    medium_df = df[(df['size_category'] == 'medium') & 
                   (df['difficulty'].isin(['easy','medium','hard']))]

    if medium_df.empty:
        print("❌ No medium images with known difficulty found in metadata!")
        return

    print(f"Found {len(medium_df)} medium images with difficulty labels")

    # 2. Group by difficulty
    grouped = medium_df.groupby('difficulty')

    # Prepare output directories
    out_base = Path(output_dir)
    large_img_dir = out_base / "size" / "large" / "images"
    large_img_dir.mkdir(parents=True, exist_ok=True)

    for difficulty, group in grouped:
        # Select up to N images
        selected = group.sample(n=min(images_per_difficulty, len(group)), random_state=42)

        print(f"\n🔹 Difficulty '{difficulty}': using {len(selected)} source images")

        for _, row in selected.iterrows():
            # Build source path: source_base_dir/size/medium/images/filename
            src_filename = Path(row['image_key']).name  # e.g., "000123.jpg"
            src_path = Path(source_base_dir) / "size" / "medium" / "images" / src_filename

            if not src_path.exists():
                print(f"   ⚠️ Source missing: {src_path}")
                continue

            # Generate large versions for each target size
            for target_mb in target_sizes_mb:
                target_bytes = target_mb * 1024 * 1024
                output_filename = f"large_{difficulty}_{src_path.stem}_{target_mb}MB.jpg"
                output_path = large_img_dir / output_filename

                actual_mb = generate_single_large_image(
                    src_path, output_path, target_bytes
                )

                # Also copy to difficulty folder
                diff_img_dir = out_base / "difficulty" / difficulty / "images"
                diff_img_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(output_path, diff_img_dir / output_filename)

                # Append to CSV
                with open(metadata_csv, 'a', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writerow({
                        "image_key": f"test-images/{output_filename}",
                        "size_category": "large",
                        "difficulty": difficulty,
                        "file_size_kb": round(actual_mb * 1024, 2)
                    })

                print(f"   ✅ {output_filename}: {actual_mb:.1f} MB")


# ----- UPDATE __main__ -----
if __name__ == "__main__":
    source_directory = "Datav2"
    output_directory = "Datav2/generated_dataset"

    # This CSV already exists from your previous processing
    metadata_csv = Path(source_directory) / "images_metadata.csv"
    if not metadata_csv.exists():
        print("❌ Metadata CSV not found! Run the VOC processing first.")
        exit()

    # Create output directory structure (optional)
    for size_cat in ["small", "medium", "large"]:
        for diff_cat in ["easy", "medium", "hard"]:
            (Path(output_directory) / "size" / size_cat / "images").mkdir(parents=True, exist_ok=True)
            (Path(output_directory) / "difficulty" / diff_cat / "images").mkdir(parents=True, exist_ok=True)

    # Generate large images from medium sources, 10 per difficulty, with sizes 8,10,15 MB
    generate_large_images_by_difficulty(
        metadata_csv=metadata_csv,
        source_base_dir=source_directory,   # where 'size/medium/images/' lives
        output_dir=output_directory,
        target_sizes_mb=[8, 15, 20],        # adjust as needed
        images_per_difficulty=10
    )

    # Final summary
    print("\n📊 FINAL DISTRIBUTION:")
    for size_cat in ["small", "medium", "large"]:
        img_dir = Path(output_directory) / "size" / size_cat / "images"
        if img_dir.exists():
            files = list(img_dir.glob("*.jpg"))
            sizes = [f.stat().st_size / (1024*1024) for f in files]
            if sizes:
                print(f"\n{size_cat.upper()}:")
                print(f"  Count: {len(files)}")
                print(f"  Size range: {min(sizes):.1f}MB - {max(sizes):.1f}MB")
                print(f"  Average: {sum(sizes)/len(sizes):.1f}MB")


def unify_images_to_flat_dir(base_dir, unified_dir_name="all_test_images"):
    """
    Copy all images from size/*/images/ folders into one flat directory.
    Update the CSV so image_key points to this unified location.
    """
    base = Path(base_dir)
    unified = base / unified_dir_name
    unified.mkdir(exist_ok=True)

    csv_path = base / "images_metadata.csv"
    df = pd.read_csv(csv_path)

    new_keys = []
    for idx, row in df.iterrows():
        # Determine current file location
        size_cat = row["size_category"]
        filename = Path(row["image_key"]).name   # e.g. 000123.jpg
        src = base / "size" / size_cat / "images" / filename

        # For generated large images, they might be in generated_dataset
        if not src.exists():
            # Try the generated large folder
            src = base / "generated_dataset" / "size" / "large" / "images" / filename
        if not src.exists():
            print(f"⚠️ Missing image: {filename}")
            continue

        # Copy to unified folder (skip if already there)
        dest = unified / filename
        if not dest.exists():
            shutil.copy2(src, dest)

        # Update image_key to point to unified folder
        new_keys.append(f"{unified_dir_name}/{filename}")

    df["image_key"] = new_keys
    df.to_csv(csv_path, index=False)
    print(f"✅ Unified {len(new_keys)} images into {unified}")

unify_images_to_flat_dir("Datav2", "all_test_images")




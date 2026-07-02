""" 
Here only Coco Data are donwloaded and classified according to difficulty.
The data are then stored in Datav2 inside a folder size with images divided in large,medium,small. 
From Coco dataset no large images are present but then with image_generatorV2 they will be generated. 
 
Then a csv for recap is written with image_key,size_category,difficulty,file_size_kb.
This csv is implemented for later artillery use for load testing. 

The image path is currently not relevant,
but it then gets created when all images are merged in a big directory with image_generatorV2
"""

import csv
import shutil
from pathlib import Path
import fiftyone as fo
import fiftyone.zoo as foz
from ultralytics.utils import TQDM  


yaml = {
    "names": {
        0: "aeroplane", 1: "bicycle", 2: "bird", 3: "boat", 4: "bottle",
        5: "bus", 6: "car", 7: "cat", 8: "chair", 9: "cow",
        10: "diningtable", 11: "dog", 12: "horse", 13: "motorbike",
        14: "person", 15: "pottedplant", 16: "sheep", 17: "sofa",
        18: "train", 19: "tvmonitor"
    }
}
class_names = list(yaml["names"].values())   

def get_difficulty_from_sample(sample, class_names):
    total_objects = 0
    small_object_count = 0

    if sample.ground_truth is not None:
        for det in sample.ground_truth.detections:
            label = det.label.replace(" ", "") # remove space for comparison with model labels
            if class_names is not None and label not in class_names:
                continue

            total_objects += 1

            if det.bounding_box is not None:
                _, _, bw, bh = det.bounding_box   # bw and bh are the relative width/height wrp to image ( 0-1 value)
                if bw * bh < 0.03:                # less than 3% of image area
                    small_object_count += 1

    # Logica per difficoltà:
    # difficile: +8 oggetti, +3 oggetti piccoli ( meno 3% immagine)
    # medio: 3 oggetti o 1 piccolo 

    if total_objects >= 8 or small_object_count >= 3:
        return "hard"
    elif total_objects >= 3 or small_object_count >= 1:
        return "medium"
    else:
        return "easy"

## File size classification
# <350:small
# 350-5mb medium
# >5mb large (non ce ne sono dai dataset ma il generator li genera poi)
def get_size_category(file_path):
    size_kb = file_path.stat().st_size / 1024
    if size_kb < 350:
        return "small", size_kb
    elif 350 <= size_kb < 5000:
        return "medium", size_kb
    else:
        return "large", size_kb

# ---------- Main ----------
def main():
    base_dir = Path("Datav2")
    base_dir.mkdir(exist_ok=True, parents=True)

    # load COCO validation set
    print("Loading COCO 2017 validation set (this may take a while the first time)...")
    dataset = foz.load_zoo_dataset(
        "coco-2017",
        split="validation",
        label_types=["detections"],        
        max_samples=None,                  
        dataset_name="coco-val"
    )

    #  output directories and CSV
    for cat in ["small", "medium", "large"]:
        (base_dir / "size" / cat / "images").mkdir(parents=True, exist_ok=True)

    csv_path = base_dir / "images_metadata.csv"
    fieldnames = ["image_key", "size_category", "difficulty", "file_size_kb"]

    with open(csv_path, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        # 3. Process each COCO sample
        for sample in TQDM(dataset, desc="Processing COCO"):
            file_path = Path(sample.filepath)

            # size category
            size_cat, size_kb = get_size_category(file_path)

            # difficulty 
            difficulty = get_difficulty_from_sample(sample, class_names)

            # Copy image to the appropriate size folder
            target_dir = base_dir / "size" / size_cat / "images"
            dest_path = target_dir / file_path.name
            if not dest_path.exists():
                shutil.copy2(file_path, dest_path)

            # Write row to CSV
            writer.writerow({
                "image_key": f"all_images/{file_path.name}",   ### this relative path to the jpeg image is currently not present, is created when running the image_generatorV2
                "size_category": size_cat,
                "difficulty": difficulty,
                "file_size_kb": round(size_kb, 2)
            })

    print(f" Done! CSV saved to {csv_path}")
    print(f"Images organised into {base_dir / 'size'}/")

if __name__ == "__main__":
    main()
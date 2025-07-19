#!/usr/bin/env python3
import os, re, sys, json, time, shlex, shutil, argparse, subprocess
from urllib.parse import urljoin

DEFAULT_SOURCE_PATH = 'source'
DEFAULT_OUTPUT_PATH = 'out'
DEFAULT_IMAGEMAGICK_PATH = 'magick'
DEFAULT_BASE_URL = 'http://localhost/'

IMAGE_FILENAME_MAGIC_CONSTANT = '$$KR_'
IMAGE_SUPPORTED_FORMATS = ('.jpg', '.jpeg', '.png', '.bmp', '.webp', '.avif', '.tif', '.tiff', '.tga')
IMAGE_OUTPUT_FORMAT = 'webp'
IMAGE_OUTPUT_MAX_RES = 512
IMAGE_OUTPUT_QUALITY = 85
IMAGE_MAGICK_COMMAND = f'-resize {IMAGE_OUTPUT_MAX_RES}x{IMAGE_OUTPUT_MAX_RES}\\> -quality {IMAGE_OUTPUT_QUALITY}'

def main():
    parser = argparse.ArgumentParser(description="Prepare source image files for the Image Pool Compiler.")
    parser.add_argument('--source-path', type=str, default=DEFAULT_SOURCE_PATH, help='Path to the source directory (default: "src")')
    parser.add_argument('--output-path', type=str, default=DEFAULT_OUTPUT_PATH, help='Path to the output directory (default: "out")')
    parser.add_argument('--base-url', type=str, default=DEFAULT_BASE_URL, help='Base URL (default: "http://localhost/")')
    parser.add_argument('--magick', type=str, default=DEFAULT_IMAGEMAGICK_PATH, help='Direct path for ImageMagick\'s `magick` executable (default: tries global "magick")')
    args = parser.parse_args()
    
    # we've parsed the arguments. time to set up the rest
    src_path = args.source_path
    output_path = args.output_path
    base_url = args.base_url
    magick_path = args.magick

    # check if imagemagick exists
    if shutil.which(magick_path) is None:
        print("Error: ImageMagick not found.")
        print("Please install ImageMagick, or set the path to `magick` using --magick.")
        print("Note: If you're on Linux/Unix, try setting `--magick=convert` instead.")
        sys.exit(1)

    manifest_version = int(time.time())

    # intialize base object for the manifest
    manifest = {
        "version": int(manifest_version),
        "pool": {
            "base_url": str(base_url),
            "categories": [],
            "total_length": 0,
            "urls": [],
            "attributes": [],
        }
    }

    # check if the output path exists, if not create it
    if not os.path.exists(output_path):
        os.makedirs(output_path)

    # Get all category folders. We do some pattern matching to only get folders
    # that match the naming convention of 'XX - Category Name'
    category_folders = [
        folder for folder in os.listdir(src_path)
        if os.path.isdir(os.path.join(src_path, folder)) and re.match(r'^\d{2}\s*-\s*\w+', folder)
    ]
    category_folders.sort() # sort by first digits

    # process each category
    cumulative_length = 0 # cumulative for all items across all categories
    for i, category_folder in enumerate(category_folders):
        category_start_index = cumulative_length
        category_length = 0
        
        src_category_path = os.path.join(src_path, category_folder)

        # split the category folder name: "XX - Category Name"
        _split = category_folder.split(' - ', 1)
        category_number = int(_split[0])
        category_name = str(_split[1])

        out_category_path = os.path.join(output_path, f"{category_number:02x}")

        print("\033[96m" + 
            f"Processing category {i + 1} of {len(category_folders)}: " +
            f"[{category_number:02d}] {category_name}" +
            "\033[0m")
        
        # create output category folder
        try:
            os.makedirs(out_category_path, exist_ok=True)
        except Exception as e:
            print("\033[91m[ERROR]\033[0m", end=' ')
            print("Failed to create output category folder. Skipping.")
            print(f"'{out_category_path}': {e}")
            continue
        
        src_image_files = [
            f for f in os.listdir(src_category_path)
            if f.startswith(IMAGE_FILENAME_MAGIC_CONSTANT)
                and f.lower().endswith(IMAGE_SUPPORTED_FORMATS)
                and os.path.isfile(os.path.join(src_category_path, f))
        ]

        # process each image in the category
        for j, src_image in enumerate(src_image_files):
            if j >= 65536:
                print("\033[91m[ERROR]\033[0m", end=" ")
                print("Max images per category reached (65536)! Finishing category.")
                break

            # remove file extension, then tokenize by underscore
            _parts = src_image.split('.')[0].split('_')

            img_cat = int(_parts[1], 16) # hex string -> dec integer
            img_id = str(_parts[2])
            img_attrib = int(_parts[3], 16) # hex string -> dec integer

            # sanity checks. if these fail, skip this item!
            # check if any tokens are missing
            if img_cat is None or img_id is None or img_attrib is None:
                print("\033[91m[ERROR]\033[0m", end=' ')
                print("One or more tokens are empty or invalid! Skipping.")
                print(f"  img: {src_image}, cat: {img_cat}, id: {img_id}, attrib: {img_attrib}")
                continue

            # Check if category number matches
            if img_cat != category_number:
                print("\033[91m[ERROR]\033[0m", end=' ')
                print("Category mismatch! Skipping.")
                print(f"  cat: {category_number}, img: {src_image}, img cat: {img_cat}")
                continue

            # Check if image ID is at least 4 characters long.
            # Note: normally the IDs are 12 characters long, but we're being liberal here,
            # since these values *could* change in the future. We just need them to be at
            # *least* 4 characters long, otherwise the output folder structure could break.
            if len(img_id) < 4:
                print("\033[91m[ERROR]\033[0m", end=' ')
                print("Invalid _src_id! Must be at least 4 characters. Skipping.")
                print(f"  img: {src_image}, id: {img_id}")
                continue

            # Check if image ID is alphanumeric (a-z, A-Z, 0-9)
            if not re.fullmatch(r'[a-zA-Z0-9]+', img_id):
                print("\033[91m[ERROR]\033[0m", end=' ')
                print("Invalid _src_id! Must be alphanumeric (a-z, A-Z, 0-9). Skipping.")
                print(f"  img: {src_image}, id: {img_id}")
                continue

            # Check if _src_attrib is a valid 8-bit unsigned integer
            if not (0 <= img_attrib <= 255):
                print("\033[91m[ERROR]\033[0m", end=' ')
                print(f"Invalid attribute flags! Must be a valid 8-bit uint (0->255). Skipping.")
                print(f"  img: {src_image}, attrib: {img_attrib}")
                continue

            # Okay, we're ready to process the image!
            _out_subfolder = str(img_id[:2])
            _out_file = str(img_id[2:]) + "." + IMAGE_OUTPUT_FORMAT

            src_image_path = os.path.join(src_category_path, src_image)
            out_image_dir = os.path.join(out_category_path, _out_subfolder)
            out_image_path = os.path.join(out_image_dir, _out_file)

            # try to create the output image directory
            try:
                os.makedirs(out_image_dir, exist_ok=True)
            except Exception as e:
                print("\033[91m[ERROR]\033[0m", end=' ')
                print(f"Failed to create output image directory '{out_image_dir}': {e}")
                continue

            print(f"[{i}:{j}] {src_image} -> {out_image_path}", end="\t")
            
            cmd = f'{magick_path} {shlex.quote(src_image_path)} {IMAGE_MAGICK_COMMAND} {shlex.quote(out_image_path)}'

            result = subprocess.run(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            if result.returncode != 0:
                print("\033[91m[ERROR]\033[0m")
                print("Image processing command failed!")
                print(f"  error: {result.stderr}")
                continue

            img_url = urljoin(base_url, os.path.relpath(out_image_path, output_path))

            print("\033[92m[OK]\033[0m")
            manifest["pool"]["urls"].append(img_url)
            manifest["pool"]["attributes"].append(img_attrib)

            category_length += 1
        cumulative_length += category_length

        # add category metadata object
        manifest["pool"]["categories"].append({
            "name": str(category_name),
            "index": int(category_start_index),
            "length": int(category_length)
        })

        print("\033[92m" + 
            f"Finished processing category! " +
            f"{category_length} images inside category [{category_number:02d}] {category_name}" + 
            "\033[0m\n")
    
    # add total length of items to manifest
    manifest["pool"]["total_length"] = int(cumulative_length)

    fail = False

    # final sanity checks
    if len(manifest["pool"]["urls"]) != int(manifest["pool"]["total_length"]):
        print("\033[91m[ERROR]\033[0m Mismatch between total length and number of URLs in manifest!")
        print(f"  total_length: {manifest['pool']['total_length']}, urls: {len(manifest['pool']['urls'])}")
        fail = True
    
    if len(manifest["pool"]["urls"]) != len(manifest["pool"]["attributes"]):
        print("\033[91m[ERROR]\033[0m Mismatch between number of attributes and number of URLs in manifest!")
        print(f"  urls: {len(manifest['pool']['urls'])}, attributes: {len(manifest['pool']['attributes'])}")
        fail = True

    # write metadata manifest file
    with open(os.path.join(output_path, 'meta.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)

    if fail:
        print("Image pool compiled, but there were some errors (see above).")
        print("Please make sure to check the output, or try again with different parameters.")
    else:
        print("Image pool compiled successfully! It is now ready to be committed.")

if __name__ == "__main__":
    main()
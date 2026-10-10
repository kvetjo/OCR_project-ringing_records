import os
import boto3
from pypdf import PdfReader, PdfWriter
from pdf2image import convert_from_path

# Cesta k Poppleru
POPPLER_PATH = r"C:\Birdlife_projects\poppler-25.11.0\Library\bin" 

# ==============================
# USER SETTINGS / CONFIGURATION
# ==============================

# Input settings (single_file OR folder)
INPUT_MODE = "folder" 
INPUT_FOLDER_PATH = r"C:\Users\jonas.kvet\Desktop\storm_petrels"
INPUT_FILE_PATH = r"C:\Users\jonas.kvet\Desktop\shearwaters_for_testing\Cory_Shearwaters_merged.pdf"

# Output settings
OUTPUT_DESTINATION = "local"  # "local" or "r2"
OUTPUT_FORMAT = "jpeg"      # "pdf" or "jpeg" ("jpg")
OUTPUT_BASE_FOLDER = r"C:\Users\jonas.kvet\Desktop\storm_petrels\output"
KEEP_LOCAL_FILES = True    # True = ponechat soubory, False = smazat po uploadu na R2

# Processing settings
PAGES_PER_PDF = 1          # How many pages per one PDF file (usually 2 as those are scans of front and back page)
FILES_PER_BATCH = 100       # How many files (PDF or JPEG) in one batch
START_BATCH_NUMBER = 10 

#SIMAR
#batch_13 - batch_17 = 2015
#batch_18 - batch_22 = 2016
#batch_23 - batch_27 = 2017
#batch_28 - batch_35 = 2019
#batch_36 - batch_40 = 2020

#GHADIRA
#batch_41 - batch_47 = 2019
#batch_48 - batch_xx = 2020


# ==============================
# CLOUDFLARE R2 CONFIGURATION
# ==============================
R2_ENDPOINT_URL = ""
R2_ACCESS_KEY_ID = ""
R2_SECRET_ACCESS_KEY = ""
R2_BUCKET_NAME = ""

# NOVÉ NASTAVENÍ: Cílová složka v rámci R2 bucketu. 
# Pro nahrání přímo do rootu nechte prázdné: ""
# Pro nahrání do subsložky zadejte cestu, např.: "Ghadira/2020" nebo "Simar"
R2_TARGET_FOLDER = "ringing" 

#----------------------------
# FREE TIER = 10GB / month
#----------------------------
MAX_R2_STORAGE_GB = 9
MAX_R2_STORAGE_BYTES = MAX_R2_STORAGE_GB * 1024 * 1024 * 1024

def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )

def check_r2_storage_limit():
    s3 = get_r2_client()
    paginator = s3.get_paginator("list_objects_v2")
    total_size = 0
    for page in paginator.paginate(Bucket=R2_BUCKET_NAME):
        if "Contents" in page:
            for obj in page["Contents"]:
                total_size += obj["Size"]
    
    used_gb = total_size / (1024 ** 3)
    print(f"Cloudflare R2 used storage: {used_gb:.2f} GB")
    if total_size >= MAX_R2_STORAGE_BYTES:
        raise RuntimeError(f"UPLOAD CANCELLED: R2 limit reached ({used_gb:.2f} GB)")

def upload_to_r2(local_file_path, batch_name, file_name):
    s3 = get_r2_client()
    
    # NOVÁ LOGIKA PRO CÍLOVOU SLOŽKU
    if R2_TARGET_FOLDER:
        # Odstraníme lomítka na začátku a konci, aby nevznikly nechtěné lomítka navíc
        folder_prefix = R2_TARGET_FOLDER.strip("/")
        r2_key = f"{folder_prefix}/{batch_name}/{file_name}"
    else:
        r2_key = f"{batch_name}/{file_name}"
        
    content_type = "application/pdf" if file_name.lower().endswith(".pdf") else "image/jpeg"
    
    s3.upload_file(
        Filename=local_file_path,
        Bucket=R2_BUCKET_NAME,
        Key=r2_key,
        ExtraArgs={"ContentType": content_type}
    )
    print(f"Uploaded to R2: {r2_key}")

def get_pdf_files():
    pdf_files = []
    if INPUT_MODE == "single_file":
        # Check if it's a valid local path, skipping file:/// checks if needed or parsing it
        clean_path = INPUT_FILE_PATH.replace("file:///", "").replace("%20", " ")
        if os.path.exists(clean_path) and clean_path.lower().endswith(".pdf"):
            pdf_files.append(clean_path)
    elif INPUT_MODE == "folder":
        for root, _, files in os.walk(INPUT_FOLDER_PATH):
            for file in files:
                if file.lower().endswith(".pdf"):
                    pdf_files.append(os.path.join(root, file))
    return sorted(pdf_files)

def main():
    if OUTPUT_DESTINATION == "r2":
        check_r2_storage_limit()

    pdf_files = get_pdf_files()
    if not pdf_files:
        print("No files found.")
        return

    failed_files = []

    current_batch_number = START_BATCH_NUMBER
    files_in_current_batch = 0
    file_counter = 1 

    if not os.path.exists(OUTPUT_BASE_FOLDER):
        os.makedirs(OUTPUT_BASE_FOLDER)

    for pdf_path in pdf_files:
        print(f"\nProcessing: {pdf_path}")
        
        # --- LOGIKA PRO JPEG ---
        if OUTPUT_FORMAT.lower() == "jpeg" or OUTPUT_FORMAT.lower() =="jpg":
            try:
                images = convert_from_path(pdf_path, poppler_path=POPPLER_PATH)
            except Exception as e:
                print(f"ERROR: Cannot process file {pdf_path}. Skipping.")
                # Add the problematic file to our list
                failed_files.append(pdf_path)
                continue
            for img in images:
                if files_in_current_batch >= FILES_PER_BATCH:
                    current_batch_number += 1
                    files_in_current_batch = 0
                    file_counter = 1

                batch_name = f"batch_{current_batch_number}"
                batch_folder = os.path.join(OUTPUT_BASE_FOLDER, batch_name)
                os.makedirs(batch_folder, exist_ok=True)

                filename = f"page_{file_counter}.jpg"
                filepath = os.path.join(batch_folder, filename)
                img.save(filepath, "JPEG")

                if OUTPUT_DESTINATION == "r2":
                    upload_to_r2(filepath, batch_name, filename)
                    if not KEEP_LOCAL_FILES:
                        os.remove(filepath)

                files_in_current_batch += 1
                file_counter += 1

        # --- LOGIKA PRO PDF ---
        # --- LOGIKA PRO PDF ---
        else:
            try:
                reader = PdfReader(pdf_path)
                total_pages = len(reader.pages)
            except Exception as e:
                print(f"ERROR: Cannot read file {pdf_path}. Skipping.")
                # Add the problematic file to our list
                failed_files.append(pdf_path)
                continue
            
            for i in range(0, total_pages, PAGES_PER_PDF):
                if files_in_current_batch >= FILES_PER_BATCH:
                    current_batch_number += 1
                    files_in_current_batch = 0
                    file_counter = 1

                batch_name = f"batch_{current_batch_number}"
                batch_folder = os.path.join(OUTPUT_BASE_FOLDER, batch_name)
                os.makedirs(batch_folder, exist_ok=True)

                writer = PdfWriter()
                # Přidá definovaný počet stránek (nebo zbytek)
                for page_offset in range(PAGES_PER_PDF):
                    if i + page_offset < total_pages:
                        writer.add_page(reader.pages[i + page_offset])

                filename = f"page_{file_counter}.pdf"
                filepath = os.path.join(batch_folder, filename)

                with open(filepath, "wb") as out_f:
                    writer.write(out_f)

                if OUTPUT_DESTINATION == "r2":
                    upload_to_r2(filepath, batch_name, filename)
                    if not KEEP_LOCAL_FILES:
                        os.remove(filepath)

                files_in_current_batch += 1
                file_counter += 1

    print("\n✅ Done.")
    # Print summary of failed files at the very end
    if failed_files:
        print("\n" + "="*50)
        print("⚠️  WARNING: THE FOLLOWING FILES FAILED TO PROCESS:")
        print("="*50)
        for failed_file in failed_files:
            print(f"- {failed_file}")
        print("="*50)

if __name__ == "__main__":
    main()
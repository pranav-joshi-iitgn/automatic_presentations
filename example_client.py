import base64
import requests
import json
import os

# --- Configuration ---
SERVER_URL = "http://10.0.60.193:8001/generate"
TYPST_FILEPATH = "presentation.typ"

# Optional: List of image files required by your presentation.typ
IMAGE_ASSETS = ["architecture.png", "logo.jpg"] 

def encode_image(filepath):
    """Reads a local image and converts it to a Base64 string."""
    with open(filepath, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def run_client():
    if not os.path.exists(TYPST_FILEPATH):
        print(f"Error: {TYPST_FILEPATH} not found.")
        return

    # 1. Read the Typst Code
    with open(TYPST_FILEPATH, "r", encoding="utf-8") as f:
        typ_code = f.read()

    # 2. Package any image assets
    images_payload = {}
    for img_path in IMAGE_ASSETS:
        if os.path.exists(img_path):
            filename = os.path.basename(img_path)
            images_payload[filename] = encode_image(img_path)
        else:
            print(f"Warning: {img_path} not found. Skipping.")

    # 3. Construct JSON Payload
    # Change 'output_type' to "segments" if you want the zip file instead
    payload = {
        "typ_code": typ_code,
        "images": images_payload,
        "output_type": "full" 
    }

    print("Sending job to the rendering server. This may take a moment...")
    
    # 4. Fire the Request
    response = requests.post(SERVER_URL, json=payload)

    if response.status_code == 200:
        # Check what the server sent back
        content_type = response.headers.get("Content-Type", "")
        
        if "zip" in content_type:
            output_filename = "downloaded_segments.zip"
        else:
            output_filename = "downloaded_presentation.mp4"
            
        with open(output_filename, "wb") as f:
            f.write(response.content)
            
        print(f"✅ Success! File downloaded and saved as: {output_filename}")
    else:
        print(f"❌ Server Error {response.status_code}: {response.text}")

if __name__ == "__main__":
    run_client()
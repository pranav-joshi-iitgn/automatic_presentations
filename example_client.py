import base64
import requests
import os

# --- Configuration ---
SERVER_URL = "http://10.0.60.193:8001/generate"

# High-definition placeholder images from Picsum
IMAGE_URL_1 = "https://picsum.photos/seed/slide1/1280/720"
IMAGE_URL_2 = "https://picsum.photos/seed/slide2/1280/720"

# A completely new presentation defined dynamically in the script
TYPST_CODE = """
#set page(width: 1920pt, height: 1080pt, margin: 80pt)
#set text(size: 60pt, font: "sans-serif", fill: rgb("ffffff"))
#set page(fill: rgb("1a1a2e"))

/* NOTE 1: Welcome to our dynamic test presentation. We are testing the automatic image downloading and segment streaming mode. */
#align(center + horizon)[
  = Automated Slide Deck Testing
  Test Presentation
]

#pagebreak()

/* NOTE 2: Here is the first sample image we downloaded from the internet. It is rendered seamlessly by Typst. */
#align(center + horizon)[
  = Sample Image 1
  #v(40pt)
  // Reduced width to 60% to prevent vertical page overflow
  #image("image1.jpg", width: 60%) 
]

#pagebreak()

/* NOTE 3: And here is the second image. Because we requested the segments mode, this presentation will be downloaded as a zip archive containing individual video clips. */
#align(center + horizon)[
  = Sample Image 2
  #v(40pt)
  // Reduced width to 60% to prevent vertical page overflow
  #image("image2.jpg", width: 60%) 
]
"""

def download_and_encode(url, filename):
    """Downloads an image from the web, saves it locally for inspection, and returns it as Base64."""
    print(f"Downloading {filename} from {url}...")
    response = requests.get(url)
    response.raise_for_status()
    
    # Save it locally so you can verify what was downloaded
    with open(filename, 'wb') as f:
        f.write(response.content)
        
    return base64.b64encode(response.content).decode('utf-8')

def run_client():
    print("Preparing presentation assets...")
    
    # 1. Download and encode images on the fly
    b64_img1 = download_and_encode(IMAGE_URL_1, "image1.jpg")
    b64_img2 = download_and_encode(IMAGE_URL_2, "image2.jpg")

    # 2. Construct JSON Payload
    payload = {
        "typ_code": TYPST_CODE,
        "images": {
            "image1.jpg": b64_img1,
            "image2.jpg": b64_img2
        },
        "output_type": "segments"  # Testing the Zip archive mode
    }

    print("\nSending job to the rendering server. This may take a moment...")
    
    # 3. Fire the Request
    response = requests.post(SERVER_URL, json=payload)

    if response.status_code == 200:
        content_type = response.headers.get("Content-Type", "")
        
        # Verify the server actually honored the 'segments' request
        if "zip" in content_type:
            output_filename = "downloaded_segments.zip"
        else:
            output_filename = "downloaded_presentation.mp4"
            print("Warning: Server returned an MP4 instead of a ZIP file.")
            
        with open(output_filename, "wb") as f:
            f.write(response.content)
            
        print(f"✅ Success! Server processed the assets. File downloaded and saved as: {output_filename}")
    else:
        print(f"❌ Server Error {response.status_code}: {response.text}")

if __name__ == "__main__":
    run_client()
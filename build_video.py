import os
import re
import requests
import subprocess
import shutil

# --- Configuration ---
TYPST_FILE = "presentation.typ"
BUILD_DIR = "build"
FINAL_OUTPUT = "final_presentation.mp4"
TTS_URL = "http://10.0.60.193:8000/generate"
TTS_DESC = "Divya's voice is monotone yet slightly fast in delivery, with a very close recording that almost has no background noise."

def cleanup():
    print(f"[DEBUG] Wiping and creating clean build directory: '{BUILD_DIR}'")
    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
    os.makedirs(BUILD_DIR)

def extract_numbered_notes(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    matches = re.findall(r'/\*\s*NOTE\s*(\d+):\s*(.*?)\s*\*/', content, re.DOTALL)
    notes_dict = {int(num_str): text.strip() for num_str, text in matches}
    print(f"[DEBUG] Found {len(notes_dict)} numbered notes.")
    return notes_dict

def clean_and_chunk_text(text):
    if not text: return []
    sanitized = text.replace("?", ".").replace("!", ".").replace(",", "")
    chunks = [c.strip() for c in re.split(r'\s*\.\s*', sanitized) if c.strip()]
    return chunks

def generate_slide_images():
    print("[DEBUG] Compiling presentation slides via Typst...")
    subprocess.run(["typst", "compile", TYPST_FILE, f"{BUILD_DIR}/slide_{{p}}.png"], check=True)

def create_silent_audio(filepath, duration=3):
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", 
        "-t", str(duration), "-acodec", "pcm_s16le", filepath
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def clean_text_for_prosody(text):
    """
    Sanitizes text without breaking it into tiny pieces.
    Replaces problem characters with engine-safe equivalents that preserve human flow.
    """
    if not text: 
        return ""
    
    # Replace question marks with an ellipsis (...) 
    # This tricks LLM-based TTS engines into making a natural, curious pause 
    # instead of hitting the "end-of-generation" token crash.
    sanitized = text.replace("?", "...").replace("!", ".")
    
    # Keep commas! They give the engine structural cues for natural breathing pauses.
    return sanitized.strip()

def generate_audio_for_slides(notes_dict, total_slides):
    print("[DEBUG] Generating high-prosody audio assets from TTS server...")
    for slide_num in range(1, total_slides + 1):
        final_audio_filename = f"audio_{slide_num}.wav"
        final_audio_path = os.path.join(BUILD_DIR, final_audio_filename)
        
        note = notes_dict.get(slide_num, "")
        
        if note:
            # Clean the text but keep it as ONE single, natural paragraph
            safe_paragraph = clean_text_for_prosody(note)
            print(f"[DEBUG] Slide {slide_num} Full Prompt: \"{safe_paragraph}\"")
            
            payload = {
                "prompt": safe_paragraph, 
                "description": TTS_DESC
            }
            try:
                # Send the entire note in a single API call so the model inflects naturally
                response = requests.post(TTS_URL, json=payload, timeout=45)
                response.raise_for_status()
                with open(final_audio_path, 'wb') as f:
                    f.write(response.content)
                print(f"  ✓ Generated natural audio for slide {slide_num}")
            except Exception as e:
                print(f"  [ERROR] TTS failed on slide {slide_num}: {e}")
                create_silent_audio(final_audio_path, duration=3)
        else:
            create_silent_audio(final_audio_path, duration=3)

def get_audio_duration(filepath):
    """Uses ffprobe to capture the precise float duration of an audio track."""
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", filepath
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())

def stitch_video_single_pass(total_slides):
    print("\n🎬 Commencing Single-Pass Video Assembly via Filter Complex...")
    
    cmd = ["ffmpeg", "-y"]
    filter_inputs = ""
    
    for i in range(1, total_slides + 1):
        img_path = os.path.join(BUILD_DIR, f"slide_{i}.png")
        audio_path = os.path.join(BUILD_DIR, f"audio_{i}.wav")
        
        # Get the strict duration of this slide's audio
        duration = get_audio_duration(audio_path)
        print(f"  -> Slide {i}: Audio length is exactly {duration:.3f} seconds.")
        
        # Lock the video stream generation of this image to the exact audio duration
        cmd.extend(["-loop", "1", "-t", str(duration), "-i", img_path])
        cmd.extend(["-i", audio_path])
        
        # Calculate indices for the filter complex string
        img_idx = 2 * (i - 1)
        aud_idx = img_idx + 1
        filter_inputs += f"[{img_idx}:v][{aud_idx}:a]"
        
    # Build the linear concat instruction sequence
    filter_complex_str = f"{filter_inputs} concat=n={total_slides}:v=1:a=1 [v][a]"
    
    cmd.extend([
        "-filter_complex", filter_complex_str,
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-r", "24",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        FINAL_OUTPUT
    ])
    
    print("[DEBUG] Running master FFmpeg compilation pipeline...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    
    if res.returncode == 0:
        print(f"\n✅ SUCCESS! The master video timeline is synchronized: {FINAL_OUTPUT}")
    else:
        print(f"\n[ERROR] Master layout engine failed:\n{res.stderr}")

if __name__ == "__main__":
    cleanup()
    generate_slide_images()
    
    slide_images = [f for f in os.listdir(BUILD_DIR) if f.startswith("slide_") and f.endswith(".png")]
    total_slides = len(slide_images)
    
    notes_dict = extract_numbered_notes(TYPST_FILE)
    generate_audio_for_slides(notes_dict, total_slides)
    
    stitch_video_single_pass(total_slides)
import os
import re
import subprocess
import shutil
import warnings
import io
import sys
from contextlib import redirect_stdout, redirect_stderr

# --- Suppress ALL Standard ML Warning Junk ---
warnings.filterwarnings("ignore")
os.environ["TRANSFORMERS_VERBOSITY"] = "error"

import torch
import gc
import soundfile as sf
from parler_tts import ParlerTTSForConditionalGeneration
from transformers import AutoTokenizer, logging

logging.set_verbosity_error()

# --- Configuration ---
TYPST_FILE = "presentation.typ"
BUILD_DIR = "build"
SEGMENTS_DIR = os.path.join(BUILD_DIR, "segments")
FINAL_OUTPUT = "final_presentation.mp4"
TTS_DESC = "Divya's voice is monotone yet slightly fast in delivery, with a very close recording that almost has no background noise."

# Environment tweak to handle fragmented VRAM spaces alongside Ollama
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

def cleanup():
    print(f"[DEBUG] Wiping and creating clean build directories...")
    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
    os.makedirs(BUILD_DIR)
    os.makedirs(SEGMENTS_DIR)

def extract_numbered_notes(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    matches = re.findall(r'/\*\s*NOTE\s*(\d+):\s*(.*?)\s*\*/', content, re.DOTALL)
    notes_dict = {int(num_str): text.strip() for num_str, text in matches}
    print(f"[DEBUG] Found {len(notes_dict)} numbered notes.")
    return notes_dict

def clean_text_for_prosody(text):
    if not text: 
        return ""
    sanitized = text.replace("?", "...").replace("!", ".")
    return sanitized.strip()

def generate_slide_images():
    print("[DEBUG] Compiling presentation slides via Typst...")
    subprocess.run(["typst", "compile", TYPST_FILE, f"{BUILD_DIR}/slide_{{p}}.png"], check=True)

def create_silent_audio(filepath, duration=3):
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", 
        "-t", str(duration), "-acodec", "pcm_s16le", filepath
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def generate_audio_for_slides(notes_dict, total_slides):
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"\n🤖 Loading Indic Parler-TTS locally onto device: {device}...")
    
    try:
        # CHANGED: Forcefully trap and silence Parler's hardcoded config prints
        trap = io.StringIO()
        with redirect_stdout(trap), redirect_stderr(trap):
            model = ParlerTTSForConditionalGeneration.from_pretrained(
                "ai4bharat/indic-parler-tts", 
                torch_dtype=torch.float16
            ).to(device)
            model.eval()
            
            tokenizer = AutoTokenizer.from_pretrained("ai4bharat/indic-parler-tts")
            description_tokenizer = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path)
    except Exception as e:
        print(f"❌ [CRITICAL ERROR] Failed to load local weights: {e}")
        return

    print("[DEBUG] Generating high-prosody audio assets locally...")
    for slide_num in range(1, total_slides + 1):
        final_audio_filename = f"audio_{slide_num}.wav"
        final_audio_path = os.path.join(BUILD_DIR, final_audio_filename)
        
        note = notes_dict.get(slide_num, "")
        
        if note:
            safe_paragraph = clean_text_for_prosody(note)
            print(f"[DEBUG] Slide {slide_num} Full Prompt: \"{safe_paragraph}\"")
            
            try:
                with torch.no_grad():
                    desc_inputs = description_tokenizer(TTS_DESC, return_tensors="pt").to(device)
                    prompt_inputs = tokenizer(safe_paragraph, return_tensors="pt").to(device)

                    generation = model.generate(
                        input_ids=desc_inputs.input_ids,
                        attention_mask=desc_inputs.attention_mask,
                        prompt_input_ids=prompt_inputs.input_ids,
                        prompt_attention_mask=prompt_inputs.attention_mask
                    )
                    
                    # CHANGED: Cast the 16-bit tensor back to 32-bit so soundfile can save it cleanly
                    audio_arr = generation.cpu().numpy().squeeze().astype("float32")
                    sf.write(final_audio_path, audio_arr, model.config.sampling_rate)
                    print(f"  ✓ Generated natural audio for slide {slide_num}")
                
            except Exception as e:
                print(f"  [ERROR] Local inference engine failed on slide {slide_num}: {e}")
                create_silent_audio(final_audio_path, duration=3)
            finally:
                if 'desc_inputs' in locals(): del desc_inputs
                if 'prompt_inputs' in locals(): del prompt_inputs
                if 'generation' in locals(): del generation
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
        else:
            print(f"  - No note for slide {slide_num}. Generating default silence.")
            create_silent_audio(final_audio_path, duration=3)
            
    print("\n🧹 Batch complete. Cleaning up master model layers...")
    del model, tokenizer, description_tokenizer
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

def get_audio_duration(filepath):
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", filepath
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())

def build_segments_and_stitch_gpu(total_slides):
    print("\n🎬 Commencing GPU-Accelerated Video Assembly...")
    
    concat_list_path = os.path.join(BUILD_DIR, "concat.txt")
    
    with open(concat_list_path, "w") as concat_file:
        for i in range(1, total_slides + 1):
            img_path = os.path.join(BUILD_DIR, f"slide_{i}.png")
            audio_path = os.path.join(BUILD_DIR, f"audio_{i}.wav")
            segment_filename = f"segment_{i}.mp4"
            segment_path = os.path.join(SEGMENTS_DIR, segment_filename)
            
            duration = get_audio_duration(audio_path)
            print(f"  -> Slide {i}: Compiling {duration:.3f}s segment via NVENC...")
            
            cmd = [
                "ffmpeg", "-y", 
                "-loop", "1", "-framerate", "24", 
                "-i", img_path, 
                "-i", audio_path, 
                "-t", str(duration), 
                "-c:v", "h264_nvenc", "-preset", "slow", 
                "-pix_fmt", "yuv420p", 
                "-c:a", "aac", "-b:a", "192k", 
                segment_path
            ]
            
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                print(f"  [ERROR] GPU encoding failed for segment {i}:\n{res.stderr}")
                exit(1)
                
            concat_file.write(f"file 'segments/{segment_filename}'\n")

    print("\n[DEBUG] Assembling master timeline video file from generated segments...")
    res = subprocess.run([
        "ffmpeg", "-y", 
        "-f", "concat", "-safe", "0", 
        "-i", "concat.txt", 
        "-c", "copy", 
        os.path.abspath(FINAL_OUTPUT)
    ], cwd=BUILD_DIR, capture_output=True, text=True)
    
    if res.returncode == 0:
        print(f"✅ SUCCESS! Final presentation saved to: {FINAL_OUTPUT}")
        print(f"📁 Individual slides preserved in: {SEGMENTS_DIR}/")
    else:
        print(f"\n[ERROR] Master layout engine failed:\n{res.stderr}")

if __name__ == "__main__":
    cleanup()
    generate_slide_images()
    
    slide_images = [f for f in os.listdir(BUILD_DIR) if f.startswith("slide_") and f.endswith(".png")]
    total_slides = len(slide_images)
    
    notes_dict = extract_numbered_notes(TYPST_FILE)
    generate_audio_for_slides(notes_dict, total_slides)
    
    build_segments_and_stitch_gpu(total_slides)
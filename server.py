import os
import re
import subprocess
import shutil
import warnings
import io
import base64
import uuid
import zipfile
from threading import Lock
from contextlib import redirect_stdout, redirect_stderr

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

# --- ML Setup & Suppressions ---
warnings.filterwarnings("ignore")
os.environ["TRANSFORMERS_VERBOSITY"] = "error"
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

import torch
import gc
import soundfile as sf
from parler_tts import ParlerTTSForConditionalGeneration
from transformers import AutoTokenizer, logging

logging.set_verbosity_error()

app = FastAPI(title="Typst Presentation-to-Video Server")
gpu_lock = Lock()

TTS_DESC = "Divya's voice is monotone yet slightly fast in delivery, with a very close recording that almost has no background noise."

# --- API Data Models ---
class GenerationRequest(BaseModel):
    typ_code: str
    images: dict[str, str]  # Dictionary of { "filename.png": "base64_string" }
    output_type: str        # "full" or "segments"

def cleanup_workspace(workspace_dir: str):
    """Deletes the temporary job directory after the file is sent."""
    if os.path.exists(workspace_dir):
        print(f"[CLEANUP] Wiping temporary workspace: {workspace_dir}")
        shutil.rmtree(workspace_dir)

def clean_text_for_prosody(text):
    if not text: return ""
    return text.replace("?", "...").replace("!", ".").strip()

def get_audio_duration(filepath):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", filepath]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())

@app.post("/generate")
def generate_presentation(req: GenerationRequest, background_tasks: BackgroundTasks):
    job_id = uuid.uuid4().hex
    workspace = os.path.join(os.getcwd(), f"jobs_{job_id}")
    build_dir = os.path.join(workspace, "build")
    segments_dir = os.path.join(build_dir, "segments")
    
    os.makedirs(workspace, exist_ok=True)
    os.makedirs(build_dir, exist_ok=True)
    os.makedirs(segments_dir, exist_ok=True)
    
    typst_file = os.path.join(workspace, "presentation.typ")
    final_output = os.path.join(workspace, "final_presentation.mp4")
    zip_output = os.path.join(workspace, "segments.zip")
    
    try:
        # 1. Unpack Payload into Workspace
        with open(typst_file, "w", encoding="utf-8") as f:
            f.write(req.typ_code)
            
        for img_name, b64_data in req.images.items():
            img_path = os.path.join(workspace, img_name)
            with open(img_path, "wb") as f:
                f.write(base64.b64decode(b64_data))
                
        # 2. Extract Notes & Compile Slides
        with open(typst_file, 'r', encoding='utf-8') as f:
            content = f.read()
        matches = re.findall(r'/\*\s*NOTE\s*(\d+):\s*(.*?)\s*\*/', content, re.DOTALL)
        notes_dict = {int(num_str): text.strip() for num_str, text in matches}
        
        print(f"[{job_id}] Compiling Typst slides...")
        subprocess.run(["typst", "compile", "presentation.typ", "build/slide_{p}.png"], 
                       cwd=workspace, check=True)
        
        total_slides = len([f for f in os.listdir(build_dir) if f.startswith("slide_") and f.endswith(".png")])
        
        # 3. Generate Audio (Protected by GPU Lock)
        with gpu_lock:
            print(f"[{job_id}] 🔒 GPU Locked. Generating TTS...")
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
            
            trap = io.StringIO()
            with redirect_stdout(trap), redirect_stderr(trap):
                model = ParlerTTSForConditionalGeneration.from_pretrained("ai4bharat/indic-parler-tts", torch_dtype=torch.float16).to(device)
                model.eval()
                tokenizer = AutoTokenizer.from_pretrained("ai4bharat/indic-parler-tts")
                desc_tokenizer = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path)

            for i in range(1, total_slides + 1):
                audio_path = os.path.join(build_dir, f"audio_{i}.wav")
                note = clean_text_for_prosody(notes_dict.get(i, ""))
                
                if note:
                    with torch.no_grad():
                        desc_inputs = desc_tokenizer(TTS_DESC, return_tensors="pt").to(device)
                        prompt_inputs = tokenizer(note, return_tensors="pt").to(device)
                        generation = model.generate(
                            input_ids=desc_inputs.input_ids, attention_mask=desc_inputs.attention_mask,
                            prompt_input_ids=prompt_inputs.input_ids, prompt_attention_mask=prompt_inputs.attention_mask
                        )
                        audio_arr = generation.cpu().numpy().squeeze().astype("float32")
                        sf.write(audio_path, audio_arr, model.config.sampling_rate)
                else:
                    # Generate 3 seconds of silence
                    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "3", "-acodec", "pcm_s16le", audio_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            del model, tokenizer, desc_tokenizer
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            print(f"[{job_id}] 🔓 GPU Unlocked. Yielding VRAM back to Ollama.")

        # 4. Render Segments using NVENC
        print(f"[{job_id}] Encoding GPU segments...")
        concat_list_path = os.path.join(build_dir, "concat.txt")
        with open(concat_list_path, "w") as concat_file:
            for i in range(1, total_slides + 1):
                img_path = os.path.join(build_dir, f"slide_{i}.png")
                audio_path = os.path.join(build_dir, f"audio_{i}.wav")
                segment_filename = f"segment_{i}.mp4"
                segment_path = os.path.join(segments_dir, segment_filename)
                duration = get_audio_duration(audio_path)
                
                subprocess.run([
                    "ffmpeg", "-y", "-loop", "1", "-framerate", "24", 
                    "-i", img_path, "-i", audio_path, "-t", str(duration), 
                    "-c:v", "h264_nvenc", "-preset", "slow", "-pix_fmt", "yuv420p", 
                    "-c:a", "aac", "-b:a", "192k", segment_path
                ], check=True, capture_output=True)
                concat_file.write(f"file 'segments/{segment_filename}'\n")

        # 5. Return requested output
        background_tasks.add_task(cleanup_workspace, workspace)
        
        if req.output_type == "segments":
            print(f"[{job_id}] Packaging segments zip...")
            with zipfile.ZipFile(zip_output, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(segments_dir):
                    for file in files:
                        zipf.write(os.path.join(root, file), file)
            return FileResponse(zip_output, media_type="application/zip", filename="segments.zip")
            
        else: # "full"
            print(f"[{job_id}] Stitching master video...")
            subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", 
                "-i", "concat.txt", "-c", "copy", final_output
            ], cwd=build_dir, check=True, capture_output=True)
            return FileResponse(final_output, media_type="video/mp4", filename="presentation.mp4")

    except Exception as e:
        cleanup_workspace(workspace)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="10.0.60.193", port=8001)
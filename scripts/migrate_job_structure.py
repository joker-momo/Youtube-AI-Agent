# scripts/migrate_job_structure.py
import shutil
from pathlib import Path

def migrate_job(job_dir: Path):
    if not (job_dir / "job.json").exists():
        return
        
    print(f"Migrating job: {job_dir.name}...")
    
    # Tạo thư mục con
    json_dir = job_dir / "json"
    outputs_dir = job_dir / "outputs"
    json_dir.mkdir(exist_ok=True)
    outputs_dir.mkdir(exist_ok=True)
    
    # Danh sách các tệp json cần di chuyển
    json_files = [
        "seo.json", "scenes.json", "script.json", "whisper_timestamps.json",
        "render_props.json", "render_progress.json", "approvals.json",
        "idea.json", "research.json", "persona_eval.json", "audio_qa.json",
        "review.json", "tts_report.json", "visual_review.json", "assets_manifest.json"
    ]
    
    # Danh sách các tệp outputs cần di chuyển
    output_files = [
        "video.mp4", "thumbnail.jpg", "visual_contact_sheet.jpg", 
        "report.md", "operator_review.html"
    ]
    
    # Di chuyển JSON files
    for filename in json_files:
        p = job_dir / filename
        if p.exists():
            shutil.move(str(p), str(json_dir / filename))
            print(f"  Moved JSON: {filename} -> json/")
            
    # Di chuyển Output files
    for filename in output_files:
        p = job_dir / filename
        if p.exists():
            shutil.move(str(p), str(outputs_dir / filename))
            print(f"  Moved Output: {filename} -> outputs/")
            
    # Di chuyển các biến thể thumbnail (thumbnail_1.jpg, thumbnail_2.jpg, ...)
    for p in job_dir.glob("thumbnail_*.jpg"):
        shutil.move(str(p), str(outputs_dir / p.name))
        print(f"  Moved Output: {p.name} -> outputs/")
        
    # Đồng bộ sang remotion/public/ nếu có
    public_dir = Path(__file__).resolve().parents[1] / "remotion" / "public" / "jobs" / job_dir.name
    if public_dir.exists():
        public_outputs = public_dir / "outputs"
        public_outputs.mkdir(exist_ok=True)
        # Di chuyển các ảnh static thumbnail của Remotion
        for p in public_dir.glob("thumbnail*"):
            if p.is_file():
                shutil.move(str(p), str(public_outputs / p.name))
                print(f"  Moved Remotion Static: {p.name} -> outputs/")

if __name__ == "__main__":
    jobs_root = Path(__file__).resolve().parents[1] / "jobs"
    if jobs_root.exists():
        for job_path in jobs_root.iterdir():
            if job_path.is_dir() and job_path.name.startswith("job-"):
                migrate_job(job_path)
    else:
        print("Jobs directory not found.")

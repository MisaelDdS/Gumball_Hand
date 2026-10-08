"""Rebuild assets/gumball_sprites.zip from gumball/sprites_v2 (or the folder given as argument).
Keeps only what the pet reads: PNG frames inside the animation folders + manifest.json + eyes.json
(no GIF previews, contact sheets or source art). Run after new sprites arrive, then commit + push:
  .venv\\Scripts\\python.exe pack_sprites.py [sprites_folder]"""
import sys
import zipfile
from pathlib import Path

here = Path(__file__).resolve().parent
src = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "gumball" / "sprites_v2"
out = here / "assets" / "gumball_sprites.zip"
out.parent.mkdir(exist_ok=True)
keep = [p for p in sorted(src.rglob("*")) if p.is_file() and "source_art" not in p.parts
        and (p.suffix == ".json" and p.parent == src or p.suffix == ".png" and p.parent != src)]
if not any(p.name == "manifest.json" for p in keep):
    sys.exit(f"No manifest.json in {src}")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in keep:
        z.write(p, Path("sprites_v2") / p.relative_to(src))
print(f"{len(keep)} files -> {out} ({out.stat().st_size / 1e6:.1f} MB)")

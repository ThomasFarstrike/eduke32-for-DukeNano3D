#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import shutil
import subprocess

def run(cmd, cwd, check=True):
    print(f"[run] {cmd} (cwd={cwd})")
    subprocess.run(cmd, cwd=cwd, check=check)

def find_tool(script_dir: Path, tool_name: str) -> Path:
    candidate = script_dir / tool_name
    if candidate.exists() and os.access(candidate, os.X_OK):
        return candidate
    which = shutil.which(tool_name)
    if which:
        return Path(which)
    raise FileNotFoundError(f"Required tool '{tool_name}' not found in {script_dir} or PATH")

def collect_files(temp_dir: Path, patterns):
    files = []
    for pattern in patterns:
        files.extend(sorted(temp_dir.glob(pattern)))
    return files

def main():
    parser = argparse.ArgumentParser(description="Re-package Duke Nukem 3D GRP with PNG tiles and duke3d.def")
    parser.add_argument("grpfile", help="Path to .grp file to compact")
    parser.add_argument("--temp-dir", default="temp_folder", help="Temporary working directory")
    parser.add_argument("--output", default="newfile.grp", help="Output GRP filename")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temporary working directory")
    parser.add_argument(
        "--quicktest",
        action="store_true",
        help="Process only tiles000.art into PNGs/duke3d.def and keep remaining ART files in the output GRP",
    )
    args = parser.parse_args()

    work_dir = Path.cwd().resolve()
    script_dir = Path(__file__).resolve().parent
    grp_path = (work_dir / args.grpfile).resolve()
    if not grp_path.exists():
        print(f"Input GRP not found: {grp_path}")
        return 1

    temp_dir = (work_dir / args.temp_dir).resolve()
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)

    kextract = find_tool(script_dir, "kextract")
    kgroup = find_tool(script_dir, "kgroup")
    arttool = find_tool(script_dir, "arttool")
    convert = shutil.which("convert")
    if not convert:
        raise FileNotFoundError("Required tool 'convert' (ImageMagick) not found in PATH")

    # Step 1: extract GRP into temp_dir
    run([str(kextract), str(grp_path), "*"], cwd=temp_dir)

    # arttool expects lowercase tilesXXX.art filenames
    if args.quicktest:
        first_art_upper = temp_dir / "TILES000.ART"
        first_art_lower = temp_dir / "tiles000.art"
        if first_art_upper.exists():
            first_art_upper.rename(first_art_lower)
    else:
        for art_file in temp_dir.glob("TILES*.ART"):
            art_file.rename(temp_dir / art_file.name.lower())

    # normalize palette file casing for runtime lookup
    palette_upper = temp_dir / "PALETTE.DAT"
    palette_lower = temp_dir / "palette.dat"
    if palette_upper.exists() and not palette_lower.exists():
        palette_upper.rename(palette_lower)

    # Step 2: extract tiles*.art, convert to PNG, build duke3d.def
    duke_def_path = temp_dir / "duke3d.def"
    if duke_def_path.exists():
        duke_def_path.unlink()

    with duke_def_path.open("w", encoding="utf-8") as duke_def:
        if args.quicktest:
            first_art = temp_dir / "tiles000.art"
            art_files = [first_art] if first_art.exists() else []
        else:
            art_files = sorted(temp_dir.glob("tiles*.art"))
        for art_file in art_files:
            name = art_file.stem  # TILES000
            digits = "".join(ch for ch in name if ch.isdigit())
            tile_index = int(digits) if digits else 0

            for tile_nr in range(256):
                global_tile = tile_index * 256 + tile_nr
                global_padded = f"{global_tile:04d}"

                run([str(arttool), "exporttile", str(global_tile)], cwd=temp_dir, check=False)
                local_pcx = temp_dir / f"tile{global_padded}.pcx"
                if not local_pcx.exists():
                    continue

                out_png = temp_dir / f"TILE{global_padded}.PNG"
                run([
                    convert,
                    str(local_pcx),
                    "-alpha", "on",
                    "-transparent", "#FC00FC",
                    "-strip",
                    "-define", "png:compression-level=9",
                    "-define", "png:compression-strategy=1",
                    "-define", "png:exclude-chunks=date,time",
                    "-colors", "256",
                    f"PNG8:{out_png}",
                ], cwd=temp_dir)

                duke_def.write(f"tilefromtexture {global_tile} {{ file TILE{global_padded}.PNG }}\n")

    # Step 3: repack GRP without ART files
    patterns = [
        "*.VOC", "*.voc",
        "*.PNG", "*.png",
        "*.CON", "*.con",
        "*.DAT", "*.dat",
        "*.BIN", "*.bin",
        "*.MAP", "*.map",
        "duke3d.def",
        "*.MID", "*.mid",
    ]
    files = collect_files(temp_dir, patterns)

    if args.quicktest:
        files.extend(sorted(temp_dir.glob("TILES*.ART")))
        files.extend(sorted(temp_dir.glob("tiles*.art")))
        files = [f for f in files if f.name != "tiles000.art"]

    # de-duplicate while preserving order
    files = list(dict.fromkeys(files))

    if not files:
        print("No files found to pack.")
        return 1

    output_path = (work_dir / args.output).resolve()
    run([str(kgroup), str(output_path)] + [str(p) for p in files], cwd=temp_dir)

    if not args.keep_temp:
        shutil.rmtree(temp_dir)

    print(f"Created: {output_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

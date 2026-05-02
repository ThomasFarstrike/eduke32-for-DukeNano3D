#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import shutil
import re
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

def get_tile_raw_size(arttool: Path, cwd: Path, tile_num: int):
    proc = subprocess.run(
        [str(arttool), "info", str(tile_num)],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    match = re.search(rf"Tile\s+{tile_num}:\s+(\d+)x(\d+)", output)
    if not match:
        return None
    width = int(match.group(1))
    height = int(match.group(2))
    return width * height


def parse_tilefiles_arg(value: str):
    parts = [p.strip() for p in value.split(",") if p.strip()]
    if not parts:
        raise argparse.ArgumentTypeError("Expected comma-separated tile file indices, e.g. 0,1,2")

    indices = []
    for part in parts:
        if not part.isdigit():
            raise argparse.ArgumentTypeError(
                f"Invalid tile file index '{part}'. Expected non-negative integers like 0,1,2"
            )
        indices.append(int(part))

    return sorted(set(indices))


def main():
    parser = argparse.ArgumentParser(description="Re-package Duke Nukem 3D GRP with PNG tiles and duke3d.def")
    parser.add_argument("grpfile", help="Path to .grp file to compact")
    parser.add_argument("--temp-dir", default="temp_folder", help="Temporary working directory")
    parser.add_argument("--output", default="newfile.grp", help="Output GRP filename")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temporary working directory")
    parser.add_argument(
        "--tilefilestopng",
        "--tilesfilestopng",
        "--tilestopng",
        type=parse_tilefiles_arg,
        help="Only convert specific TILESXXX.ART files to PNG (comma-separated indices, e.g. 0,1,2). Untouched ART files stay in output.",
    )
    parser.add_argument(
        "--onlysmaller",
        action="store_true",
        help="Only replace tiles when PNG is smaller than raw tile data; keeps ART files in output",
    )
    args = parser.parse_args()

    selected_tile_files = set(args.tilefilestopng or [])

    work_dir = Path.cwd().resolve()
    script_dir = Path(__file__).resolve().parent
    grp_path = (work_dir / args.grpfile).resolve()
    if not grp_path.exists():
        print(f"Input GRP not found: {grp_path}")
        return 1

    temp_dir = (work_dir / args.temp_dir).resolve()

    # Always remove default temp_folder as requested, then remove selected temp dir.
    default_temp_dir = (work_dir / "temp_folder").resolve()
    if default_temp_dir.exists():
        shutil.rmtree(default_temp_dir)

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

    # arttool expects lowercase tilesXXX.art filenames for files we process.
    if selected_tile_files:
        for tile_file_index in sorted(selected_tile_files):
            selected_upper = temp_dir / f"TILES{tile_file_index:03d}.ART"
            selected_lower = temp_dir / f"tiles{tile_file_index:03d}.art"
            if selected_upper.exists() and not selected_lower.exists():
                selected_upper.rename(selected_lower)
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
        if selected_tile_files:
            art_files = [
                temp_dir / f"tiles{tile_file_index:03d}.art"
                for tile_file_index in sorted(selected_tile_files)
                if (temp_dir / f"tiles{tile_file_index:03d}.art").exists()
            ]
        else:
            art_files = sorted(temp_dir.glob("tiles*.art"))
        for art_file in art_files:
            name = art_file.stem  # TILES000
            digits = "".join(ch for ch in name if ch.isdigit())
            tile_index = int(digits) if digits else 0
            tiles_to_remove = []

            for tile_nr in range(256):
                global_tile = tile_index * 256 + tile_nr
                global_padded = f"{global_tile:04d}"

                local_pcx = temp_dir / f"tile{global_padded}.pcx"
                if local_pcx.exists():
                    local_pcx.unlink()

                export_proc = subprocess.run(
                    [str(arttool), "exporttile", str(global_tile)],
                    cwd=temp_dir,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                #if export_proc.returncode != 0:
                #    if local_pcx.exists():
                #        local_pcx.unlink()
                #    continue

                if not local_pcx.exists():
                    continue

                out_png = temp_dir / f"TILE{global_padded}.PNG"
                if out_png.exists():
                    out_png.unlink()

                convert_proc = subprocess.run([
                    convert,
                    str(local_pcx),
                    #"-alpha", "off",
                    "-alpha", "on",
                    "-transparent", "#FC00FC",
                    "-strip",
                    "-define", "png:compression-level=9",
                    "-define", "png:compression-strategy=1",
                    "-define", "png:exclude-chunks=date,time",
                    "-colors", "256",
                    f"PNG8:{out_png}",
                ], cwd=temp_dir, check=False, capture_output=True, text=True)

                if convert_proc.returncode != 0 or not out_png.exists():
                    print(f"[error] convert failed for tile {global_tile}; aborting")
                    if convert_proc.stdout:
                        print(convert_proc.stdout)
                    if convert_proc.stderr:
                        print(convert_proc.stderr)
                    if out_png.exists():
                        out_png.unlink()
                    return 1

                raw_size = get_tile_raw_size(arttool, temp_dir, global_tile)
                png_size = out_png.stat().st_size

                if args.onlysmaller:
                    # Only do arttool rmtile in --onlysmaller mode (requested: buggy otherwise).
                    if raw_size is not None and png_size < raw_size:
                        rm_proc = subprocess.run(
                            [str(arttool), "rmtile", str(global_tile)],
                            cwd=temp_dir,
                            check=False,
                            capture_output=True,
                            text=True,
                        )
                        if rm_proc.returncode == 0:
                            duke_def.write(f"tilefromtexture {global_tile} {{ file {out_png.name} }}\n")
                        else:
                            print(f"[warn] rmtile failed for tile {global_tile}, keeping ART tile")
                            out_png.unlink()
                    else:
                        out_png.unlink()
                else:
                    # In normal mode, keep ART as-is and override via DEF only.
                    duke_def.write(f"tilefromtexture {global_tile} {{ file {out_png.name} }}\n")

                # Keep PCX files for debugging when --keep-temp is used.
    # Step 3: repack GRP. ART files are included for --onlysmaller or selective tile-file processing.
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
    if args.onlysmaller or selected_tile_files:
        patterns.extend(["*.ART", "*.art"])

    files = collect_files(temp_dir, patterns)

    if selected_tile_files and not args.onlysmaller:
        selected_processed = {f"tiles{idx:03d}.art" for idx in selected_tile_files}
        selected_processed.update({f"TILES{idx:03d}.ART" for idx in selected_tile_files})
        files = [f for f in files if f.name not in selected_processed]

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

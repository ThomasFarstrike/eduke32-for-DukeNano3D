#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import shutil
import re
import subprocess
import sys

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


def find_file_case_insensitive(base_dir: Path, name: str):
    normalized = name.lower()

    direct = base_dir / name
    if direct.exists() and direct.is_file():
        return direct

    for candidate in base_dir.iterdir():
        if candidate.is_file() and candidate.name.lower() == normalized:
            return candidate

    return None


def parse_used_tiles_from_mapinfo_output(output: str):
    section_match = re.search(r"=== COMBINED TILE USAGE ===(.*?)(?:\n=== |\Z)", output, flags=re.DOTALL)
    section = section_match.group(1) if section_match else output

    lines = section.splitlines()
    used_tiles = set()
    in_tiles = False

    for line in lines:
        if not in_tiles:
            if re.search(r"used_tiles\s*\(\d+\)\s*:", line):
                in_tiles = True
                _, _, tail = line.partition(":")
                used_tiles.update(int(v) for v in re.findall(r"\b\d+\b", tail))
            continue

        if line.strip() == "":
            continue

        # End when indentation style changes (next label/section).
        if not line.startswith(" "):
            break

        used_tiles.update(int(v) for v in re.findall(r"\b\d+\b", line))

    return used_tiles


def tilefile_index_from_name(name: str):
    match = re.match(r"^tiles(\d{3})\.art$", name, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def parse_includeart_arg(value: str):
    name = Path(value).name
    if not re.match(r"^tiles\d{3}\.art$", name, flags=re.IGNORECASE):
        raise argparse.ArgumentTypeError(
            f"Invalid ART filename '{value}'. Expected TILESNNN.ART, e.g. TILES012.ART"
        )
    return name.lower()


def normalize_case_insensitive_options(argv, option_names):
    normalized = []
    lower_opts = {opt.lower(): opt for opt in option_names}

    for arg in argv:
        if not arg.startswith("--"):
            normalized.append(arg)
            continue

        key, sep, value = arg.partition("=")
        canonical = lower_opts.get(key.lower())
        if canonical:
            normalized.append(f"{canonical}{sep}{value}" if sep else canonical)
        else:
            normalized.append(arg)

    return normalized


def build_ultra_minimal_menu_allowlist():
    """
    Tile allowlist derived from EDuke32 menu/precache code paths:
      - source/duke3d/src/premap.cpp: cacheDukeTiles()
      - source/duke3d/src/menus.cpp: direct rotatesprite_fs/menu references

    Intentionally includes only menu/UI-critical ranges and explicit menu screens,
    not full gameplay precache ranges.
    """
    allow = set()

    # premap.cpp cacheDukeTiles()
    allow.update(range(2456, 2491))   # MENUSCREEN .. DUKECAR-1
    allow.update(range(2822, 2916))   # STARTALPHANUM .. ENDALPHANUM
    allow.update(range(2929, 3022))   # BIGALPHANUM-11 .. BIGALPHANUM+81
    allow.update(range(3072, 3165))   # MINIFONT .. MINIFONT+92

    # menus.cpp explicit menu/background draws
    allow.update({
        2445,             # F1HELP
        2499,             # INGAMEDUKETHREEDEE
        2503,             # PLUTOPAKSPRITE+2
        2504, 2505, 2506, # credits backgrounds (2504+cm-MENU_CREDITS)
        3240,             # BONUSSCREEN
        3280,             # TEXTSTORY
        3281,             # LOADSCREEN
    })

    return allow


def main():
    normalized_argv = normalize_case_insensitive_options(
        sys.argv[1:],
        ["--pngfolder", "--map", "--includeart", "--ultraminimalmenu"],
    )

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
    parser.add_argument(
        "--optipng",
        action="store_true",
        help="Run optipng -o7 on each generated PNG",
    )
    parser.add_argument(
        "--zopflipng",
        action="store_true",
        help="Run zopflipng with fixed high-compression settings on each generated PNG",
    )
    parser.add_argument(
        "--pngfolder",
        metavar="DIRNAME",
        help="Use pre-generated TILE####.PNG files from DIRNAME instead of exporting/converting from ART files",
    )
    parser.add_argument(
        "--map",
        metavar="MAPNAME",
        help="Limit tile processing to tiles used by MAPNAME (case-insensitive) using mapinfo",
    )
    parser.add_argument(
        "--includeart",
        metavar="FILE.ART",
        action="append",
        type=parse_includeart_arg,
        help="Force-include FILE.ART from extracted temp folder (repeatable, e.g. --includeart TILES012.ART)",
    )
    parser.add_argument(
        "--ultraminimalmenu",
        action="store_true",
        help=(
            "Include an ultra-minimal menu/UI tile allowlist derived from "
            "source/duke3d/src/premap.cpp + source/duke3d/src/menus.cpp. "
            "Useful with --map to keep startup/menu tiles without bundling full ART files."
        ),
    )

    args = parser.parse_args(normalized_argv)

    selected_tile_files = set(args.tilefilestopng or [])
    included_art_files = set(args.includeart or [])

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
    mapinfo = None
    if args.map:
        mapinfo = find_tool(script_dir, "mapinfo")
    convert = None
    if not args.pngfolder:
        convert = shutil.which("convert")
        if not convert:
            raise FileNotFoundError("Required tool 'convert' (ImageMagick) not found in PATH")

    optipng = None
    if args.optipng and not args.pngfolder:
        optipng = shutil.which("optipng")
        if not optipng:
            raise FileNotFoundError("Requested --optipng but tool 'optipng' was not found in PATH")

    zopflipng = None
    if args.zopflipng and not args.pngfolder:
        zopflipng = Path("/home/user/software/zopfli/zopflipng")
        if not (zopflipng.exists() and os.access(zopflipng, os.X_OK)):
            raise FileNotFoundError(
                "Requested --zopflipng but '/home/user/software/zopfli/zopflipng' was not found or is not executable"
            )

    if args.pngfolder and (args.optipng or args.zopflipng):
        print("[info] --pngfolder was provided: skipping --optipng/--zopflipng and using precomputed PNGs as-is")

    png_sources = {}
    if args.pngfolder:
        png_dir = Path(args.pngfolder)
        if not png_dir.is_absolute():
            png_dir = (work_dir / png_dir).resolve()

        if not png_dir.exists() or not png_dir.is_dir():
            print(f"PNG folder not found or not a directory: {png_dir}")
            return 1

        for png_file in sorted(png_dir.iterdir()):
            if not png_file.is_file():
                continue
            match = re.match(r"^tile(\d{4})\.png$", png_file.name, flags=re.IGNORECASE)
            if not match:
                continue
            tile_num = int(match.group(1))
            png_sources[tile_num] = png_file

    # Step 1: extract GRP into temp_dir
    run([str(kextract), str(grp_path), "*"], cwd=temp_dir)

    required_tiles = None
    if args.map:
        map_file = find_file_case_insensitive(temp_dir, args.map)
        if map_file is None:
            print(f"Map file not found in extracted GRP (case-insensitive): {args.map}")
            return 1

        mapinfo_proc = subprocess.run(
            [str(mapinfo), str(map_file)],
            cwd=temp_dir,
            check=False,
            capture_output=True,
            text=True,
        )
        mapinfo_output = (mapinfo_proc.stdout or "") + "\n" + (mapinfo_proc.stderr or "")
        if mapinfo_proc.returncode != 0:
            print(f"[error] mapinfo failed for map {map_file.name}; aborting")
            if mapinfo_output.strip():
                print(mapinfo_output)
            return 1

        required_tiles = parse_used_tiles_from_mapinfo_output(mapinfo_output)
        print(f"[info] --map {map_file.name}: restricting to {len(required_tiles)} used tiles")

    if args.ultraminimalmenu:
        menu_allow_tiles = build_ultra_minimal_menu_allowlist()
        if required_tiles is None:
            required_tiles = set()
        required_tiles.update(menu_allow_tiles)
        print(
            f"[info] --ultraminimalmenu: added {len(menu_allow_tiles)} "
            f"menu/precache tiles; total required tiles now {len(required_tiles)}"
        )

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
                if required_tiles is not None and global_tile not in required_tiles:
                    continue
                global_padded = f"{global_tile:04d}"

                local_pcx = temp_dir / f"tile{global_padded}.pcx"
                if local_pcx.exists():
                    local_pcx.unlink()

                out_png = temp_dir / f"TILE{global_padded}.PNG"
                if out_png.exists():
                    out_png.unlink()

                if args.pngfolder:
                    source_png = png_sources.get(global_tile)
                    if not source_png:
                        continue
                    shutil.copy2(source_png, out_png)
                else:
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

                if args.optipng and not args.pngfolder:
                    optipng_proc = subprocess.run(
                        [optipng, "-o7", str(out_png)],
                        cwd=temp_dir,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    if optipng_proc.returncode != 0:
                        print(f"[error] optipng failed for tile {global_tile}; aborting")
                        if optipng_proc.stdout:
                            print(optipng_proc.stdout)
                        if optipng_proc.stderr:
                            print(optipng_proc.stderr)
                        return 1

                if args.zopflipng and not args.pngfolder:
                    zopflipng_proc = subprocess.run(
                        [
                            str(zopflipng),
                            "--iterations=500",
                            "--filters=01234mepb",
                            "--lossy_8bit",
                            "--lossy_transparent",
                            "-y",
                            str(out_png),
                            str(out_png),
                        ],
                        cwd=temp_dir,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    if zopflipng_proc.returncode != 0:
                        print(f"[error] zopflipng failed for tile {global_tile}; aborting")
                        if zopflipng_proc.stdout:
                            print(zopflipng_proc.stdout)
                        if zopflipng_proc.stderr:
                            print(zopflipng_proc.stderr)
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
    if args.onlysmaller or selected_tile_files or included_art_files:
        patterns.extend(["*.ART", "*.art"])

    files = collect_files(temp_dir, patterns)

    if included_art_files:
        files = [
            f for f in files
            if f.suffix.lower() != ".art"
            or f.name.lower() in included_art_files
        ]

    if required_tiles is not None:
        needed_art_indices = {tile // 256 for tile in required_tiles}
        files = [
            f for f in files
            if f.suffix.lower() != ".art"
            or tilefile_index_from_name(f.name) in needed_art_indices
            or f.name.lower() in included_art_files
        ]

    if selected_tile_files and not args.onlysmaller:
        selected_processed = {f"tiles{idx:03d}.art" for idx in selected_tile_files}
        selected_processed.update({f"TILES{idx:03d}.ART" for idx in selected_tile_files})
        files = [
            f for f in files
            if f.name not in selected_processed
            or f.name.lower() in included_art_files
        ]

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

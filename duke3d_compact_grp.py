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


def get_tile_anim_info(arttool: Path, cwd: Path, tile_num: int):
    proc = subprocess.run(
        [str(arttool), "info", str(tile_num)],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")

    match_type = re.search(r"AnimType:\s*(\d+)", output)
    match_frames = re.search(r"AnimFrames:\s*(\d+)", output)
    match_speed = re.search(r"AnimSpeed:\s*(\d+)", output)

    anim_type = int(match_type.group(1)) if match_type else 0
    anim_frames = int(match_frames.group(1)) if match_frames else 0
    anim_speed = int(match_speed.group(1)) if match_speed else 0

    if anim_frames <= 0 or anim_type == 0:
        first_tile = tile_num
        last_tile = tile_num
    elif anim_type == 3:  # PICANM_ANIMTYPE_BACK
        first_tile = tile_num - anim_frames
        last_tile = tile_num
    else:
        # PICANM_ANIMTYPE_OSC / PICANM_ANIMTYPE_FWD
        first_tile = tile_num
        last_tile = tile_num + anim_frames

    return {
        "type": anim_type,
        "frames": anim_frames,
        "speed": anim_speed,
        "first": first_tile,
        "last": last_tile,
    }


def get_tile_anim_range(arttool: Path, cwd: Path, tile_num: int):
    info = get_tile_anim_info(arttool, cwd, tile_num)
    return info["first"], info["last"]


def expand_required_tiles_with_animation_frames(arttool: Path, cwd: Path, required_tiles):
    expanded = set(required_tiles)

    for tile in list(required_tiles):
        first_tile, last_tile = get_tile_anim_range(arttool, cwd, tile)
        if first_tile > last_tile:
            first_tile, last_tile = last_tile, first_tile

        for anim_tile in range(first_tile, last_tile + 1):
            if anim_tile >= 0:
                expanded.add(anim_tile)

    return expanded


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


def parse_tile_numbers_arg(value: str):
    parts = [p.strip() for p in value.split(",") if p.strip()]
    if not parts:
        raise argparse.ArgumentTypeError("Expected comma-separated tile numbers, e.g. 1289,1290,407")

    numbers = []
    for part in parts:
        if not part.isdigit():
            raise argparse.ArgumentTypeError(
                f"Invalid tile number '{part}'. Expected non-negative integers like 1289,1290"
            )
        numbers.append(int(part))

    return sorted(set(numbers))


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
    Runtime baseline tile allowlist for `--map` builds.

    Source of truth is EDuke32 startup/hud/precache code paths, primarily:
      - source/duke3d/src/premap.cpp: cacheDukeTiles()
      - source/duke3d/src/screens.cpp / menus.cpp: crosshair + menu screens

    Goal: keep the set minimal while still including non-map runtime essentials
    (HUD weapon tiles, projectiles/effects, fonts/menu screens, crosshair).
    """
    allow = set()

    # ---------- Menu and font/UI infrastructure ----------
    allow.update(range(2456, 2491))   # MENUSCREEN .. DUKECAR-1
    allow.update(range(2813, 2820))   # SPINNINGNUKEICON .. SPINNINGNUKEICON+6
    allow.update({2820, 2821})        # BIGFNTCURSOR / SMALLFNTCURSOR
    allow.update(range(2822, 2916))   # STARTALPHANUM .. ENDALPHANUM
    allow.update(range(2929, 3022))   # BIGALPHANUM-11 .. BIGALPHANUM+81
    allow.update(range(3072, 3165))   # MINIFONT .. MINIFONT+92

    # Explicit menu screens drawn outside broad menu ranges.
    allow.update({
        2445,             # F1HELP
        2499,             # INGAMEDUKETHREEDEE
        2503,             # PLUTOPAKSPRITE+2
        2504, 2505, 2506, # credits backgrounds (2504+cm-MENU_CREDITS)
        3240,             # BONUSSCREEN
        3280,             # TEXTSTORY
        3281,             # LOADSCREEN
    })

    # ---------- Player HUD and first-person weapon tiles ----------
    allow.add(2523)                   # CROSSHAIR

    # Pistol (requested missing range): FIRSTGUN .. FIRSTGUN+6
    allow.update(range(2524, 2531))   # includes FIRSTGUN and reload sequence

    # Pistol ejected shells
    allow.update(range(2533, 2535))   # SHELL .. SHELL+1

    # Chaingun view weapon tiles
    allow.update(range(2536, 2544))   # CHAINGUN .. CHAINGUN+7

    # RPG view weapon tiles
    allow.update(range(2544, 2547))   # RPGGUN .. RPGGUN+2

    # Freeze cannon view weapon tiles
    allow.update(range(2548, 2554))   # FREEZE .. FREEZE+5

    # Shrinker/Expander view weapon + crystal frames
    allow.update(range(2554, 2562))   # SHRINKER-2 .. SHRINKER+5

    # Tripbomb/remote hand sequences
    allow.update(range(2563, 2568))   # HANDHOLDINGLASER .. HANDHOLDINGLASER+4
    allow.update(range(2570, 2576))   # HANDREMOTE .. HANDREMOTE+5

    # Shotgun view weapon tiles
    allow.update(range(2613, 2620))   # SHOTGUN .. SHOTGUN+6

    # Devastator left/right weapon tiles
    allow.update(range(2510, 2512))   # DEVISTATOR .. DEVISTATOR+1

    # ---------- Core projectile/FX tiles preloaded by cacheDukeTiles() ----------
    allow.update(range(0, 61))        # startup baseline tiles 0..60

    allow.update(range(550, 553))     # FOOTPRINTS .. FOOTPRINTS+2
    allow.update(range(1261, 1267))   # TRANSPORTERBEAM .. +5
    allow.update(range(1360, 1381))   # COOLEXPLOSION1 .. +20

    allow.update(range(1620, 1624))   # BLOOD .. +3
    allow.add(1625)                   # FIRELASER
    allow.update(range(1641, 1644))   # FREEZEBLAST .. +2
    allow.update(range(1646, 1650))   # SHRINKSPARK .. +3
    allow.update(range(1650, 1654))   # MORTER .. +3
    allow.update(range(1656, 1660))   # SHRINKEREXPLOSION .. +3

    allow.update(range(1890, 1911))   # EXPLOSION2 .. +20

    allow.update(range(2245, 2270))   # JIBS1 .. JIBS5+4
    allow.update(range(2270, 2284))   # BURNING .. +13
    allow.update(range(2286, 2294))   # JIBS6 .. +7
    allow.update(range(2310, 2324))   # BURNING2 .. +13
    allow.update(range(2324, 2328))   # CRACKKNUCKLES .. +3
    allow.update(range(2329, 2333))   # SMALLSMOKE .. +3

    allow.update(range(2400, 2429))   # SCRAP1 .. +28
    allow.update(range(2448, 2452))   # GROWSPARK .. +3

    allow.update(range(2595, 2599))   # SHOTSPARK1 .. +3
    allow.update(range(2605, 2612))   # RPG .. RPG+6

    return allow


def expand_required_tiles_with_enemy_runtime_ranges(required_tiles):
    """
    Expand map-derived tiles with enemy runtime frame ranges.

    Why this exists:
      `mapinfo` reports tiles directly present in map sectors/walls/sprites,
      but many enemies animate through additional consecutive frames at runtime.
      EDuke32 handles this in premap.cpp::cacheTilesForSprite().

    We mirror those enemy-specific ranges here so map-compacted builds still have
    walking/attack animation frames.
    """
    expanded = set(required_tiles)

    # Trigger sets correspond to enemy entry tiles that can appear in map sprites.
    # Added ranges intentionally match cacheTilesForSprite() behavior.
    enemy_runtime_groups = [
        {
            "name": "LIZTROOP family runtime frames",
            "triggers": {1680, 1681, 1715, 1725, 1741, 1744},  # LIZTROOP* variants
            "ranges": [
                (1680, 1680 + 71),  # LIZTROOP .. LIZTROOP+71
                (1768, 1776 + 2),   # HEADJIB1 .. LEGJIB1+2
            ],
        },
        {
            "name": "NEWBEAST family runtime frames",
            "triggers": {4610, 4611},  # NEWBEAST / NEWBEASTSTAYPUT
            "ranges": [
                (4610, 4610 + 89),  # NEWBEAST .. NEWBEAST+89
            ],
        },
        {
            "name": "BOSS/SHARK runtime frames",
            "triggers": {1550, 2630, 2710, 2760},  # SHARK, BOSS1..3
            "ranges": [
                (1550, 1550 + 29),  # SHARK .. SHARK+29
                (2630, 2630 + 29),  # BOSS1 .. BOSS1+29
                (2710, 2710 + 29),  # BOSS2 .. BOSS2+29
                (2760, 2760 + 29),  # BOSS3 .. BOSS3+29
            ],
        },
        {
            "name": "OCTABRAIN/COMMANDER runtime frames",
            "triggers": {1820, 1821, 1920, 1921},  # OCTABRAIN*/COMMANDER*
            "ranges": [
                (1820, 1820 + 37),  # OCTABRAIN .. OCTABRAIN+37
                (1920, 1920 + 37),  # COMMANDER .. COMMANDER+37
            ],
        },
        {
            "name": "RECON runtime frames",
            "triggers": {1960},
            "ranges": [
                (1960, 1960 + 12),  # RECON .. RECON+12
            ],
        },
        {
            "name": "PIGCOP runtime frames",
            "triggers": {2000, 2045},  # PIGCOP / PIGCOPDIVE
            "ranges": [
                (2000, 2000 + 60),  # PIGCOP .. PIGCOP+60
            ],
        },
        {
            "name": "LIZMAN runtime frames",
            "triggers": {2120, 2150, 2160, 2165},  # LIZMAN* variants
            "ranges": [
                (2120, 2120 + 79),  # LIZMAN .. LIZMAN+79
                (2201, 2209 + 2),   # LIZMANHEAD1 .. LIZMANLEG1+2
            ],
        },
        {
            "name": "DRONE runtime frames",
            "triggers": {1880},
            "ranges": [
                (1880, 1880 + 9),   # DRONE .. DRONE+9
            ],
        },
    ]

    for group in enemy_runtime_groups:
        if not (expanded & group["triggers"]):
            continue

        for start, end in group["ranges"]:
            expanded.update(range(start, end + 1))

    return expanded


def expand_required_tiles_with_runtime_state_tiles(required_tiles):
    """
    Expand map-derived tiles with explicit runtime state-transition tiles.

    These are non-PICANM transitions where game code switches picnum to a
    different tile at runtime (not a contiguous animation declared in ART).
    """
    expanded = set(required_tiles)

    runtime_state_groups = [
        {
            "name": "Fan sprite broken states",
            "triggers": {407, 412},          # FANSPRITE / FANSHADOW
            "tiles": {411, 416},             # FANSPRITEBROKE / FANSHADOWBROKE
        },
        {
            "name": "Nuke button punch sequence",
            "triggers": {142},               # NUKEBUTTON
            "tiles": {143, 144, 145},        # NUKEBUTTON+1..+3
        },
    ]

    for group in runtime_state_groups:
        if not (expanded & group["triggers"]):
            continue
        expanded.update(group["tiles"])

    return expanded


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
            "Include a runtime baseline tile allowlist for --map builds, derived from "
            "source/duke3d/src/premap.cpp + HUD/menu draw code. "
            "Adds non-map essentials like crosshair, weapon HUD tiles and core FX/projectile tiles."
        ),
    )
    parser.add_argument(
        "--debug-tiles",
        metavar="TILE1,TILE2,...",
        type=parse_tile_numbers_arg,
        help=(
            "Print per-tile animation diagnostics after build (for PNG-only packs). "
            "Example: --debug-tiles 1289,1290,407,411,2813"
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

    # arttool expects lowercase tiles*.art names; normalize early so
    # --map animation expansion (arttool info) can resolve tile metadata.
    for art_file in temp_dir.glob("TILES*.ART"):
        art_file.rename(temp_dir / art_file.name.lower())

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

        expanded_tiles = expand_required_tiles_with_animation_frames(arttool, temp_dir, required_tiles)
        added_tiles = len(expanded_tiles) - len(required_tiles)
        if added_tiles > 0:
            required_tiles = expanded_tiles
            print(
                f"[info] --map {map_file.name}: added {added_tiles} animation-frame tiles "
                f"(total now {len(required_tiles)})"
            )

        enemy_expanded_tiles = expand_required_tiles_with_enemy_runtime_ranges(required_tiles)
        enemy_added_tiles = len(enemy_expanded_tiles) - len(required_tiles)
        if enemy_added_tiles > 0:
            required_tiles = enemy_expanded_tiles
            print(
                f"[info] --map {map_file.name}: added {enemy_added_tiles} enemy runtime-frame tiles "
                f"(total now {len(required_tiles)})"
            )

    if required_tiles is not None:
        runtime_state_tiles = expand_required_tiles_with_runtime_state_tiles(required_tiles)
        runtime_state_added = len(runtime_state_tiles) - len(required_tiles)
        if runtime_state_added > 0:
            required_tiles = runtime_state_tiles
            print(
                f"[info] --map {map_file.name}: added {runtime_state_added} runtime state-transition tiles "
                f"(total now {len(required_tiles)})"
            )

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
        written_tiles = set()
        anim_def_candidates = {}
        emitted_anim_ranges = []

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
                            written_tiles.add(global_tile)
                        else:
                            print(f"[warn] rmtile failed for tile {global_tile}, keeping ART tile")
                            out_png.unlink()
                    else:
                        out_png.unlink()
                else:
                    # In normal mode, keep ART as-is and override via DEF only.
                    duke_def.write(f"tilefromtexture {global_tile} {{ file {out_png.name} }}\n")
                    written_tiles.add(global_tile)

                if global_tile in written_tiles and global_tile not in anim_def_candidates:
                    anim_info = get_tile_anim_info(arttool, temp_dir, global_tile)
                    if anim_info["frames"] > 0 and anim_info["type"] > 0:
                        anim_def_candidates[global_tile] = anim_info

                # Keep PCX files for debugging when --keep-temp is used.

        emitted_anim_defs = 0
        skipped_anim_defs = 0
        for anchor_tile in sorted(anim_def_candidates):
            info = anim_def_candidates[anchor_tile]
            first_tile = min(info["first"], info["last"])
            last_tile = max(info["first"], info["last"])
            needed_tiles = set(range(first_tile, last_tile + 1))

            if not needed_tiles.issubset(written_tiles):
                skipped_anim_defs += 1
                continue

            range_end = info["first"] if info["type"] == 3 else info["last"]
            duke_def.write(
                f"animtilerange {anchor_tile} {range_end} {info['speed']} {info['type']}\n"
            )
            emitted_anim_defs += 1
            emitted_anim_ranges.append((anchor_tile, first_tile, last_tile, info["type"], info["speed"]))

        if emitted_anim_defs > 0 or skipped_anim_defs > 0:
            print(
                f"[info] duke3d.def: emitted {emitted_anim_defs} animtilerange entries "
                f"(skipped {skipped_anim_defs} due to incomplete frame coverage)"
            )

        if args.debug_tiles:
            print("[debug] Animation diagnostics for requested tiles:")
            for tile in args.debug_tiles:
                anim_info = get_tile_anim_info(arttool, temp_dir, tile)
                png_path = temp_dir / f"TILE{tile:04d}.PNG"
                covered_by_emitted_range = any(start <= tile <= end for _, start, end, _, _ in emitted_anim_ranges)
                print(
                    "[debug] "
                    f"tile={tile} png={'yes' if png_path.exists() else 'no'} "
                    f"tilefromtexture={'yes' if tile in written_tiles else 'no'} "
                    f"animtype={anim_info['type']} animframes={anim_info['frames']} animspeed={anim_info['speed']} "
                    f"animrange={anim_info['first']}..{anim_info['last']} "
                    f"covered_by_animtilerange={'yes' if covered_by_emitted_range else 'no'}"
                )

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

    has_art_files_in_pack = any(f.suffix.lower() == ".art" for f in files)
    if not has_art_files_in_pack and emitted_anim_defs == 0:
        print(
            "[warn] Packing without ART files and without animtilerange entries. "
            "Animated tiles may render as first frame only."
        )

    output_path = (work_dir / args.output).resolve()
    run([str(kgroup), str(output_path)] + [str(p) for p in files], cwd=temp_dir)

    if not args.keep_temp:
        shutil.rmtree(temp_dir)

    print(f"Created: {output_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

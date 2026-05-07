#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import shutil
import re
import subprocess
import sys
from collections import defaultdict

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


def _decode_art_offset(value: int) -> int:
    # ART stores offsets as signed int8. Some arttool builds print those
    # bytes as unsigned 0..255 in "info" output, so convert if needed.
    if value > 127:
        return value - 256
    return value


def get_tile_offsets(arttool: Path, cwd: Path, tile_num: int):
    proc = subprocess.run(
        [str(arttool), "info", str(tile_num)],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    match = re.search(r"Xofs:\s*(-?\d+),\s*Yofs:\s*(-?\d+)", output)
    if not match:
        return 0, 0

    raw_x = int(match.group(1))
    raw_y = int(match.group(2))
    return _decode_art_offset(raw_x), _decode_art_offset(raw_y)


def _collect_available_tile_numbers(cwd: Path):
    tile_numbers = set()

    for art_file in sorted(cwd.glob("tiles*.art")):
        idx = tilefile_index_from_name(art_file.name)
        if idx is None:
            continue
        start = idx * 256
        tile_numbers.update(range(start, start + 256))

    for art_file in sorted(cwd.glob("TILES*.ART")):
        idx = tilefile_index_from_name(art_file.name)
        if idx is None:
            continue
        start = idx * 256
        tile_numbers.update(range(start, start + 256))

    return tile_numbers


def expand_required_tiles_with_animation_frames(arttool: Path, cwd: Path, required_tiles):
    expanded = set(required_tiles)
    if not required_tiles:
        return expanded

    available_tiles = _collect_available_tile_numbers(cwd)
    if not available_tiles:
        return expanded

    parent = {tile: tile for tile in available_tiles}

    def find(tile):
        while parent[tile] != tile:
            parent[tile] = parent[parent[tile]]
            tile = parent[tile]
        return tile

    def union(a, b):
        ra = find(a)
        rb = find(b)
        if ra != rb:
            parent[rb] = ra

    tile_set = available_tiles
    for tile in sorted(tile_set):
        anim_info = get_tile_anim_info(arttool, cwd, tile)
        if anim_info["frames"] <= 0 or anim_info["type"] <= 0:
            continue

        first_tile = min(anim_info["first"], anim_info["last"])
        last_tile = max(anim_info["first"], anim_info["last"])

        for anim_tile in range(first_tile, last_tile + 1):
            if anim_tile in tile_set:
                union(tile, anim_tile)

    components = defaultdict(set)
    for tile in tile_set:
        components[find(tile)].add(tile)

    for tile in list(required_tiles):
        if tile in tile_set:
            expanded.update(components[find(tile)])
            continue

        # Fallback when a required tile lies outside discovered ART ranges.
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


def parse_map_arg(value: str):
    parts = [Path(p.strip()).name for p in value.split(",") if p.strip()]
    if not parts:
        raise argparse.ArgumentTypeError(
            "Expected comma-separated map filenames, e.g. E1L1.MAP,E1L2.MAP"
        )
    return parts


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


def parse_excludefiles_arg(value: str):
    parts = [Path(p.strip()).name for p in value.split(",") if p.strip()]
    if not parts:
        raise argparse.ArgumentTypeError(
            "Expected comma-separated filenames, e.g. TILES3280.PNG,TILES3281.PNG"
        )
    return [p.lower() for p in parts]


def strip_con_line_comment(line: str):
    return line.split("//", 1)[0].rstrip("\n")


def normalize_con_filename_token(token: str):
    token = token.strip().strip("\"'")
    token = token.rstrip(",;")
    return Path(token).name.lower()


def looks_like_mid_token(token: str):
    return normalize_con_filename_token(token).endswith(".mid")


def determine_required_mid_files_from_user_con(temp_dir: Path, selected_map_name: str):
    user_con = find_file_case_insensitive(temp_dir, "USER.CON")
    if user_con is None:
        print("[warn] USER.CON not found; keeping all music files")
        return None

    map_to_slot = {}
    music_by_volume = {}

    current_music_volume = None

    with user_con.open("r", encoding="utf-8", errors="replace") as fh:
        for line_num, raw_line in enumerate(fh, start=1):
            uncommented = strip_con_line_comment(raw_line)
            stripped = uncommented.strip()

            if not stripped:
                continue

            tokens = stripped.split()
            if not tokens:
                continue

            keyword = tokens[0].lower()

            if keyword == "definelevelname":
                current_music_volume = None
                if len(tokens) >= 4 and tokens[1].isdigit() and tokens[2].isdigit():
                    volume = int(tokens[1])
                    level = int(tokens[2])
                    map_name = normalize_con_filename_token(tokens[3])
                    map_to_slot[map_name] = (volume, level)
                else:
                    print(f"[debug] USER.CON:{line_num}: ignored malformed definelevelname: {stripped}")
                continue

            if keyword == "music":
                current_music_volume = None
                if len(tokens) >= 2 and tokens[1].isdigit():
                    current_music_volume = int(tokens[1])
                    track_tokens = [normalize_con_filename_token(t) for t in tokens[2:] if looks_like_mid_token(t)]
                    music_by_volume.setdefault(current_music_volume, []).extend(track_tokens)
                else:
                    print(f"[debug] USER.CON:{line_num}: ignored malformed music line: {stripped}")
                continue

            # Continuation support:
            #  - classic indentation-based continued music lists
            #  - non-indented lines containing only MID tokens after a music line
            if current_music_volume is not None:
                continuation_tokens = [normalize_con_filename_token(t) for t in tokens if looks_like_mid_token(t)]
                if continuation_tokens and (
                    raw_line[:1].isspace()
                    or len(continuation_tokens) == len(tokens)
                ):
                    music_by_volume.setdefault(current_music_volume, []).extend(continuation_tokens)
                    continue

            current_music_volume = None

    required = set(music_by_volume.get(0, []))

    selected_map_name_norm = normalize_con_filename_token(selected_map_name)
    slot = map_to_slot.get(selected_map_name_norm)

    print(
        f"[debug] USER.CON parse summary: maps={len(map_to_slot)} music_volumes={sorted(music_by_volume.keys())} "
        f"selected_map={selected_map_name_norm}"
    )

    if slot is None:
        sample_maps = sorted(map_to_slot.keys())[:12]
        print(
            f"[warn] Map {selected_map_name_norm} not found in USER.CON definelevelname list; "
            "including only title/end music"
        )
        if sample_maps:
            print(f"[debug] USER.CON map samples: {', '.join(sample_maps)}")
        print(f"[debug] USER.CON title/end tracks: {sorted(required)}")
        return required

    volume, level = slot

    # In USER.CON, definelevelname volumes are usually 0-based while music
    # episode lists are usually 1-based (`music 1 ...` for episode 1). Also,
    # `music 0 ...` is commonly title/end tracks and should not be used for
    # episode level mapping when a shifted episode list exists.
    direct_tracks = music_by_volume.get(volume) or []
    shifted_volume = volume + 1
    shifted_tracks = music_by_volume.get(shifted_volume) or []

    if shifted_tracks:
        volume_tracks = shifted_tracks
        resolved_music_volume = shifted_volume
    else:
        volume_tracks = direct_tracks
        resolved_music_volume = volume

    print(
        f"[debug] USER.CON music candidates for map volume {volume}: "
        f"direct(volume={volume}, tracks={len(direct_tracks)}) "
        f"shifted(volume={shifted_volume}, tracks={len(shifted_tracks)})"
    )

    print(
        f"[debug] USER.CON slot for {selected_map_name_norm}: definelevelname volume={volume} level={level} "
        f"resolved_music_volume={resolved_music_volume} track_count={len(volume_tracks or [])}"
    )

    if level < len(volume_tracks or []):
        chosen_track = volume_tracks[level]
        required.add(chosen_track)
        print(f"[debug] USER.CON selected map track: {chosen_track}")
    else:
        print(
            f"[warn] No music track for definelevelname volume {volume} "
            f"(resolved music volume {resolved_music_volume}) level {level} in USER.CON; "
            "including only title/end music"
        )

    print(f"[debug] USER.CON required MID files: {sorted(required)}")
    return required


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
        1141,             # menu background image (TILE1141.PNG)
        2445,             # F1HELP
        2499,             # INGAMEDUKETHREEDEE
        2503,             # PLUTOPAKSPRITE+2
        2504, 2505, 2506, # credits backgrounds (2504+cm-MENU_CREDITS)
        3240,             # BONUSSCREEN
        3280,             # TEXTSTORY
        3281,             # LOADSCREEN
    })

    # ---------- Startup screen overlays (easy to tweak) ----------
    # These are required for the boot/title startup sequence and its logo overlays.
    STARTUP_SCREEN_TILES = {
        2492,  # STARTUP_3D_REALMS_LOGO (TILE2492.PNG)
        2493,  # STARTUP_DUKE_NUKEM_SCREEN (TILE2493.PNG)
        2497,  # STARTUP_LOGO_OVERLAY_A (TILE2497.PNG)
        2498,  # STARTUP_LOGO_OVERLAY_B (TILE2498.PNG)
    }
    allow.update(STARTUP_SCREEN_TILES)

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
    allow.update(range(2570, 2577))   # HANDREMOTE .. HANDREMOTE+6 (includes TILE2576)

    # Shotgun view weapon tiles
    allow.update(range(2613, 2620))   # SHOTGUN .. SHOTGUN+6

    # Devastator left/right weapon tiles
    allow.update(range(2510, 2512))   # DEVISTATOR .. DEVISTATOR+1

    # Knee/quick-kick HUD sequence frequently needed in minimal packs
    allow.update(range(2521, 2523))   # TILE2521 .. TILE2522

    # End-of-level nuke/destruct hand overlay (FIST)
    allow.add(1640)                   # FIST

    # ---------- Core projectile/FX tiles preloaded by cacheDukeTiles() ----------
    allow.update(range(0, 61))        # startup baseline tiles 0..60

    allow.update(range(550, 553))     # FOOTPRINTS .. FOOTPRINTS+2
    allow.update(range(1233, 1236))   # TILE1233 .. TILE1235 (runtime/map-adjacent effects)
    allow.update(range(1261, 1267))   # TRANSPORTERBEAM .. +5
    allow.update(range(1332, 1334))   # TILE1332 .. TILE1333 (avoid fallback to TILE1330)
    allow.update(range(1360, 1381))   # COOLEXPLOSION1 .. +20
    allow.update(range(1400, 1528))   # TILE1400 .. TILE1527 (reported missing runtime span)

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


def expand_required_tiles_with_sprite_precache_ranges(required_tiles):
    """
    Mirror non-PICANM sprite pre-cache ranges from premap.cpp::cacheTilesForSprite().

    `mapinfo` reports direct map picnums only. Some sprites force additional
    contiguous tiles at runtime via `extraTiles` or explicit side-effects.
    """
    expanded = set(required_tiles)

    # Contiguous picnum..picnum+N ranges implied by `extraTiles` behavior.
    # Values are intentionally from source/duke3d/src/premap.cpp switch cases.
    contiguous_runtime_groups = [
        {
            "name": "Camera and nuke barrel runtime tiles",
            "triggers": {621, 1227},      # CAMERA1 / NUKEBARREL
            "length": 5,                  # picnum .. picnum+4
        },
        {
            "name": "Exploding barrel and hazard variants",
            "triggers": {1079, 1238, 1247},  # OOZFILTER / EXPLODINGBARREL / SEENINE
            "length": 3,                      # picnum .. picnum+2
        },
        {
            "name": "Rubber can wobble variants",
            "triggers": {1062},          # RUBBERCAN
            "length": 2,                 # picnum .. picnum+1
        },
        {
            "name": "Toilet water animation chunk",
            "triggers": {921},           # TOILETWATER
            "length": 4,                 # picnum .. picnum+3
        },
        {
            "name": "Atomic health spin frames",
            "triggers": {100},           # ATOMICHEALTH
            "length": 14,                # picnum .. picnum+13
        },
        {
            "name": "FEMPIC1 runtime span",
            "triggers": {1280},          # FEMPIC1
            "length": 44,                # picnum .. picnum+43
        },
    ]

    for group in contiguous_runtime_groups:
        matching_triggers = expanded & group["triggers"]
        if not matching_triggers:
            continue

        length = group["length"]
        for trigger in matching_triggers:
            expanded.update(range(trigger, trigger + length))

    # Explicit one-off state/side-effect tiles switched at runtime.
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
        {
            "name": "Hydrent/toilet/stall broken + water states",
            "triggers": {569, 571, 981},     # TOILET / STALL / HYDRENT
            "tiles": {568, 572, 921, 922, 923, 924, 938},
            # TOILETBROKE(568), STALLBROKE(572), TOILETWATER..+3(921..924), BROKEFIREHYDRENT(938)
        },
        {
            "name": "Switch on/off partner states (base+1)",
            # From sector.cpp switch handling (REST_SWITCH_CASES + ACCESSSWITCH_CASES + DIPSWITCH_LIKE_CASES)
            "triggers": {130, 132, 134, 136, 138, 140, 162, 164, 166, 168, 170, 712},
            "tiles": {131, 133, 135, 137, 139, 141, 163, 165, 167, 169, 171, 713},
        },
        {
            "name": "Screenbreak rotating trio",
            # sector.cpp animates SCREENBREAK6..8 as a cycle when active
            "triggers": {268, 269, 270},
            "tiles": {268, 269, 270},
        },
        {
            "name": "Grate break replacement state",
            # sector.cpp damage handlers replace GRATE1 with BGRATE1
            "triggers": {595},
            "tiles": {596},
        },
        {
            "name": "Wall screenbreak random replacement trio",
            # sector.cpp wall damage path swaps many breakable wall pics to
            # W_SCREENBREAK + (krand() % 3) => 357..359
            "triggers": {
                179, 263, 264, 265, 266, 267, 268, 269, 270,
                271, 272, 273, 274, 275, 276, 277, 278, 279,
                280, 281,
            },
            "tiles": {357, 358, 359},
        },
    ]

    for group in runtime_state_groups:
        if not (expanded & group["triggers"]):
            continue
        expanded.update(group["tiles"])

    return expanded

def parse_tile_id_from_token(token: str, defines: dict):
    if re.match(r"^-?\d+$", token):
        return int(token)
    return defines.get(token.lower())


def parse_sound_id_from_token(token: str, defines: dict):
    return parse_tile_id_from_token(token, defines)



def parse_con_defines_and_sounds(con_path: Path, defines: dict, voc_to_sound_ids: dict, sound_fields_by_id: dict):
    with con_path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = strip_con_line_comment(raw_line).strip()
            if not line:
                continue

            # Examples:
            #   define PISTOL_FIRE 3
            #   sound PISTOL_FIRE PISTOL.VOC ...
            #   definesound INSERT_CLIP clipin.voc 0 0 0 0 0
            tokens = line.split()
            if not tokens:
                continue

            keyword = tokens[0].lower()
            if keyword == "define" and len(tokens) >= 3:
                name = tokens[1].lower()
                value = tokens[2]
                if re.match(r"^-?\d+$", value):
                    defines[name] = int(value)
                continue

            if keyword in {"sound", "definesound"} and len(tokens) >= 3:
                sound_token = tokens[1]
                sound_file = Path(tokens[2]).name
                if Path(sound_file).suffix.lower() != ".voc":
                    continue

                sound_id = parse_sound_id_from_token(sound_token, defines)
                if sound_id is None:
                    continue

                voc_to_sound_ids.setdefault(sound_file.lower(), set()).add(sound_id)

                # USER.CON definesound format:
                # definesound <value> <filename> <pitch_lower> <pitch_upper> <priority> <type> <distance>
                if keyword == "definesound" and len(tokens) >= 8:
                    sound_fields_by_id[sound_id] = {
                        "minpitch": tokens[3],
                        "maxpitch": tokens[4],
                        "priority": tokens[5],
                        "type": tokens[6],
                        "distance": tokens[7],
                    }



def build_sound_maps_from_cons(temp_dir: Path):
    defines = {}
    voc_to_sound_ids = {}
    sound_fields_by_id = {}

    # Parse all CON files in deterministic order. This covers common DUKE3D
    # layouts where sound tokens are defined in one CON and used in another.
    con_files = sorted(
        [p for p in temp_dir.iterdir() if p.is_file() and p.suffix.lower() == ".con"],
        key=lambda p: p.name.lower(),
    )
    for con_file in con_files:
        parse_con_defines_and_sounds(con_file, defines, voc_to_sound_ids, sound_fields_by_id)

    return voc_to_sound_ids, sound_fields_by_id



def parse_con_defines_and_precache_ranges(con_path: Path, defines: dict, precache_ranges: dict):
    with con_path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = strip_con_line_comment(raw_line).strip()
            if not line:
                continue

            tokens = line.split()
            if not tokens:
                continue

            keyword = tokens[0].lower()
            if keyword == "define" and len(tokens) >= 3:
                name = tokens[1].lower()
                value = tokens[2]
                if re.match(r"^-?\d+$", value):
                    defines[name] = int(value)
                continue

            # CON syntax: precache <startTile> <endTile> <cacheFlag>
            if keyword == "precache" and len(tokens) >= 3:
                start_tile = parse_tile_id_from_token(tokens[1], defines)
                end_tile = parse_tile_id_from_token(tokens[2], defines)
                if start_tile is None or end_tile is None:
                    continue
                if start_tile < 0 or end_tile < 0:
                    continue
                if end_tile < start_tile:
                    start_tile, end_tile = end_tile, start_tile

                previous_end = precache_ranges.get(start_tile, start_tile)
                precache_ranges[start_tile] = max(previous_end, end_tile)


def build_precache_ranges_from_cons(temp_dir: Path):
    defines = {}
    precache_ranges = {}

    con_files = sorted(
        [p for p in temp_dir.iterdir() if p.is_file() and p.suffix.lower() == ".con"],
        key=lambda p: p.name.lower(),
    )
    for con_file in con_files:
        parse_con_defines_and_precache_ranges(con_file, defines, precache_ranges)

    return precache_ranges



def expand_required_tiles_with_con_precache_ranges(required_tiles, precache_ranges):
    if not required_tiles or not precache_ranges:
        return set(required_tiles)

    expanded = set(required_tiles)
    for tile in list(required_tiles):
        end_tile = precache_ranges.get(tile)
        if end_tile is None:
            continue
        expanded.update(range(tile, end_tile + 1))

    return expanded



def main():
    normalized_argv = normalize_case_insensitive_options(
        sys.argv[1:],
        ["--pngfolder", "--map", "--includeart", "--ultraminimalmenu", "--excludefiles", "--adpcmwav", "--adpcmwidth", "--maxsoundsize"],
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
        metavar="MAP1,MAP2,...",
        type=parse_map_arg,
        help=(
            "Limit tile processing to tiles used by one or more map files "
            "(comma-separated, case-insensitive), e.g. E1L1.MAP,E1L2.MAP. "
            "If omitted, all .MAP files in the extracted GRP are used."
        ),
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
        "--excludefiles",
        metavar="FILE1,FILE2,...",
        action="append",
        type=parse_excludefiles_arg,
        help="Exclude one or more filenames from the final GRP (comma-separated, repeatable), e.g. --excludefiles TILES3280.PNG",
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
    parser.add_argument(
        "--adpcmwav",
        action="store_true",
        help=(
            "Convert each non-excluded .VOC in the extracted GRP to ADPCM IMA WAV "
            "and emit matching sound { id N file name.wav } entries in duke3d.def"
        ),
    )
    parser.add_argument(
        "--adpcmwidth",
        metavar="N",
        type=int,
        choices=range(2, 6),
        help=(
            "Use adpcm-xq two-pass conversion width N (2..5) for --adpcmwav: "
            "ffmpeg VOC->WAV then adpcm-xq -wN WAV->ADPCM WAV"
        ),
    )
    parser.add_argument(
        "--maxsoundsize",
        metavar="N",
        type=int,
        help="Exclude .VOC/.WAV files larger than N bytes from the final GRP",
    )

    args = parser.parse_args(normalized_argv)

    if args.maxsoundsize is not None and args.maxsoundsize < 0:
        parser.error("--maxsoundsize requires a non-negative integer")

    if args.adpcmwidth is not None and not args.adpcmwav:
        parser.error("--adpcmwidth requires --adpcmwav")

    selected_tile_files = set(args.tilefilestopng or [])
    included_art_files = set(args.includeart or [])
    excluded_files = {
        name
        for group in (args.excludefiles or [])
        for name in group
    }

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

    ffmpeg = None
    adpcm_xq = None
    if args.adpcmwav:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise FileNotFoundError("Requested --adpcmwav but tool 'ffmpeg' was not found in PATH")

        if args.adpcmwidth is not None:
            adpcm_xq = Path("/home/user/sources/adpcm-xq/adpcm-xq")
            if not (adpcm_xq.exists() and os.access(adpcm_xq, os.X_OK)):
                raise FileNotFoundError(
                    "Requested --adpcmwidth but '/home/user/sources/adpcm-xq/adpcm-xq' was not found or is not executable"
                )

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
    required_mid_files = None
    selected_map_names = set()

    if args.map:
        map_files_to_process = []
        for requested_map in args.map:
            map_file = find_file_case_insensitive(temp_dir, requested_map)
            if map_file is None:
                print(f"Map file not found in extracted GRP (case-insensitive): {requested_map}")
                return 1
            map_files_to_process.append(map_file)
    else:
        map_files_to_process = sorted(
            [p for p in temp_dir.iterdir() if p.is_file() and p.suffix.lower() == ".map"],
            key=lambda p: p.name.lower(),
        )

    if map_files_to_process:
        required_tiles = set()
        for map_file in map_files_to_process:
            selected_map_names.add(map_file.name.lower())

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

            used_tiles_for_map = parse_used_tiles_from_mapinfo_output(mapinfo_output)
            required_tiles.update(used_tiles_for_map)
            print(f"[info] map {map_file.name}: found {len(used_tiles_for_map)} directly used tiles")

        if args.map:
            print(
                f"[info] --map selected {len(selected_map_names)} map(s): "
                f"{', '.join(sorted(selected_map_names))}"
            )
        else:
            print(f"[info] --map not provided: using all maps ({len(selected_map_names)} total)")

        print(f"[info] map-based tile restriction initial set size: {len(required_tiles)}")

        expanded_tiles = expand_required_tiles_with_animation_frames(arttool, temp_dir, required_tiles)
        added_tiles = len(expanded_tiles) - len(required_tiles)
        if added_tiles > 0:
            required_tiles = expanded_tiles
            print(
                f"[info] map-based tile set: added {added_tiles} animation-frame tiles "
                f"(total now {len(required_tiles)})"
            )

        enemy_expanded_tiles = expand_required_tiles_with_enemy_runtime_ranges(required_tiles)
        enemy_added_tiles = len(enemy_expanded_tiles) - len(required_tiles)
        if enemy_added_tiles > 0:
            required_tiles = enemy_expanded_tiles
            print(
                f"[info] map-based tile set: added {enemy_added_tiles} enemy runtime-frame tiles "
                f"(total now {len(required_tiles)})"
            )

        con_precache_ranges = build_precache_ranges_from_cons(temp_dir)
        con_precache_tiles = expand_required_tiles_with_con_precache_ranges(required_tiles, con_precache_ranges)
        con_precache_added = len(con_precache_tiles) - len(required_tiles)
        if con_precache_added > 0:
            required_tiles = con_precache_tiles
            print(
                f"[info] map-based tile set: added {con_precache_added} CON precache-range tiles "
                f"(total now {len(required_tiles)})"
            )

        required_mid_files = set()
        for selected_map_name in sorted(selected_map_names):
            map_required_mid_files = determine_required_mid_files_from_user_con(temp_dir, selected_map_name)
            if map_required_mid_files is None:
                required_mid_files = None
                break
            required_mid_files.update(map_required_mid_files)

        if required_mid_files is not None:
            print(
                f"[info] map-based music filtering: including {len(required_mid_files)} music files "
                "(title/end + all selected map tracks)"
            )
    else:
        print("[warn] No .MAP files found in extracted GRP; skipping map-based tile restriction")

    if required_tiles is not None:
        sprite_precache_tiles = expand_required_tiles_with_sprite_precache_ranges(required_tiles)
        sprite_precache_added = len(sprite_precache_tiles) - len(required_tiles)
        if sprite_precache_added > 0:
            required_tiles = sprite_precache_tiles
            print(
                f"[info] map-based tile set: added {sprite_precache_added} sprite runtime-precache tiles "
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

    # Final animation closure pass after all required-tile sources are merged
    # (--map, runtime state tiles, --ultraminimalmenu, etc.).
    # Without this, tiles introduced late (e.g. by --ultraminimalmenu) can miss
    # their dependent PICANM frames and cause skipped animtilerange entries.
    if required_tiles is not None:
        fully_expanded_tiles = expand_required_tiles_with_animation_frames(arttool, temp_dir, required_tiles)
        final_anim_added = len(fully_expanded_tiles) - len(required_tiles)
        if final_anim_added > 0:
            required_tiles = fully_expanded_tiles
            print(
                f"[info] required tiles finalization: added {final_anim_added} animation-frame tiles "
                f"after merging all tile sources (total now {len(required_tiles)})"
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

    replaced_voc_files = set()

    with duke_def_path.open("w", encoding="utf-8") as duke_def:
        written_tiles = set()
        missing_required_png_sources = []
        skipped_zero_size_tiles_without_png = []
        anim_def_candidates = {}
        emitted_anim_ranges = []

        if args.adpcmwav:
            voc_sound_ids, sound_fields_by_id = build_sound_maps_from_cons(temp_dir)
            emitted_sound_defs = 0

            voc_files = sorted(
                [p for p in temp_dir.iterdir() if p.is_file() and p.suffix.lower() == ".voc"],
                key=lambda p: p.name.lower(),
            )

            for voc_file in voc_files:
                if voc_file.name.lower() in excluded_files:
                    continue
                if args.maxsoundsize is not None and voc_file.stat().st_size > args.maxsoundsize:
                    print(
                        f"[info] --maxsoundsize: skipping {voc_file.name} "
                        f"({voc_file.stat().st_size} bytes > {args.maxsoundsize})"
                    )
                    continue

                wav_name = f"{voc_file.stem.lower()}.wav"
                wav_path = temp_dir / wav_name

                if args.adpcmwidth is None:
                    ffmpeg_proc = subprocess.run(
                        [ffmpeg, "-y", "-i", str(voc_file), "-c:a", "adpcm_ima_wav", str(wav_path)],
                        cwd=temp_dir,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    if ffmpeg_proc.returncode != 0 or not wav_path.exists():
                        print(f"[error] ffmpeg --adpcmwav failed for {voc_file.name}; aborting")
                        if ffmpeg_proc.stdout:
                            print(ffmpeg_proc.stdout)
                        if ffmpeg_proc.stderr:
                            print(ffmpeg_proc.stderr)
                        return 1
                else:
                    intermediate_wav_path = temp_dir / f"{voc_file.stem.lower()}.__pcm.wav"
                    intermediate_wav_path.unlink(missing_ok=True)
                    wav_path.unlink(missing_ok=True)

                    ffmpeg_proc = subprocess.run(
                        [ffmpeg, "-y", "-i", str(voc_file), str(intermediate_wav_path)],
                        cwd=temp_dir,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    if ffmpeg_proc.returncode != 0 or not intermediate_wav_path.exists():
                        print(f"[error] ffmpeg first pass (--adpcmwidth) failed for {voc_file.name}; aborting")
                        if ffmpeg_proc.stdout:
                            print(ffmpeg_proc.stdout)
                        if ffmpeg_proc.stderr:
                            print(ffmpeg_proc.stderr)
                        return 1

                    adpcm_xq_proc = subprocess.run(
                        [str(adpcm_xq), f"-w{args.adpcmwidth}", str(intermediate_wav_path), str(wav_path)],
                        cwd=temp_dir,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    intermediate_wav_path.unlink(missing_ok=True)

                    if adpcm_xq_proc.returncode != 0 or not wav_path.exists():
                        print(
                            f"[error] adpcm-xq second pass (--adpcmwidth {args.adpcmwidth}) failed for {voc_file.name}; aborting"
                        )
                        if adpcm_xq_proc.stdout:
                            print(adpcm_xq_proc.stdout)
                        if adpcm_xq_proc.stderr:
                            print(adpcm_xq_proc.stderr)
                        return 1

                if args.maxsoundsize is not None and wav_path.stat().st_size > args.maxsoundsize:
                    print(
                        f"[info] --maxsoundsize: dropping converted {wav_name} "
                        f"({wav_path.stat().st_size} bytes > {args.maxsoundsize})"
                    )
                    wav_path.unlink(missing_ok=True)
                    continue

                sound_ids = sorted(voc_sound_ids.get(voc_file.name.lower(), set()))
                if not sound_ids:
                    print(f"[warn] No sound ID found in CON files for {voc_file.name}; skipping sound {{ ... }} def entry")
                    continue

                for sound_id in sound_ids:
                    sound_fields = sound_fields_by_id.get(sound_id)
                    if sound_fields:
                        duke_def.write(
                            "sound { "
                            f"id {sound_id} "
                            f"file {wav_name} "
                            f"minpitch {sound_fields['minpitch']} "
                            f"maxpitch {sound_fields['maxpitch']} "
                            f"priority {sound_fields['priority']} "
                            f"type {sound_fields['type']} "
                            f"distance {sound_fields['distance']} "
                            "}\n"
                        )
                    else:
                        duke_def.write(f"sound {{ id {sound_id} file {wav_name} }}\n")

                    emitted_sound_defs += 1

                replaced_voc_files.add(voc_file.name.lower())

            if emitted_sound_defs > 0:
                duke_def.write("\n")
                print(f"[info] --adpcmwav: emitted {emitted_sound_defs} sound entries in duke3d.def")

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
                        raw_size = get_tile_raw_size(arttool, temp_dir, global_tile)
                        if raw_size == 0:
                            skipped_zero_size_tiles_without_png.append(global_tile)
                            continue
                        missing_required_png_sources.append(global_tile)
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
                            xofs, yofs = get_tile_offsets(arttool, temp_dir, global_tile)
                            if xofs == 0 and yofs == 0:
                                duke_def.write(f"tilefromtexture {global_tile} {{ file {out_png.name} }}\n")
                            else:
                                duke_def.write(
                                    f"tilefromtexture {global_tile} {{ file {out_png.name} xoffset {xofs} yoffset {yofs} }}\n"
                                )
                            written_tiles.add(global_tile)
                        else:
                            print(f"[warn] rmtile failed for tile {global_tile}, keeping ART tile")
                            out_png.unlink()
                    else:
                        out_png.unlink()
                else:
                    # In normal mode, keep ART as-is and override via DEF only.
                    xofs, yofs = get_tile_offsets(arttool, temp_dir, global_tile)
                    if xofs == 0 and yofs == 0:
                        duke_def.write(f"tilefromtexture {global_tile} {{ file {out_png.name} }}\n")
                    else:
                        duke_def.write(
                            f"tilefromtexture {global_tile} {{ file {out_png.name} xoffset {xofs} yoffset {yofs} }}\n"
                        )
                    written_tiles.add(global_tile)

                if global_tile in written_tiles and global_tile not in anim_def_candidates:
                    anim_info = get_tile_anim_info(arttool, temp_dir, global_tile)
                    if anim_info["frames"] > 0 and anim_info["type"] > 0:
                        anim_def_candidates[global_tile] = anim_info

                # Keep PCX files for debugging when --keep-temp is used.

        if skipped_zero_size_tiles_without_png:
            skipped_zero_size_tiles_without_png = sorted(set(skipped_zero_size_tiles_without_png))
            print(
                f"[info] --pngfolder: skipped {len(skipped_zero_size_tiles_without_png)} "
                "required tile(s) that are 0x0 in ART and have no TILE####.PNG source"
            )

        if missing_required_png_sources:
            missing_required_png_sources = sorted(set(missing_required_png_sources))
            preview = ",".join(str(t) for t in missing_required_png_sources[:24])
            if len(missing_required_png_sources) > 24:
                preview += ",..."
            print(
                f"[error] --pngfolder missing required TILE####.PNG files for "
                f"{len(missing_required_png_sources)} non-empty tile(s): {preview}"
            )
            print("[error] Should we be aborting to avoid silently skipping required tiles?")
            #return 1

        emitted_anim_defs = 0
        skipped_anim_defs = 0
        skipped_anim_details = []
        for anchor_tile in sorted(anim_def_candidates):
            info = anim_def_candidates[anchor_tile]
            first_tile = min(info["first"], info["last"])
            last_tile = max(info["first"], info["last"])
            needed_tiles = set(range(first_tile, last_tile + 1))

            if not needed_tiles.issubset(written_tiles):
                skipped_anim_defs += 1
                missing_tiles = sorted(needed_tiles - written_tiles)
                skipped_anim_details.append(
                    {
                        "anchor": anchor_tile,
                        "first": first_tile,
                        "last": last_tile,
                        "type": info["type"],
                        "speed": info["speed"],
                        "missing": missing_tiles,
                    }
                )
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

        for skipped in skipped_anim_details:
            print(
                "[info] duke3d.def: skipped animtilerange "
                f"anchor={skipped['anchor']} range={skipped['first']}..{skipped['last']} "
                f"type={skipped['type']} speed={skipped['speed']} "
                f"reason=incomplete frame coverage; missing tiles: "
                f"{','.join(str(t) for t in skipped['missing'])}"
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
        "*.WAV", "*.wav",
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

    if excluded_files:
        files = [f for f in files if f.name.lower() not in excluded_files]

    if args.maxsoundsize is not None:
        files = [
            f for f in files
            if f.suffix.lower() not in {".voc", ".wav"}
            or f.stat().st_size <= args.maxsoundsize
        ]

    if args.adpcmwav and replaced_voc_files:
        files = [
            f for f in files
            if f.suffix.lower() != ".voc"
            or f.name.lower() not in replaced_voc_files
        ]

    if required_tiles is not None:
        needed_art_indices = {tile // 256 for tile in required_tiles}
        files = [
            f for f in files
            if f.suffix.lower() != ".art"
            or tilefile_index_from_name(f.name) in needed_art_indices
            or f.name.lower() in included_art_files
        ]

    if selected_map_names:
        files = [
            f for f in files
            if f.suffix.lower() != ".map"
            or f.name.lower() in selected_map_names
        ]

    if required_mid_files is not None:
        files = [
            f for f in files
            if f.suffix.lower() != ".mid"
            or f.name.lower() in required_mid_files
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

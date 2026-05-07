
# Format: "FILENAME,optional human comment"
# 2456 is the background for the menus and should be kept, but single color will make it just 3% of the size!
# 0966 is a poster, replace it by something smaller?
# 0095 is the night sky with stars
# 0989-0993 are the skyline, replace with something smaller? or something repeating?
# SHOTGUN7.VOC leave it because it's very important
EXCLUDE_CSVS=$(cat <<'EOF'
TILE1102.PNG,some high def image
TILE2445.PNG,help screen can be omitted in this pack
TILE3260.PNG,end animation
TILE3263.PNG,end animation
TILE3264.PNG,end animation
TILE3265.PNG,end animation
TILE3266.PNG,end animation
TILE3267.PNG,end animation
TILE3268.PNG,end animation
TILE3270.PNG,how to order
TILE3271.PNG,mousepad and strategy guide
TILE3272.PNG,how to buy
TILE3273.PNG,3drealms promo
TILE3280.PNG,end story text
TILE3290.PNG,spaceship picture1
TILE3291.PNG,spaceship picture2
TILE3292.PNG,end story picture and text
BONUS.VOC,274k
BARMUSIC.VOC,70k
!PRISON.VOC,
CHEW05.VOC,
!PIG.VOC
AMB81B.VOC,50k
!BOSS.VOC
WIND54.VOC
WARAMB23.VOC
WARAMB13.VOC
WARAMB21.VOC,40k
FIRE09.VOC,38k
DSCREM38.VOC,unused
PAIN13.VOC,unused
PAIN28.VOC,unused
PIGWRN.VOC,unused
PISSIN01.VOC,unused
EOF
)

EXCLUDE_ARGS=()
while IFS= read -r csv; do
    [ -z "$csv" ] && continue
    filename=$(printf '%s' "$csv" | cut -d',' -f1 | tr -d '[:space:]')
    [ -z "$filename" ] && continue
    EXCLUDE_ARGS+=("--excludefiles" "$filename")
done <<EOF
$EXCLUDE_CSVS
EOF

#python3 duke3d_compact_grp.py --optipng --zopflipng --ultraminimalmenu --pngfolder precalculated_pngs/ --map E1L1.MAP --keep-temp "${EXCLUDE_ARGS[@]}" /tmp/DUKE3D_v1.3d_shareware.grp
#python3 duke3d_compact_grp.py --optipng --zopflipng --ultraminimalmenu --pngfolder precalculated_pngs/ --map E1L1.MAP --map E1L2.MAP --keep-temp "${EXCLUDE_ARGS[@]}" /tmp/DUKE3D_v1.3d_shareware.grp

#python3 duke3d_compact_grp.py --optipng --zopflipng --adpcmwav --ultraminimalmenu --pngfolder precalculated_pngs/ --map E1L1.MAP --keep-temp "${EXCLUDE_ARGS[@]}" /tmp/DUKE3D_v1.3d_shareware.grp --output E1L1_adpcm_some_excludes.grp
#python3 duke3d_compact_grp.py --optipng --zopflipng --adpcmwav --ultraminimalmenu --pngfolder precalculated_pngs/ --map E1L1.MAP,E1L2.MAP --keep-temp "${EXCLUDE_ARGS[@]}" /tmp/DUKE3D_v1.3d_shareware.grp --output E1L1_E1L2_adpcm_some_excludes.grp

#python3 duke3d_compact_grp.py --optipng --zopflipng --adpcmwav --ultraminimalmenu --pngfolder precalculated_pngs/ --map E1L1.MAP --keep-temp "${EXCLUDE_ARGS[@]}" /tmp/DUKE3D_v1.3d_shareware.grp --output newfile.grp

# maxsoundsize:
#python3 duke3d_compact_grp.py --optipng --zopflipng --adpcmwav --ultraminimalmenu --pngfolder precalculated_pngs/ --map E1L1.MAP --keep-temp "${EXCLUDE_ARGS[@]}" /tmp/DUKE3D_v1.3d_shareware.grp --output newfile.grp --maxsoundsize 15000

#python3 duke3d_compact_grp.py --optipng --zopflipng --adpcmwav --ultraminimalmenu --pngfolder precalculated_pngs/ --map E1L1.MAP --keep-temp "${EXCLUDE_ARGS[@]}" /tmp/DUKE3D_v1.3d_shareware.grp --output newfile.grp --adpcmwidth 2

# All levels, everything included, same quality, just compressed and removing a few unused sounds:
python3 duke3d_compact_grp.py --optipng --zopflipng --adpcmwav --ultraminimalmenu --pngfolder precalculated_pngs/ --keep-temp /tmp/DUKE3D_v1.3d_shareware.grp --output E1L1-6.grp
zip -9 E1L1-6.grp.zip E1L1-6.grp

# All levels but some compromise:
python3 duke3d_compact_grp.py --optipng --zopflipng --adpcmwav --ultraminimalmenu --pngfolder precalculated_pngs/ --keep-temp "${EXCLUDE_ARGS[@]}" /tmp/DUKE3D_v1.3d_shareware.grp --adpcmwidth 2 --output E1L1-6_compromise.grp
zip -9 E1L1-6_compromise.grp.zip E1L1-6_compromise.grp

# All levels but tiny:
python3 duke3d_compact_grp.py --optipng --zopflipng --adpcmwav --ultraminimalmenu --pngfolder precalculated_pngs/ --keep-temp "${EXCLUDE_ARGS[@]}" /tmp/DUKE3D_v1.3d_shareware.grp --adpcmwidth 2 --maxsoundsize 5000 --output E1L1-6_tiny.grp
zip -9 E1L1-6_tiny.grp.zip E1L1-6_tiny.grp

# 2 levels with compromise:
python3 duke3d_compact_grp.py --optipng --zopflipng --adpcmwav --ultraminimalmenu --pngfolder precalculated_pngs/ --keep-temp "${EXCLUDE_ARGS[@]}" --map E1L1.MAP,E1L2.MAP /tmp/DUKE3D_v1.3d_shareware.grp --adpcmwidth 2 --maxsoundsize 15000 --output E1L1-2_compromise.grp
zip -9 E1L1-2_compromise.grp.zip E1L1-2_compromise.grp

# One level but some compromise:
python3 duke3d_compact_grp.py --optipng --zopflipng --adpcmwav --ultraminimalmenu --pngfolder precalculated_pngs/ --map E1L1.MAP --keep-temp "${EXCLUDE_ARGS[@]}" /tmp/DUKE3D_v1.3d_shareware.grp  --adpcmwidth 2 --output E1L1_compromise.grp
zip -9 E1L1_compromise.grp.zip E1L1_compromise.grp

# One level, tiny:
python3 duke3d_compact_grp.py --optipng --zopflipng --adpcmwav --ultraminimalmenu --pngfolder precalculated_pngs/ --map E1L1.MAP --keep-temp "${EXCLUDE_ARGS[@]}" /tmp/DUKE3D_v1.3d_shareware.grp  --adpcmwidth 2 --maxsoundsize 5000 --output E1L1_tiny.grp
zip -9 E1L1_tiny.grp.zip E1L1_tiny.grp

# Current minimal, just to establish lower bound:
python3 duke3d_compact_grp.py --optipng --zopflipng --adpcmwav --ultraminimalmenu --pngfolder precalculated_pngs/ --map E1L1.MAP --keep-temp "${EXCLUDE_ARGS[@]}" /tmp/DUKE3D_v1.3d_shareware.grp  --adpcmwidth 2 --maxsoundsize 0 --output E1L1_minimal.grp
zip -9 E1L1_minimal.grp.zip E1L1_minimal.grp

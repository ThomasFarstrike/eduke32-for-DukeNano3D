#python3 duke3d_compact_grp.py --quicktest --keep-temp /tmp/DUKE3D_v1.3d_shareware.grp 
#python3 duke3d_compact_grp.py --keep-temp /tmp/DUKE3D_v1.3d_shareware.grp 

# nope:
#python3 duke3d_compact_grp.py --optipng --zopflipng --pngfolder precalculated_pngs/ --map E1L1.MAP --tilefilestopng 0,1,2,3,4,5,6,7,8,9,10,11   --keep-temp /tmp/DUKE3D_v1.3d_shareware.grp

#python3 duke3d_compact_grp.py --optipng --zopflipng --pngfolder precalculated_pngs/ --map E1L1.MAP --includeart 12 --keep-temp /tmp/DUKE3D_v1.3d_shareware.grp

#python3 duke3d_compact_grp.py --optipng --zopflipng --pngfolder precalculated_pngs/ --map E1L1.MAP --includeart TILES012.ART --keep-temp /tmp/DUKE3D_v1.3d_shareware.grp

# few artifacts but not bad:
#python3 duke3d_compact_grp.py --optipng --zopflipng --ultraminimalmenu --pngfolder precalculated_pngs/ --map E1L1.MAP --includeart TILES012.ART --keep-temp /tmp/DUKE3D_v1.3d_shareware.grp

#python3 duke3d_compact_grp.py --optipng --zopflipng --ultraminimalmenu --pngfolder precalculated_pngs/ --map E1L1.MAP --keep-temp /tmp/DUKE3D_v1.3d_shareware.grp

# Format: "FILENAME,optional human comment"
# 2456 is the background for the menus and should be kept, but single color will make it just 3% of the size!
# 0966 is a poster, replace it by something smaller?
# 0095 is the night sky with stars
# 0989-0993 are the skyline, replace with something smaller? or something repeating?
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
SHOTGUN7.VOC,
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

#python3 duke3d_compact_grp.py --optipng --zopflipng --adpcmwav --ultraminimalmenu --pngfolder precalculated_pngs/ --map E1L1.MAP --keep-temp "${EXCLUDE_ARGS[@]}" /tmp/DUKE3D_v1.3d_shareware.grp
#python3 duke3d_compact_grp.py --optipng --zopflipng --adpcmwav --ultraminimalmenu --pngfolder precalculated_pngs/ --map E1L1.MAP --keep-temp /tmp/DUKE3D_v1.3d_shareware.grp
python3 duke3d_compact_grp.py --optipng --zopflipng --adpcmwav --ultraminimalmenu --pngfolder precalculated_pngs/ --keep-temp /tmp/DUKE3D_v1.3d_shareware.grp

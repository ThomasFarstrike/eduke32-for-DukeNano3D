# This takes around 4 hours to create all the optimized PNG files,
# but once you have them, then save temp_folder/ somewhere, for example rename it to precalculated_pngs/
# and then you can use it for subsequent runs.
time python3 duke3d_compact_grp.py --optipng --zopflipng  --keep-temp /tmp/DUKE3D_v1.3d_shareware.grp

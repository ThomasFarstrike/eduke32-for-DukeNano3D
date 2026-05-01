#include "compat.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAXTILES 30720

#pragma pack(push, 1)
struct build7sect_t
{
    int16_t wallptr, wallnum;
    int32_t ceilingz, floorz;
    uint16_t ceilingstat, floorstat;
    int16_t ceilingpicnum, ceilingheinum;
    int8_t ceilingshade;
    uint8_t ceilingpal, ceilingxpanning, ceilingypanning;
    int16_t floorpicnum, floorheinum;
    int8_t floorshade;
    uint8_t floorpal, floorxpanning, floorypanning;
    uint8_t visibility, fogpal;
    int16_t lotag, hitag;
    int16_t extra;
};

struct build7wall_t
{
    int32_t x, y;
    int16_t point2, nextwall, nextsector;
    uint16_t cstat;
    int16_t picnum, overpicnum;
    int8_t shade;
    uint8_t pal, xrepeat, yrepeat, xpanning, ypanning;
    int16_t lotag, hitag;
    int16_t extra;
};

struct build7sprite_t
{
    int32_t x, y, z;
    uint16_t cstat;
    int16_t picnum;
    int8_t shade;
    uint8_t pal, clipdist, blend;
    uint8_t xrepeat, yrepeat;
    int8_t xoffset, yoffset;
    int16_t sectnum, statnum;
    int16_t ang, owner;
    int16_t xvel, yvel, zvel;
    int16_t lotag, hitag;
    int16_t extra;
};
#pragma pack(pop)

static_assert(sizeof(build7sect_t) == 40, "build7sect_t size mismatch");
static_assert(sizeof(build7wall_t) == 32, "build7wall_t size mismatch");
static_assert(sizeof(build7sprite_t) == 44, "build7sprite_t size mismatch");

static inline uint16_t bswap16(uint16_t v) { return (uint16_t)((v >> 8) | (v << 8)); }
static inline uint32_t bswap32(uint32_t v)
{
    return (v >> 24) | ((v >> 8) & 0x0000FF00u) | ((v << 8) & 0x00FF0000u) | (v << 24);
}

#if defined(__BYTE_ORDER__) && (__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__)
# define LE16(x) (x)
# define LE32(x) (x)
#else
# define LE16(x) bswap16(x)
# define LE32(x) bswap32(x)
#endif

static void mark_tile(uint8_t *used, int32_t tile)
{
    if (tile >= 0 && tile < MAXTILES)
        used[tile] = 1;
}

static int read_u16(FILE *fp, uint16_t *out)
{
    return fread(out, sizeof(*out), 1, fp) == 1;
}

static int read_u32(FILE *fp, uint32_t *out)
{
    return fread(out, sizeof(*out), 1, fp) == 1;
}

static int process_map(const char *path, uint8_t *used)
{
    FILE *fp = fopen(path, "rb");
    if (!fp)
    {
        fprintf(stderr, "maptiles: unable to open %s\n", path);
        return 1;
    }

    uint32_t rawver = 0;
    if (!read_u32(fp, &rawver))
    {
        fprintf(stderr, "maptiles: failed to read version from %s\n", path);
        fclose(fp);
        return 1;
    }

    if (!memcmp(&rawver, "--ED", 4))
    {
        fprintf(stderr, "maptiles: %s is map-text (v10); not supported by this tool\n", path);
        fclose(fp);
        return 1;
    }

    const uint32_t mapversion = LE32(rawver);
    if (mapversion < 7 || mapversion > 9)
    {
        fprintf(stderr, "maptiles: %s has unsupported map version %u\n", path, mapversion);
        fclose(fp);
        return 1;
    }

    if (fseek(fp, 20, SEEK_SET) != 0)
    {
        fprintf(stderr, "maptiles: failed to seek header in %s\n", path);
        fclose(fp);
        return 1;
    }

    uint16_t numsectors = 0;
    if (!read_u16(fp, &numsectors))
    {
        fprintf(stderr, "maptiles: failed to read numsectors from %s\n", path);
        fclose(fp);
        return 1;
    }
    numsectors = LE16(numsectors);

    for (uint16_t i = 0; i < numsectors; ++i)
    {
        build7sect_t sec;
        if (fread(&sec, sizeof(sec), 1, fp) != 1)
        {
            fprintf(stderr, "maptiles: failed to read sector %u from %s\n", i, path);
            fclose(fp);
            return 1;
        }

        const int32_t ceilpic = (int16_t)LE16((uint16_t)sec.ceilingpicnum);
        const int32_t floorpic = (int16_t)LE16((uint16_t)sec.floorpicnum);
        mark_tile(used, ceilpic);
        mark_tile(used, floorpic);
    }

    uint16_t numwalls = 0;
    if (!read_u16(fp, &numwalls))
    {
        fprintf(stderr, "maptiles: failed to read numwalls from %s\n", path);
        fclose(fp);
        return 1;
    }
    numwalls = LE16(numwalls);

    for (uint16_t i = 0; i < numwalls; ++i)
    {
        build7wall_t wal;
        if (fread(&wal, sizeof(wal), 1, fp) != 1)
        {
            fprintf(stderr, "maptiles: failed to read wall %u from %s\n", i, path);
            fclose(fp);
            return 1;
        }

        const int32_t pic = (int16_t)LE16((uint16_t)wal.picnum);
        const int32_t overpic = (int16_t)LE16((uint16_t)wal.overpicnum);
        mark_tile(used, pic);
        mark_tile(used, overpic);
    }

    uint16_t numsprites = 0;
    if (!read_u16(fp, &numsprites))
    {
        fprintf(stderr, "maptiles: failed to read numsprites from %s\n", path);
        fclose(fp);
        return 1;
    }
    numsprites = LE16(numsprites);

    for (uint16_t i = 0; i < numsprites; ++i)
    {
        build7sprite_t spr;
        if (fread(&spr, sizeof(spr), 1, fp) != 1)
        {
            fprintf(stderr, "maptiles: failed to read sprite %u from %s\n", i, path);
            fclose(fp);
            return 1;
        }

        const int32_t pic = (int16_t)LE16((uint16_t)spr.picnum);
        mark_tile(used, pic);
    }

    fclose(fp);
    return 0;
}

static void dump_used(const uint8_t *used)
{
    for (int32_t i = 0; i < MAXTILES; ++i)
        if (used[i])
            printf("%d\n", i);
}

static void usage(const char *argv0)
{
    fprintf(stderr,
        "Usage: %s [--per-map] <map1.map> [map2.map ...]\n"
        "  --per-map  Print a separate list per map (with a header).\n",
        argv0);
}

int main(int argc, char **argv)
{
    int per_map = 0;
    int argi = 1;

    for (; argi < argc; ++argi)
    {
        if (!strcmp(argv[argi], "--per-map"))
        {
            per_map = 1;
            continue;
        }
        if (!strcmp(argv[argi], "-h") || !strcmp(argv[argi], "--help"))
        {
            usage(argv[0]);
            return 0;
        }
        if (argv[argi][0] == '-')
        {
            usage(argv[0]);
            return 1;
        }
        break;
    }

    if (argi >= argc)
    {
        usage(argv[0]);
        return 1;
    }

    if (per_map)
    {
        for (int i = argi; i < argc; ++i)
        {
            uint8_t used[MAXTILES] = {0};
            if (process_map(argv[i], used) != 0)
                return 1;

            printf("# %s\n", argv[i]);
            dump_used(used);
        }
        return 0;
    }

    uint8_t used[MAXTILES] = {0};
    for (int i = argi; i < argc; ++i)
    {
        if (process_map(argv[i], used) != 0)
            return 1;
    }

    dump_used(used);
    return 0;
}

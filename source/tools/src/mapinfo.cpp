#include "compat.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <string>
#include <sys/stat.h>
#include <vector>

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

static inline uint16_t bswap16(uint16_t v) { return uint16_t((v >> 8) | (v << 8)); }
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

struct MapReport
{
    std::string path;
    uint64_t fileSize = 0;
    int version = -1;
    uint16_t numSectors = 0;
    uint16_t numWalls = 0;
    uint16_t numSprites = 0;
    std::vector<int> usedTiles;
    std::string warning;
    bool ok = false;
};

enum AssetType
{
    ASSET_TILE_ART = 0,
    ASSET_TILE_IMAGE,
    ASSET_SOUND_FX,
    ASSET_MIDI,
    ASSET_MAP,
    ASSET_CON,
    ASSET_DEF,
    ASSET_OTHER,
    ASSET_COUNT
};

struct AssetEntry
{
    AssetType type;
    std::string path;
    std::string name;
    uint64_t size = 0;
    bool required = false;
};

struct TypeStat
{
    uint64_t totalBytes = 0;
    int count = 0;
};

static const char *g_assetTypeName[ASSET_COUNT] = {
    "TILE_ART",
    "TILE_IMAGE",
    "SOUND_FX",
    "MIDI",
    "MAP",
    "CON_SCRIPT",
    "DEF_SCRIPT",
    "OTHER",
};

static int read_u16(FILE *fp, uint16_t *out) { return fread(out, sizeof(*out), 1, fp) == 1; }
static int read_u32(FILE *fp, uint32_t *out) { return fread(out, sizeof(*out), 1, fp) == 1; }

static bool ends_with_icase(std::string const &s, const char *suffix)
{
    size_t const slen = s.size();
    size_t const tlen = strlen(suffix);
    if (slen < tlen)
        return false;

    for (size_t i = 0; i < tlen; ++i)
    {
        char a = s[slen - tlen + i];
        char b = suffix[i];
        if (a >= 'A' && a <= 'Z') a = char(a - 'A' + 'a');
        if (b >= 'A' && b <= 'Z') b = char(b - 'A' + 'a');
        if (a != b)
            return false;
    }
    return true;
}

static bool starts_with_icase(std::string const &s, const char *prefix)
{
    size_t const plen = strlen(prefix);
    if (s.size() < plen)
        return false;

    for (size_t i = 0; i < plen; ++i)
    {
        char a = s[i];
        char b = prefix[i];
        if (a >= 'A' && a <= 'Z') a = char(a - 'A' + 'a');
        if (b >= 'A' && b <= 'Z') b = char(b - 'A' + 'a');
        if (a != b)
            return false;
    }
    return true;
}

static std::string dirname_of(std::string const &path)
{
    size_t const pos = path.find_last_of("/\\");
    if (pos == std::string::npos)
        return ".";
    if (pos == 0)
        return path.substr(0, 1);
    return path.substr(0, pos);
}

static std::string join_path(std::string const &a, std::string const &b)
{
    if (a.empty() || a == ".")
        return b;
    if (a.back() == '/' || a.back() == '\\')
        return a + b;
    return a + "/" + b;
}

static int parse_decimal(const std::string &s, size_t pos, size_t len)
{
    if (pos + len > s.size() || len == 0)
        return -1;

    int v = 0;
    for (size_t i = 0; i < len; ++i)
    {
        char c = s[pos + i];
        if (c < '0' || c > '9')
            return -1;
        v = v * 10 + (c - '0');
    }
    return v;
}

static int tile_from_filename(const std::string &filename)
{
    if (!starts_with_icase(filename, "tile"))
        return -1;

    size_t i = 4;
    while (i < filename.size() && filename[i] >= '0' && filename[i] <= '9')
        ++i;

    if (i == 4)
        return -1;

    return parse_decimal(filename, 4, i - 4);
}

static int art_index_from_filename(const std::string &filename)
{
    if (!starts_with_icase(filename, "tiles"))
        return -1;

    int idx = parse_decimal(filename, 5, 3);
    if (idx < 0)
        return -1;

    return idx;
}

static void mark_tile(std::vector<uint8_t> &used, int32_t tile)
{
    if (tile >= 0 && tile < MAXTILES)
        used[(size_t)tile] = 1;
}

static int process_map(const char *path, MapReport *report, std::vector<uint8_t> &globalUsed)
{
    report->path = path;

    struct stat st;
    if (stat(path, &st) == 0)
        report->fileSize = (uint64_t)st.st_size;

    FILE *fp = fopen(path, "rb");
    if (!fp)
    {
        report->warning = "unable to open map";
        return 1;
    }

    uint32_t rawver = 0;
    if (!read_u32(fp, &rawver))
    {
        report->warning = "failed to read map version";
        fclose(fp);
        return 1;
    }

    if (!memcmp(&rawver, "--ED", 4))
    {
        report->version = 10;
        report->warning = "map-text (v10) not supported by this binary parser";
        fclose(fp);
        return 1;
    }

    uint32_t const mapversion = LE32(rawver);
    report->version = (int)mapversion;

    if (mapversion < 7 || mapversion > 9)
    {
        report->warning = "unsupported binary map version";
        fclose(fp);
        return 1;
    }

    if (fseek(fp, 20, SEEK_SET) != 0)
    {
        report->warning = "failed to seek map header";
        fclose(fp);
        return 1;
    }

    uint16_t numsectors = 0;
    if (!read_u16(fp, &numsectors))
    {
        report->warning = "failed to read numsectors";
        fclose(fp);
        return 1;
    }
    numsectors = LE16(numsectors);
    report->numSectors = numsectors;

    std::vector<uint8_t> localUsed((size_t)MAXTILES, 0);

    for (uint16_t i = 0; i < numsectors; ++i)
    {
        build7sect_t sec;
        if (fread(&sec, sizeof(sec), 1, fp) != 1)
        {
            report->warning = "failed reading sector data";
            fclose(fp);
            return 1;
        }

        mark_tile(localUsed, (int16_t)LE16((uint16_t)sec.ceilingpicnum));
        mark_tile(localUsed, (int16_t)LE16((uint16_t)sec.floorpicnum));
    }

    uint16_t numwalls = 0;
    if (!read_u16(fp, &numwalls))
    {
        report->warning = "failed to read numwalls";
        fclose(fp);
        return 1;
    }
    numwalls = LE16(numwalls);
    report->numWalls = numwalls;

    for (uint16_t i = 0; i < numwalls; ++i)
    {
        build7wall_t wal;
        if (fread(&wal, sizeof(wal), 1, fp) != 1)
        {
            report->warning = "failed reading wall data";
            fclose(fp);
            return 1;
        }

        mark_tile(localUsed, (int16_t)LE16((uint16_t)wal.picnum));
        mark_tile(localUsed, (int16_t)LE16((uint16_t)wal.overpicnum));
    }

    uint16_t numsprites = 0;
    if (!read_u16(fp, &numsprites))
    {
        report->warning = "failed to read numsprites";
        fclose(fp);
        return 1;
    }
    numsprites = LE16(numsprites);
    report->numSprites = numsprites;

    for (uint16_t i = 0; i < numsprites; ++i)
    {
        build7sprite_t spr;
        if (fread(&spr, sizeof(spr), 1, fp) != 1)
        {
            report->warning = "failed reading sprite data";
            fclose(fp);
            return 1;
        }

        mark_tile(localUsed, (int16_t)LE16((uint16_t)spr.picnum));
    }

    fclose(fp);

    for (int32_t i = 0; i < MAXTILES; ++i)
    {
        if (localUsed[(size_t)i])
        {
            report->usedTiles.push_back(i);
            globalUsed[(size_t)i] = 1;
        }
    }

    report->ok = true;
    return 0;
}

static AssetType classify_asset(std::string const &filename)
{
    if (ends_with_icase(filename, ".art"))
        return ASSET_TILE_ART;

    if (starts_with_icase(filename, "tile") &&
        (ends_with_icase(filename, ".png") || ends_with_icase(filename, ".pcx") ||
         ends_with_icase(filename, ".jpg") || ends_with_icase(filename, ".jpeg") ||
         ends_with_icase(filename, ".bmp") || ends_with_icase(filename, ".tga") ||
         ends_with_icase(filename, ".gif") || ends_with_icase(filename, ".webp")))
        return ASSET_TILE_IMAGE;

    if (ends_with_icase(filename, ".voc") || ends_with_icase(filename, ".wav") ||
        ends_with_icase(filename, ".ogg") || ends_with_icase(filename, ".flac") ||
        ends_with_icase(filename, ".mp3"))
        return ASSET_SOUND_FX;

    if (ends_with_icase(filename, ".mid") || ends_with_icase(filename, ".midi"))
        return ASSET_MIDI;

    if (ends_with_icase(filename, ".map"))
        return ASSET_MAP;

    if (ends_with_icase(filename, ".con"))
        return ASSET_CON;

    if (ends_with_icase(filename, ".def"))
        return ASSET_DEF;

    return ASSET_OTHER;
}

static int scan_asset_dir(std::string const &dir, std::vector<AssetEntry> *out)
{
    DIR *dp = opendir(dir.c_str());
    if (!dp)
    {
        fprintf(stderr, "mapinfo: warning: unable to open asset dir '%s'\n", dir.c_str());
        return 1;
    }

    struct dirent *de;
    while ((de = readdir(dp)) != nullptr)
    {
        if (!strcmp(de->d_name, ".") || !strcmp(de->d_name, ".."))
            continue;

        std::string const fullpath = join_path(dir, de->d_name);

        struct stat st;
        if (stat(fullpath.c_str(), &st) != 0)
            continue;

        if (!S_ISREG(st.st_mode))
            continue;

        AssetEntry entry;
        entry.path = fullpath;
        entry.name = de->d_name;
        entry.size = (uint64_t)st.st_size;
        entry.type = classify_asset(entry.name);

        out->push_back(entry);
    }

    closedir(dp);
    return 0;
}

static void print_tile_list(std::vector<int> const &tiles)
{
    if (tiles.empty())
    {
        printf("  used_tiles: (none)\n");
        return;
    }

    printf("  used_tiles (%zu):", tiles.size());
    for (size_t i = 0; i < tiles.size(); ++i)
    {
        if (i % 20 == 0)
            printf("\n    ");
        printf("%d ", tiles[i]);
    }
    printf("\n");
}

static void usage(const char *argv0)
{
    fprintf(stderr,
            "Usage: %s [--assets-dir <dir>] <map1.map> [map2.map ...]\n"
            "\n"
            "Reports map structure and used tile numbers per map, then scans asset files\n"
            "from each map directory plus optional --assets-dir directories and lists them\n"
            "ordered by type and size (bytes).\n",
            argv0);
}

int main(int argc, char **argv)
{
    std::vector<std::string> mapPaths;
    std::vector<std::string> assetDirs;

    for (int i = 1; i < argc; ++i)
    {
        if (!strcmp(argv[i], "-h") || !strcmp(argv[i], "--help"))
        {
            usage(argv[0]);
            return 0;
        }

        if (!strcmp(argv[i], "--assets-dir"))
        {
            if (i + 1 >= argc)
            {
                fprintf(stderr, "mapinfo: missing argument after --assets-dir\n");
                return 1;
            }
            assetDirs.emplace_back(argv[++i]);
            continue;
        }

        if (argv[i][0] == '-')
        {
            fprintf(stderr, "mapinfo: unknown option '%s'\n", argv[i]);
            return 1;
        }

        mapPaths.emplace_back(argv[i]);
    }

    if (mapPaths.empty())
    {
        usage(argv[0]);
        return 1;
    }

    std::vector<uint8_t> globalUsed((size_t)MAXTILES, 0);
    std::vector<MapReport> reports;
    reports.reserve(mapPaths.size());

    int mapErrors = 0;
    for (auto const &mapPath : mapPaths)
    {
        MapReport report;
        process_map(mapPath.c_str(), &report, globalUsed);
        if (!report.ok)
            ++mapErrors;

        reports.push_back(report);

        std::string const mapDir = dirname_of(mapPath);
        if (std::find(assetDirs.begin(), assetDirs.end(), mapDir) == assetDirs.end())
            assetDirs.push_back(mapDir);
    }

    printf("=== MAP INFO ===\n");
    for (auto const &report : reports)
    {
        printf("map: %s\n", report.path.c_str());
        printf("  file_size_bytes: %llu\n", (unsigned long long)report.fileSize);
        if (report.version >= 0)
            printf("  version: %d\n", report.version);

        if (report.ok)
        {
            printf("  sectors: %u\n", report.numSectors);
            printf("  walls: %u\n", report.numWalls);
            printf("  sprites: %u\n", report.numSprites);
            print_tile_list(report.usedTiles);
        }
        else
        {
            printf("  warning: %s\n", report.warning.c_str());
        }
    }

    std::vector<int> allUsedTiles;
    allUsedTiles.reserve(2048);
    for (int i = 0; i < MAXTILES; ++i)
        if (globalUsed[(size_t)i])
            allUsedTiles.push_back(i);

    printf("\n=== COMBINED TILE USAGE ===\n");
    print_tile_list(allUsedTiles);

    std::vector<AssetEntry> assets;
    for (auto const &dir : assetDirs)
        scan_asset_dir(dir, &assets);

    std::sort(assets.begin(), assets.end(), [](AssetEntry const &a, AssetEntry const &b)
    {
        if (a.type != b.type)
            return a.type < b.type;
        if (a.size != b.size)
            return a.size > b.size;
        return a.path < b.path;
    });

    std::vector<uint8_t> usedTileMask((size_t)MAXTILES, 0);
    for (int tile : allUsedTiles)
        if (tile >= 0 && tile < MAXTILES)
            usedTileMask[(size_t)tile] = 1;

    for (auto &asset : assets)
    {
        switch (asset.type)
        {
            case ASSET_TILE_ART:
            {
                int const artIdx = art_index_from_filename(asset.name);
                if (artIdx >= 0)
                {
                    int const lo = artIdx * 256;
                    int const hi = lo + 255;
                    for (int t = lo; t <= hi && t < MAXTILES; ++t)
                    {
                        if (t >= 0 && usedTileMask[(size_t)t])
                        {
                            asset.required = true;
                            break;
                        }
                    }
                }
                break;
            }
            case ASSET_TILE_IMAGE:
            {
                int const tile = tile_from_filename(asset.name);
                if (tile >= 0 && tile < MAXTILES && usedTileMask[(size_t)tile])
                    asset.required = true;
                break;
            }
            default:
                break;
        }
    }

    // Always include explicitly requested maps in required totals.
    for (auto const &r : reports)
    {
        for (auto &asset : assets)
        {
            if (asset.type == ASSET_MAP && asset.path == r.path)
            {
                asset.required = true;
                break;
            }
        }
    }

    TypeStat typeStats[ASSET_COUNT] = {};
    TypeStat requiredTypeStats[ASSET_COUNT] = {};
    uint64_t grandTotalBytesAllScanned = 0;
    uint64_t grandTotalBytesRequired = 0;

    for (auto const &asset : assets)
    {
        typeStats[asset.type].count++;
        typeStats[asset.type].totalBytes += asset.size;
        grandTotalBytesAllScanned += asset.size;

        if (asset.required)
        {
            requiredTypeStats[asset.type].count++;
            requiredTypeStats[asset.type].totalBytes += asset.size;
            grandTotalBytesRequired += asset.size;
        }
    }

    printf("\n=== ASSET INVENTORY (ordered by type, then size bytes desc) ===\n");
    printf("scanned_dirs:\n");
    for (auto const &dir : assetDirs)
        printf("  %s\n", dir.c_str());

    for (int t = 0; t < ASSET_COUNT; ++t)
    {
        printf("\n[%s] count=%d total_bytes=%llu required_count=%d required_total_bytes=%llu\n",
               g_assetTypeName[t],
               typeStats[t].count,
               (unsigned long long)typeStats[t].totalBytes,
               requiredTypeStats[t].count,
               (unsigned long long)requiredTypeStats[t].totalBytes);

        for (auto const &asset : assets)
        {
            if (asset.type != t)
                continue;

            printf("  %12llu  %c  %s\n",
                   (unsigned long long)asset.size,
                   asset.required ? '*' : ' ',
                   asset.path.c_str());
        }
    }

    printf("\n=== TOTALS ===\n");
    printf("GRAND_TOTAL_BYTES_ALL_SCANNED=%llu\n", (unsigned long long)grandTotalBytesAllScanned);
    printf("GRAND_TOTAL_BYTES_REQUIRED=%llu\n", (unsigned long long)grandTotalBytesRequired);

    return mapErrors == 0 ? 0 : 2;
}

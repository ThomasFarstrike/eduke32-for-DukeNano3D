/*
 Copyright (C) 2009 Jonathon Fowler <jf@jonof.id.au>
 Copyright (C) 2015 EDuke32 developers
 Copyright (C) 2015 Voidpoint, LLC

 This program is free software; you can redistribute it and/or
 modify it under the terms of the GNU General Public License
 as published by the Free Software Foundation; either version 2
 of the License, or (at your option) any later version.

 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

 See the GNU General Public License for more details.

 You should have received a copy of the GNU General Public License
 along with this program; if not, write to the Free Software
 Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

 */

/**
 * Raw, WAV, and VOC source support for MultiVoc
 */

#include "_multivc.h"
#include "compat.h"
#include "multivoc.h"
#include "pitch.h"
#include "pragmas.h"

#include <vector>

static playbackstatus MV_GetNextWAVBlock(VoiceNode *voice)
{
    if (voice->BlockLength == 0)
    {
        if (voice->Loop.Start == nullptr)
            return NoMoreData;

        voice->BlockLength = voice->Loop.Size;
        voice->NextBlock   = voice->Loop.Start;
        voice->length      = 0;
        voice->position    = 0;
    }

    voice->sound        = voice->NextBlock;
    voice->position    -= voice->length;
    voice->length       = min(voice->BlockLength, 0x8000u);
    voice->NextBlock   += voice->length * ((voice->channels * voice->bits) >> 3);
    voice->BlockLength -= voice->length;
    voice->length     <<= 16;

    return KeepPlaying;
}

static playbackstatus MV_GetNextVOCBlock(VoiceNode *voice)
{
    size_t   blocklength = 0;
    uint32_t samplespeed = 0;  // XXX: compiler-happy on synthesis
    uint32_t tc          = 0;
    unsigned BitsPerSample;
    unsigned Channels;
    unsigned Format;

    if (voice->BlockLength > 0)
    {
        voice->position    -= voice->length;
        voice->sound       += (voice->length >> 16) * ((voice->channels * voice->bits) >> 3);
        voice->length       = min(voice->BlockLength, 0x8000u);
        voice->BlockLength -= voice->length;
        voice->length     <<= 16;
        return KeepPlaying;
    }

    auto ptr = (uint8_t const *)voice->NextBlock;

    voice->Paused = FALSE;

    int voicemode = 0;
    int blocktype = 0;
    int lastblocktype = 0;
    int packtype = 0;

    int done = FALSE;

    do
    {
        // Stop playing if we get a null pointer
        if (ptr == nullptr)
        {
            done = 2;
            break;
        }

        // terminator is not mandatory according to
        // http://wiki.multimedia.cx/index.php?title=Creative_Voice

        if ((uint32_t)(ptr - (uint8_t *)voice->rawdataptr) >= voice->rawdatasiz)
            blocktype = 0;  // fake a terminator
        else
            blocktype = *ptr;

        if (blocktype != 0)
            blocklength = ptr[1]|(ptr[2]<<8)|(ptr[3]<<16);
        else
            blocklength = 0;
        // would need one byte pad at end of alloc'd region:
//        blocklength = B_LITTLE32(*(uint32_t *)(ptr + 1)) & 0x00ffffff;

        ptr += 4;

        switch (blocktype)
        {
        case 0 :
end_of_data:
            // End of data
            if ((voice->Loop.Start == nullptr) ||
                    ((intptr_t) voice->Loop.Start >= ((intptr_t) ptr - 4)))
            {
                done = 2;
            }
            else
            {
                voice->NextBlock    = voice->Loop.Start;
                voice->BlockLength  = 0;
                voice->position     = 0;
                return MV_GetNextVOCBlock(voice);
            }
            break;

        case 1 :
            // Sound data block
            voice->bits  = 8;
            voice->channels = voicemode + 1;
            if (lastblocktype != 8)
            {
                tc = (uint32_t)*ptr << 8;
                packtype = *(ptr + 1);
            }

            ptr += 2;
            blocklength -= 2;

            samplespeed = 256000000L / (voice->channels * (65536 - tc));

            // Skip packed or stereo data
            if ((packtype != 0) || (voicemode != 0 && voicemode != 1))
                ptr += blocklength;
            else
                done = TRUE;

            if ((uint32_t)(ptr - (uint8_t *)voice->rawdataptr) >= voice->rawdatasiz)
                goto end_of_data;

            voicemode = 0;
            break;

        case 2 :
            // Sound continuation block
            samplespeed = voice->SamplingRate;
            done = TRUE;
            break;

        case 3 :
            // Silence
        case 4 :
            // Marker
        case 5 :
            // ASCII string
            // All not implemented.
            ptr += blocklength;
            break;

        case 6 :
            // Repeat begin
            if (voice->Loop.End == nullptr)
            {
                voice->Loop.Count = B_LITTLE16(*(uint16_t const *)ptr);
                voice->Loop.Start = (char *)((intptr_t) ptr + blocklength);
            }
            ptr += blocklength;
            break;

        case 7 :
            // Repeat end
            ptr += blocklength;
            if (lastblocktype == 6)
                voice->Loop.Count = 0;
            else
            {
                if ((voice->Loop.Count > 0) && (voice->Loop.Start != nullptr))
                {
                    ptr = (uint8_t const *) voice->Loop.Start;

                    if (voice->Loop.Count < 0xffff)
                    {
                        if (--voice->Loop.Count == 0)
                            voice->Loop.Start = nullptr;
                    }
                }
            }
            break;

        case 8 :
            // Extended block
            voice->bits  = 8;
            voice->channels = 1;
            tc = B_LITTLE16(*(uint16_t const *)ptr);
            packtype = *(ptr + 2);
            voicemode = *(ptr + 3);
            ptr += blocklength;
            break;

        case 9 :
            // New sound data block
            samplespeed = B_LITTLE32(*(uint32_t const *)ptr);
            BitsPerSample = (unsigned)*(ptr + 4);
            Channels = (unsigned)*(ptr + 5);
            Format = (unsigned)B_LITTLE16(*(uint16_t const *)(ptr + 6));

            if ((BitsPerSample == 8) && (Channels == 1 || Channels == 2) && (Format == VOC_8BIT))
            {
                ptr         += 12;
                blocklength -= 12;
                voice->bits  = 8;
                voice->channels = Channels;
                done         = TRUE;
            }
            else if ((BitsPerSample == 16) && (Channels == 1 || Channels == 2) && (Format == VOC_16BIT))
            {
                ptr         += 12;
                blocklength -= 12;
                voice->bits  = 16;
                voice->channels = Channels;
                done         = TRUE;
            }
            else
            {
                ptr += blocklength;
            }

            // CAUTION:
            //  SNAKRM.VOC is corrupt!  blocklength gets us beyond the
            //  end of the file.
            if ((uint32_t)(ptr - (uint8_t *)voice->rawdataptr) >= voice->rawdatasiz)
                goto end_of_data;

            break;

        default :
            // Unknown data.  Probably not a VOC file.
            done = 2;
            break;
        }

        lastblocktype = blocktype;
    }
    while (!done);

    if (done != 2)
    {
        voice->NextBlock    = (char const *)ptr + blocklength;
        voice->sound        = (char const *)ptr;

        // CODEDUP multivoc.c MV_SetVoicePitch
        voice->SamplingRate = samplespeed;
        voice->RateScale    = divideu64((uint64_t)voice->SamplingRate * voice->PitchScale, MV_MixRate);

        // Multiply by MV_MIXBUFFERSIZE - 1
        voice->FixedPointBufferSize = (voice->RateScale * MV_MIXBUFFERSIZE) -
                                      voice->RateScale;

        if (voice->Loop.End != nullptr)
        {
            if (blocklength > (uintptr_t)voice->Loop.End)
                blocklength = (uintptr_t)voice->Loop.End;
            else
                voice->Loop.End = (char *)blocklength;

            voice->Loop.Start = voice->sound + (uintptr_t)voice->Loop.Start;
            voice->Loop.End   = voice->sound + (uintptr_t)voice->Loop.End;
            voice->Loop.Size  = voice->Loop.End - voice->Loop.Start;
        }

        if (voice->bits == 16)
            blocklength /= 2;

        if (voice->channels == 2)
            blocklength /= 2;

        voice->position     = 0;
        voice->length       = min<uint32_t>(blocklength, 0x8000u);
        voice->BlockLength  = blocklength - voice->length;
        voice->length     <<= 16;

        MV_SetVoiceMixMode(voice);

        return KeepPlaying;
    }

    return NoMoreData;
}

static playbackstatus MV_GetNextRAWBlock(VoiceNode *voice)
{
    if (voice->BlockLength == 0)
    {
        if (voice->Loop.Start == NULL)
            return NoMoreData;

        voice->BlockLength = voice->Loop.Size;
        voice->NextBlock   = voice->Loop.Start;
        voice->length      = 0;
        voice->position    = 0;
    }

    voice->sound        = voice->NextBlock;
    voice->position    -= voice->length;
    voice->length       = min(voice->BlockLength, 0x8000u);
    voice->NextBlock   += voice->length * (voice->channels * voice->bits / 8);
    voice->BlockLength -= voice->length;
    voice->length     <<= 16;

    return KeepPlaying;
}

static inline uint16_t MV_ReadLE16(uint8_t const *p)
{
    return (uint16_t)(p[0] | (p[1] << 8));
}

static inline uint32_t MV_ReadLE32(uint8_t const *p)
{
    return (uint32_t)(p[0] | (p[1] << 8) | (p[2] << 16) | (p[3] << 24));
}

static int MV_DecodeIMAADPCMNibble(int predictor, int &index, int nibble)
{
    static int const indexTable[16] =
    {
        -1, -1, -1, -1, 2, 4, 6, 8,
        -1, -1, -1, -1, 2, 4, 6, 8,
    };

    static int const stepTable[89] =
    {
          7,     8,     9,    10,    11,    12,    13,    14,
         16,    17,    19,    21,    23,    25,    28,    31,
         34,    37,    41,    45,    50,    55,    60,    66,
         73,    80,    88,    97,   107,   118,   130,   143,
        157,   173,   190,   209,   230,   253,   279,   307,
        337,   371,   408,   449,   494,   544,   598,   658,
        724,   796,   876,   963,  1060,  1166,  1282,  1411,
       1552,  1707,  1878,  2066,  2272,  2499,  2749,  3024,
       3327,  3660,  4026,  4428,  4871,  5358,  5894,  6484,
       7132,  7845,  8630,  9493, 10442, 11487, 12635, 13899,
      15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
      32767,
    };

    int const step = stepTable[index];
    int delta = step >> 3;

    if (nibble & 1) delta += step >> 2;
    if (nibble & 2) delta += step >> 1;
    if (nibble & 4) delta += step;

    predictor += (nibble & 8) ? -delta : delta;
    predictor = clamp(predictor, INT16_MIN, INT16_MAX);

    index += indexTable[nibble & 15];
    index = clamp(index, 0, 88);

    return predictor;
}

static bool MV_DecodeWAVIMAADPCM(uint8_t const *dataPtr, uint32_t dataSize, int channels, int blockAlign,
                                 int samplesPerBlock, std::vector<int16_t> &decoded)
{
    if ((channels != 1 && channels != 2) || blockAlign <= 0 || dataSize == 0)
        return false;

    uint32_t offset = 0;

    while (offset < dataSize)
    {
        uint8_t const *block = dataPtr + offset;
        uint32_t const blockSize = min((uint32_t)blockAlign, dataSize - offset);
        int const headerSize = 4 * channels;

        if (blockSize < (uint32_t)headerSize)
            return false;

        int predictor[2] = { 0, 0 };
        int index[2] = { 0, 0 };

        for (int ch = 0; ch < channels; ++ch)
        {
            uint8_t const *hdr = block + (ch * 4);
            predictor[ch] = (int)(int16_t)MV_ReadLE16(hdr);
            index[ch] = (int)hdr[2];

            if (index[ch] > 88)
                return false;
        }

        int const encodedBytes = (int)blockSize - headerSize;
        int const derivedSamplesPerBlock = channels == 1
            ? (1 + encodedBytes * 2)
            : (1 + (encodedBytes / 8) * 8);
        if (derivedSamplesPerBlock <= 0)
            return false;

        int const blockSamplesPerChannel = (samplesPerBlock > 0) ? min(samplesPerBlock, derivedSamplesPerBlock) : derivedSamplesPerBlock;
        if (blockSamplesPerChannel <= 0)
            return false;

        for (int ch = 0; ch < channels; ++ch)
            decoded.push_back((int16_t)predictor[ch]);

        int remaining = blockSamplesPerChannel - 1;
        uint8_t const *src = block + headerSize;
        uint8_t const *srcEnd = block + blockSize;

        if (channels == 1)
        {
            while (remaining > 0)
            {
                if (src >= srcEnd)
                    return false;

                uint8_t const byte = *src++;
                predictor[0] = MV_DecodeIMAADPCMNibble(predictor[0], index[0], byte & 0x0f);
                decoded.push_back((int16_t)predictor[0]);
                if (--remaining <= 0)
                    break;

                predictor[0] = MV_DecodeIMAADPCMNibble(predictor[0], index[0], byte >> 4);
                decoded.push_back((int16_t)predictor[0]);
                --remaining;
            }
        }
        else
        {
            while (remaining > 0)
            {
                if (src + 8 > srcEnd)
                    break;

                uint8_t chBytes[2][4];
                memcpy(chBytes[0], src, 4);
                memcpy(chBytes[1], src + 4, 4);
                src += 8;

                for (int i = 0; i < 8 && remaining > 0; ++i)
                {
                    for (int ch = 0; ch < 2; ++ch)
                    {
                        uint8_t const byte = chBytes[ch][i >> 1];
                        int const nibble = (i & 1) ? (byte >> 4) : (byte & 0x0f);
                        predictor[ch] = MV_DecodeIMAADPCMNibble(predictor[ch], index[ch], nibble);
                    }

                    decoded.push_back((int16_t)predictor[0]);
                    decoded.push_back((int16_t)predictor[1]);
                    --remaining;
                }
            }
        }

        offset += blockSize;
    }

    return true;
}

int MV_PlayWAV3D(char *ptr, uint32_t length, int loophow, int pitchoffset, int angle, int distance,
                     int priority, fix16_t volume, intptr_t callbackval)
{
    if (!MV_Installed)
        return MV_Error;

    if (distance < 0)
    {
        distance  = -distance;
        angle    += MV_NUMPANPOSITIONS / 2;
    }

    int const vol = MIX_VOLUME(distance);

    // Ensure angle is within 0 - 127
    angle &= MV_MAXPANPOSITION;

    return MV_PlayWAV(ptr, length, loophow, -1, pitchoffset, max(0, 255 - distance),
        MV_PanTable[angle][vol].left, MV_PanTable[angle][vol].right, priority, volume, callbackval);
}

int MV_PlayWAV(char *ptr, uint32_t length, int loopstart, int loopend, int pitchoffset, int vol,
                   int left, int right, int priority, fix16_t volume, intptr_t callbackval)
{
    if (!MV_Installed)
        return MV_Error;

    if (length < 12 || memcmp(ptr, "RIFF", 4) != 0 || memcmp(ptr + 8, "WAVE", 4) != 0)
        return MV_SetErrorCode(MV_InvalidFile);

    uint8_t const *wavData = (uint8_t const *)ptr;
    uint8_t const *fmtData = nullptr;
    uint32_t       fmtSize = 0;
    uint8_t const *sampleData = nullptr;
    uint32_t       sampleDataSize = 0;

    for (uint32_t offset = 12; offset + 8 <= length;)
    {
        uint8_t const *chunk = wavData + offset;
        uint32_t const chunkSize = MV_ReadLE32(chunk + 4);
        uint32_t const chunkDataOffset = offset + 8;
        uint32_t const chunkPaddedSize = chunkSize + (chunkSize & 1);

        if (chunkDataOffset > length || chunkSize > length - chunkDataOffset)
            return MV_SetErrorCode(MV_InvalidFile);

        if (memcmp(chunk, "fmt ", 4) == 0)
        {
            fmtData = chunk + 8;
            fmtSize = chunkSize;
        }
        else if (memcmp(chunk, "data", 4) == 0)
        {
            sampleData = chunk + 8;
            sampleDataSize = chunkSize;
            break;
        }

        if (chunkDataOffset + chunkPaddedSize < chunkDataOffset)
            return MV_SetErrorCode(MV_InvalidFile);

        offset = chunkDataOffset + chunkPaddedSize;
    }

    if (fmtData == nullptr || fmtSize < 16 || sampleData == nullptr || sampleDataSize == 0)
        return MV_SetErrorCode(MV_InvalidFile);

    uint16_t const wFormatTag = MV_ReadLE16(fmtData + 0);
    uint16_t const nChannels = MV_ReadLE16(fmtData + 2);
    uint32_t const nSamplesPerSec = MV_ReadLE32(fmtData + 4);
    uint16_t const nBlockAlign = MV_ReadLE16(fmtData + 12);
    uint16_t const nBitsPerSample = MV_ReadLE16(fmtData + 14);

    if (nChannels != 1 && nChannels != 2)
        return MV_SetErrorCode(MV_InvalidFile);

    int      wavBits = 0;
    int      wavChannels = 0;
    void *   rawDataPtr = nullptr;
    uint32_t rawDataSize = 0;
    bool     ownsRawData = false;
    char const *nextBlock = nullptr;
    uint32_t blockLength = 0;

    if (wFormatTag == 1)
    {
        if ((nBitsPerSample != 8 && nBitsPerSample != 16) || nBlockAlign == 0)
            return MV_SetErrorCode(MV_InvalidFile);

        int pcmBlockLen = sampleDataSize;

        wavBits = nBitsPerSample;
        wavChannels = nChannels;

        if (wavBits == 16)
            pcmBlockLen /= 2;

        if (wavChannels == 2)
            pcmBlockLen /= 2;

        rawDataPtr = (void *)ptr;
        rawDataSize = length;
        ownsRawData = false;
        nextBlock = (char const *)sampleData;
        blockLength = pcmBlockLen;
    }
    else if (wFormatTag == 0x0011)
    {
        if (fmtSize < 20 || nBlockAlign == 0)
            return MV_SetErrorCode(MV_InvalidFile);

        int const samplesPerBlock = MV_ReadLE16(fmtData + 18);

        // Some encoders write a 20-byte fmt chunk (WAVEFORMATEX + wSamplesPerBlock),
        // where cbSize may be 0 even though wSamplesPerBlock is present.
        if (samplesPerBlock <= 0)
            return MV_SetErrorCode(MV_InvalidFile);

        std::vector<int16_t> decoded;
        if (!MV_DecodeWAVIMAADPCM(sampleData, sampleDataSize, nChannels, nBlockAlign, samplesPerBlock, decoded) || decoded.empty())
            return MV_SetErrorCode(MV_InvalidFile);

        size_t const decodedBytes = decoded.size() * sizeof(int16_t);
        if (decodedBytes > UINT32_MAX)
            return MV_SetErrorCode(MV_InvalidFile);

        auto pcmData = (int16_t *)Xaligned_alloc(16, decodedBytes);
        memcpy(pcmData, decoded.data(), decodedBytes);

        wavBits = 16;
        wavChannels = nChannels;
        rawDataPtr = pcmData;
        rawDataSize = (uint32_t)decodedBytes;
        ownsRawData = true;
        nextBlock = (char const *)pcmData;
        blockLength = decoded.size() / nChannels;
    }
    else
    {
        return MV_SetErrorCode(MV_InvalidFile);
    }

    auto voice = MV_AllocVoice(priority);
    if (voice == nullptr)
    {
        if (ownsRawData && rawDataPtr != nullptr)
            ALIGNED_FREE_AND_NULL(rawDataPtr);

        return MV_SetErrorCode(MV_NoVoices);
    }

    voice->wavetype = FMT_WAV;
    voice->bits = wavBits;
    voice->channels = wavChannels;
    voice->GetSound = MV_GetNextWAVBlock;
    voice->rawdataptr = rawDataPtr;
    voice->rawdatasiz = rawDataSize;
    voice->ownsRawData = ownsRawData;
    voice->position = 0;
    voice->BlockLength = blockLength;
    voice->NextBlock = nextBlock;
    voice->priority = priority;
    voice->callbackval = callbackval;
    voice->Loop.Start = loopstart >= 0 ? voice->NextBlock : nullptr;
    voice->Loop.End = nullptr;
    voice->Loop.Count = 0;
    voice->Loop.Size = loopend > 0 ? loopend - loopstart + 1 : blockLength;

    MV_SetVoicePitch(voice, nSamplesPerSec, pitchoffset);
    MV_SetVoiceVolume(voice, vol, left, right, volume);
    MV_PlayVoice(voice);

    return voice->handle;
}

int MV_PlayVOC3D(char *ptr, uint32_t length, int loophow, int pitchoffset, int angle,
                     int distance, int priority, fix16_t volume, intptr_t callbackval)
{
    if (!MV_Installed)
        return MV_Error;

    if (distance < 0)
    {
        distance  = -distance;
        angle    += MV_NUMPANPOSITIONS / 2;
    }

    int const vol = MIX_VOLUME(distance);

    // Ensure angle is within 0 - 127
    angle &= MV_MAXPANPOSITION;

    return MV_PlayVOC(ptr, length, loophow, -1, pitchoffset, max(0, 255 - distance),
        MV_PanTable[angle][vol].left, MV_PanTable[angle][vol].right, priority, volume, callbackval);
}

int MV_PlayVOC(char *ptr, uint32_t length, int loopstart, int loopend, int pitchoffset, int vol,
                   int left, int right, int priority, fix16_t volume, intptr_t callbackval)
{
    if (!MV_Installed)
        return MV_Error;

    // Make sure it looks like a valid VOC file.
    if (memcmp(ptr, "Creative Voice File", 19) != 0)
        return MV_SetErrorCode(MV_InvalidFile);

    // Request a voice from the voice pool
    auto voice = MV_AllocVoice(priority);

    if (voice == nullptr)
        return MV_SetErrorCode(MV_NoVoices);

    voice->rawdataptr  = (uint8_t *)ptr;
    voice->rawdatasiz  = length;
    voice->ownsRawData = false;
    voice->wavetype    = FMT_VOC;
    voice->bits        = 8;
    voice->channels    = 1;
    voice->GetSound    = MV_GetNextVOCBlock;
    voice->NextBlock   = ptr + B_LITTLE16(*(uint16_t *)(ptr + 0x14));
    voice->PitchScale  = PITCH_GetScale(pitchoffset);
    voice->priority    = priority;
    voice->callbackval = callbackval;
    voice->Loop        = { loopstart >= 0 ? voice->NextBlock : nullptr, nullptr, 0, (uint32_t)(loopend - loopstart + 1) };

    MV_SetVoiceVolume(voice, vol, left, right, volume);
    MV_PlayVoice(voice);

    return voice->handle;
}

int MV_PlayRAW(char *ptr, uint32_t length, int rate, char *loopstart, char *loopend, int pitchoffset, int vol,
                   int left, int right, int priority, fix16_t volume, intptr_t callbackval)
{
    if (!MV_Installed)
        return MV_Error;

    // Request a voice from the voice pool
    auto voice = MV_AllocVoice(priority);

    if (voice == nullptr)
        return MV_SetErrorCode(MV_NoVoices);

    voice->rawdataptr  = (uint8_t *)ptr;
    voice->rawdatasiz  = length;
    voice->ownsRawData = false;
    voice->wavetype    = FMT_RAW;
    voice->bits        = 8;
    voice->channels    = 1;
    voice->GetSound    = MV_GetNextRAWBlock;
    voice->NextBlock   = ptr;
    voice->position    = 0;
    voice->BlockLength = length;
    voice->PitchScale  = PITCH_GetScale(pitchoffset);
    voice->priority    = priority;
    voice->callbackval = callbackval;
    voice->Loop        = { loopstart, loopend, 0, (uint32_t)(loopend - loopstart + 1) };
    voice->volume      = volume;

    MV_SetVoicePitch(voice, rate, pitchoffset);
    MV_SetVoiceVolume(voice, vol, left, right, volume);
    MV_PlayVoice(voice);

    return voice->handle;
}

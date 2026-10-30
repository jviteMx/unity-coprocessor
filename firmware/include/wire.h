#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>

namespace wire {
constexpr int HEADER=10;
constexpr uint8_t VERSION=1, DATA=1, HELLO=2;
inline uint16_t u16(const uint8_t* p) {return uint16_t(p[0]) | (uint16_t(p[1])<<8);}
inline uint32_t u32(const uint8_t* p) {return uint32_t(u16(p)) | (uint32_t(u16(p+2))<<16);}
inline uint32_t crc32(const uint8_t* p,size_t n) {
    uint32_t crc=0xffffffffu;
    for(size_t i=0;i<n;i++) {crc^=p[i];for(int j=0;j<8;j++) crc=(crc>>1)^(0xedb88320u & (0u-(crc&1)));}
    return ~crc;
}
template<int MAX_PAYLOAD> class Parser {
    uint8_t buffer[HEADER+MAX_PAYLOAD+4]{};
    size_t used=0,target=0;
public:
    bool partial() const {return used!=0;}
    void reset() {used=target=0;}
    // 0=incomplete, 1=valid packet, -1=invalid packet. Data stays valid until next feed.
    int feed(uint8_t b) {
        if(used==0 && b!='U') return 0;
        if(used==1 && b!='C') {used=(b=='U');return 0;}
        buffer[used++]=b;
        if(used==HEADER) {
            int len=u16(buffer+4);
            if(buffer[3]!=VERSION || len>MAX_PAYLOAD) {reset();return -1;}
            target=HEADER+len+4;
        }
        if(target && used==target) {
            bool ok=crc32(buffer,target-4)==u32(buffer+target-4);
            reset();return ok?1:-1;
        }
        return 0;
    }
    uint8_t type() const {return buffer[2];}
    uint16_t length() const {return u16(buffer+4);}
    uint32_t sequence() const {return u32(buffer+6);}
    const uint8_t* payload() const {return buffer+HEADER;}
};
} // namespace wire

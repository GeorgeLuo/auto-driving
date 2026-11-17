#pragma once
#include <cstdint>

namespace sim {
inline uint32_t noise(uint32_t x, uint32_t z, uint32_t seed) {
    uint32_t n = x*374761393u + z*668265263u + seed*2246822519u;
    n = (n^(n>>13))*1274126177u;
    return n^(n>>16);
}
}

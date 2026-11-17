#pragma once
#include <cstdint>

namespace sim {
inline uint32_t noise(int x, int z, uint32_t seed) {
    uint32_t n = uint32_t(x)*374761393u + uint32_t(z)*668265263u + seed*2246822519u;
    n = (n^(n>>13))*1274126177u;
    return n^(n>>16);
}
}

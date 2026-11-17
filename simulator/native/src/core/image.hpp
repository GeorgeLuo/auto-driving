#pragma once
#include <vector>

namespace sim {
struct ImageSize { int width, height; };

// Row-major RGB triples with finite linear intensities and 255 as reference white.
struct LinearRgbImage {
    ImageSize size;
    std::vector<double> channels;
};
}

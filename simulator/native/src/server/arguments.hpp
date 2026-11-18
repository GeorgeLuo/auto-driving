#pragma once
#include <charconv>
#include <stdexcept>
#include <string>
#include <string_view>

namespace sim {
inline int integer_argument(std::string_view text, int low, int high) {
    int value;
    const auto end = text.data()+text.size();
    const auto parsed = std::from_chars(text.data(),end,value);
    if (parsed.ec != std::errc{} || parsed.ptr != end || value < low || value > high)
        throw std::invalid_argument("Expected an integer in ["+std::to_string(low)+", "+
                                    std::to_string(high)+"]: "+std::string(text));
    return value;
}
}

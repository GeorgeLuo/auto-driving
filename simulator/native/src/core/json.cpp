#include "core/json.hpp"
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace sim {
void require_keys(const Json& object, std::initializer_list<const char*> keys) {
    if (!object.is_object()) throw std::invalid_argument("Expected an object");
    for (auto it = object.begin(); it != object.end(); ++it) {
        if (std::none_of(keys.begin(), keys.end(), [&](const char* key) { return it.key() == key; }))
            throw std::invalid_argument("Unknown field: " + it.key());
    }
}
double number(const Json& object, const std::string& key, double low, double high) {
    const auto& value = object.at(key);
    if (!value.is_number() || !std::isfinite(value.get<double>()) ||
        value.get<double>() < low || value.get<double>() > high)
        throw std::invalid_argument("Invalid numeric field: " + key);
    return value.get<double>();
}
}

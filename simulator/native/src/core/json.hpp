#pragma once
#include <initializer_list>
#include <nlohmann/json.hpp>
#include <string>

namespace sim {
using Json = nlohmann::json;
void require_keys(const Json& object, std::initializer_list<const char*> keys);
double number(const Json& object, const std::string& key, double low, double high);
}

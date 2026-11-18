#pragma once
#include <filesystem>
#include <initializer_list>
#include <nlohmann/json.hpp>
#include <string>

namespace sim {
using Json = nlohmann::json;
Json read_json(const std::filesystem::path& path);
void require_keys(const Json& object, std::initializer_list<const char*> keys);
double number(const Json& object, const std::string& key, double low, double high);
}

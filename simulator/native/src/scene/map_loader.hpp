#pragma once
#include "core/json.hpp"
#include "scene/map.hpp"

namespace sim {
Map load_map(const Json& map, const AssetCatalog& assets);
void apply_legacy_materials(Map& map, const Json& materials);
}

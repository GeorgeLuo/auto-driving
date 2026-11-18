#pragma once
#include "core/json.hpp"
#include "scene/asset.hpp"

namespace sim {
std::optional<CollisionBox> read_collision(const Json& json);
}

#pragma once
#include "render.hpp"
namespace sim {
Json message(const std::string& type, const Json& request, const Json& payload);
Json capture(const World& world, const ImageRequest& request);
Json stream_frame(const World& world, const ImageRequest& request, const std::string& subscription, uint64_t dropped);
Json usage();
// Called under the world's mutex; all mutations are atomic against immutable capture copies.
Json command(World& world, const std::string& id, const Json& payload);
}

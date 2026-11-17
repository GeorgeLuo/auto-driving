#pragma once
#include "server/world.hpp"

namespace sim {
struct ImageRequest { int width = 640, height = 480; double quality = .8; };
ImageRequest image_request(const Json& options);
std::vector<unsigned char> render_rgb(const World& world, const Renderer& renderer, int width, int height);
std::vector<unsigned char> render_jpeg(const World& world, const Renderer& renderer, const ImageRequest& request);
Json message(const std::string& type, const Json& request, const Json& payload);
Json capture(const World& world, const Renderer& renderer, const ImageRequest& request);
Json stream_frame(const World& world, const Renderer& renderer, const ImageRequest& request,
                  const std::string& subscription, uint64_t dropped);
Json usage();
// Called under the world's mutex; all mutations are atomic against immutable capture copies.
Json command(World& world, const std::string& id, const Json& payload);
}

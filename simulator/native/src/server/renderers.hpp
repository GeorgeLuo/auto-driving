#pragma once
#include "render/renderer.hpp"
#include <memory>

namespace sim {
std::unique_ptr<Renderer> make_renderer(std::string_view name);
}

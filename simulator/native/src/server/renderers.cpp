#include "server/renderers.hpp"
#include "render/software/renderer.hpp"
#include <stdexcept>
#include <string>

namespace sim {
std::unique_ptr<Renderer> make_renderer(std::string_view name) {
    if (name == "software") return std::make_unique<SoftwareRenderer>();
    throw std::invalid_argument("Unknown renderer: " + std::string(name));
}
}

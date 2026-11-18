#include "server/renderers.hpp"
#include "render/software/renderer.hpp"
#include <algorithm>
#include <charconv>
#include <cstdlib>
#include <stdexcept>
#include <string>
#include <thread>

namespace sim {
namespace {
unsigned software_workers() {
    if (const char* setting = std::getenv("AUTOMA_NATIVE_RENDER_WORKERS")) {
        const std::string_view value(setting);
        unsigned count;
        const auto end = value.data()+value.size();
        const auto parsed = std::from_chars(value.data(),end,count);
        if (parsed.ec != std::errc{} || parsed.ptr != end || count < 1 || count > 256)
            throw std::invalid_argument("AUTOMA_NATIVE_RENDER_WORKERS must be an integer in [1, 256]");
        return count;
    }
    return std::max(1u,std::thread::hardware_concurrency());
}
}

std::unique_ptr<Renderer> make_renderer(std::string_view name, unsigned workers) {
    if (name == "software") return std::make_unique<SoftwareRenderer>(workers ? workers : software_workers());
    throw std::invalid_argument("Unknown renderer: " + std::string(name));
}
}

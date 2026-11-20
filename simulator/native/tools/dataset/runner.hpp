#pragma once
#include "core/json.hpp"
#include "scene/asset.hpp"
#include "server/protocol.hpp"
#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

namespace sim::dataset {

struct ControlSegment {
    int frames = 0;
    Input input;
};

struct CaptureSpec {
    ImageRequest image{320, 240, 0.8};
    int stride = 3;
    bool include_frame_zero = true;
};

struct ValidationSpec {
    double max_step_yaw_radians = 15.0 * pi / 180.0;
    double min_excitation_yaw_radians = 0.05;
};

struct EpisodeSpec {
    std::string episode_id;
    uint64_t seed = 0;
    Json map_document;
    CaptureSpec capture;
    ValidationSpec validation;
    std::vector<ControlSegment> segments;
};

struct EpisodeResult {
    std::string episode_id;
    int total_steps = 0;
    int captured_frames = 0;
    bool valid_for_turn_id = false;
    Json config;
};

EpisodeSpec load_episode_spec(const Json& spec, const std::filesystem::path& base_dir);
EpisodeSpec load_episode_spec_file(const std::filesystem::path& spec_path);
EpisodeResult generate_episode(const EpisodeSpec& spec, const std::filesystem::path& out_dir,
                               const std::shared_ptr<const AssetCatalog>& assets,
                               const Renderer& renderer);
std::string fnv1a_hex(std::string_view bytes);

} // namespace sim::dataset

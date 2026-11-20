#pragma once
#include "physics/vehicle.hpp"
#include "render/renderer.hpp"
#include "scene/map_loader.hpp"
#include "sensor/camera.hpp"

namespace sim {
struct Configuration {
    Json document;
    std::shared_ptr<const Map> map;
    VehicleSettings vehicle;
    Projection projection;
    std::shared_ptr<const CameraSampler> camera_sampler;
    double camera_height, fps;
};

struct World {
    std::string epoch;
    Configuration configuration;
    VehiclePose pose;
    StepOutcome last_step;
    Input input;
    bool playing = true;
    uint64_t frame = 0, revision = 0, command_sequence = 0, applied_command_sequence = 0;
    double simulation_time = 0;
    std::shared_ptr<const AssetCatalog> assets;

    World(const Json& document, std::shared_ptr<const AssetCatalog> catalog);
    // Used by the offline tour and its benchmark.
    void start_tour(const Json& overrides = Json::object());
    double next_step_interval() const;
    void step();
    Json configure(const Json& patch);
    CameraPose camera_pose() const;
    Json state() const;
    Json debug() const;
    Json fingerprint() const;
    Json input_json() const;
    Json identity() const;
    Json native_state() const;
private:
    std::optional<Configuration> pending_;
};

uint64_t monotonic_us();
std::string new_epoch();
}

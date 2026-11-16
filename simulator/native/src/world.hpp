#pragma once
#include <nlohmann/json.hpp>
#include <cstdint>
#include <string>
#include <vector>

namespace sim {
using Json = nlohmann::json;
constexpr double pi = 3.14159265358979323846;
struct Vec { double x = 0, z = 0; };
struct Box {
    std::string id;
    double x, z, width, depth, height, rotation;
    bool boundary;
};
struct Input {
    bool forward = false, reverse = false;
    double steering = 0, throttle = 1;
};
struct World {
    Json config;
    std::vector<Box> boxes;
    Vec position, direction;
    Input input;
    bool playing = true;
    uint64_t frame = 0, revision = 0, command_sequence = 0, applied_command_sequence = 0;
    double simulation_time = 0;
    std::string epoch;
    Json pending_config;

    explicit World(const Json& configuration);
    void step();
    Json configure(const Json& patch);
    Json state() const;
    Json debug() const;
    Json fingerprint() const;
    Json input_json() const;
    Json identity() const;
    Json native_state() const;
};
Json read_config(const std::string& path);
void validate_config(const Json& config);
double number(const Json& object, const std::string& key, double low, double high);
void require_keys(const Json& object, std::initializer_list<const char*> keys);
Vec vehicle_motion(Vec position, Vec direction, double distance, double steering, double max_angle,
                   double wheelbase, Vec& next_direction);
Vec collide(Vec end, Vec start, double columns, double rows, double radius, const std::vector<Box>& boxes);
uint64_t monotonic_us();
std::string new_epoch();
}

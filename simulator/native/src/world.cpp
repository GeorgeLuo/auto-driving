#include "world.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <fstream>
#include <limits>
#include <stdexcept>

namespace sim {
uint64_t monotonic_us() {
    return std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}
std::string new_epoch() {
    static std::atomic<uint64_t> sequence{0};
    return "native-" + std::to_string(monotonic_us()) + "-" + std::to_string(++sequence);
}
void require_keys(const Json& object, std::initializer_list<const char*> keys) {
    if (!object.is_object()) throw std::invalid_argument("Expected an object");
    for (auto it = object.begin(); it != object.end(); ++it) {
        if (std::none_of(keys.begin(), keys.end(), [&](const char* key) { return it.key() == key; }))
            throw std::invalid_argument("Unknown field: " + it.key());
    }
}
double number(const Json& object, const std::string& key, double low, double high) {
    const auto& value = object.at(key);
    if (!value.is_number() || !std::isfinite(value.get<double>()) ||
        value.get<double>() < low || value.get<double>() > high)
        throw std::invalid_argument("Invalid numeric field: " + key);
    return value.get<double>();
}
void validate_config(const Json& c) {
    require_keys(c, {"scenarioId", "map", "spawn", "vehicle", "camera", "materials", "fps"});
    if (!c.at("scenarioId").is_string() || c.at("scenarioId").get<std::string>().empty())
        throw std::invalid_argument("scenarioId must be nonempty");
    number(c, "fps", 1, 1000);
    const auto& m = c.at("map");
    require_keys(m, {"columns", "rows", "obstacles"});
    number(m, "columns", 1, 1000); number(m, "rows", 1, 1000);
    if (!m.at("obstacles").is_array() || m.at("obstacles").size() > 1000)
        throw std::invalid_argument("Expected at most 1000 obstacles");
    for (const auto& b : m.at("obstacles")) {
        require_keys(b, {"id", "x", "z", "width", "depth", "height", "rotationRadians", "boundary"});
        b.at("id").get<std::string>(); b.at("boundary").get<bool>();
        number(b, "x", -1000, 1000); number(b, "z", -1000, 1000);
        number(b, "width", .001, 2000); number(b, "depth", .001, 2000);
        number(b, "height", .001, 1000); number(b, "rotationRadians", -2*pi, 2*pi);
    }
    const auto& s = c.at("spawn");
    require_keys(s, {"x", "z", "headingRadians"});
    number(s, "x", -1000, 1000); number(s, "z", -1000, 1000);
    number(s, "headingRadians", -2*pi, 2*pi);
    const auto& v = c.at("vehicle");
    require_keys(v, {"speedUnitsPerFrame", "wheelbase", "radius", "maxSteeringAngleRadians"});
    number(v, "speedUnitsPerFrame", 0, 10); number(v, "wheelbase", .001, 10);
    number(v, "radius", .001, 10); number(v, "maxSteeringAngleRadians", 0, 70*pi/180);
    if (2*v["radius"].get<double>() >= std::min(m["columns"].get<double>(),m["rows"].get<double>()))
        throw std::invalid_argument("Vehicle radius must fit inside the map");
    const auto& camera = c.at("camera");
    require_keys(camera, {"height", "pitchDownRadians", "verticalFovDegrees", "near", "far", "barrelDistortion", "vignette"});
    number(camera, "height", .001, 10); number(camera, "pitchDownRadians", -pi/2+.01, pi/2-.01);
    number(camera, "verticalFovDegrees", 10, 150); number(camera, "near", .001, 10);
    number(camera, "far", camera.at("near").get<double>()+.001, 2000);
    number(camera, "barrelDistortion", 0, .5); number(camera, "vignette", 0, 1);
    const auto& materials = c.at("materials");
    require_keys(materials, {"floor", "obstacle", "wall", "seed", "textureScale"});
    for (const auto* key : {"floor", "obstacle", "wall"}) {
        const auto& color = materials.at(key);
        if (!color.is_array() || color.size() != 3) throw std::invalid_argument("Expected RGB color");
        for (const auto& channel : color)
            if (!channel.is_number_integer() || channel.get<double>() < 0 || channel.get<double>() > 255)
                throw std::invalid_argument("Color channels must be integers in 0..255");
    }
    if (!materials.at("seed").is_number_unsigned()) throw std::invalid_argument("seed must be unsigned");
    number(materials, "seed", 0, 4294967295.0); number(materials, "textureScale", .01, 100);
}
Json read_config(const std::string& path) {
    std::ifstream input(path);
    if (!input) throw std::invalid_argument("Cannot open config: " + path);
    Json result; input >> result; validate_config(result); return result;
}
static void validate_pose(Vec position, const Json& c) {
    double radius = c["vehicle"]["radius"];
    if (std::abs(position.x) > c["map"]["columns"].get<double>()/2-radius ||
        std::abs(position.z) > c["map"]["rows"].get<double>()/2-radius)
        throw std::invalid_argument("Vehicle pose is outside the map");
    for (const auto& b : c["map"]["obstacles"]) {
        if (b["boundary"].get<bool>()) continue;
        double x = position.x-b["x"].get<double>(), z = position.z-b["z"].get<double>();
        double cosine = std::cos(b["rotationRadians"].get<double>()), sine = std::sin(b["rotationRadians"].get<double>());
        if (std::abs(x*cosine-z*sine) < b["width"].get<double>()/2+radius-1e-12 &&
            std::abs(x*sine+z*cosine) < b["depth"].get<double>()/2+radius-1e-12)
            throw std::invalid_argument("Vehicle pose intersects obstacle " + b["id"].get<std::string>());
    }
}
World::World(const Json& c) : config(c), epoch(new_epoch()) {
    validate_config(config);
    position = {c["spawn"]["x"], c["spawn"]["z"]};
    validate_pose(position,c);
    double angle = c["spawn"]["headingRadians"];
    direction = {std::cos(angle), std::sin(angle)};
    for (const auto& b : c["map"]["obstacles"])
        boxes.push_back({b["id"], b["x"], b["z"], b["width"], b["depth"], b["height"], b["rotationRadians"], b["boundary"]});
}
Vec bicycle(Vec position, Vec direction, double distance, double steering, double max_angle,
            double wheelbase, Vec& next_direction) {
    next_direction = direction;
    Vec travel = direction;
    if (std::abs(distance) > .001 && steering != 0 && max_angle > 0) {
        double angle = std::atan2(direction.z, direction.x);
        double yaw = distance / wheelbase * std::tan(steering * max_angle);
        // Chase measures heading atan2(x,z); in our atan2(z,x) representation
        // positive Chase steering subtracts yaw (left when facing north).
        travel = {std::cos(angle - yaw/2), std::sin(angle - yaw/2)};
        next_direction = {std::cos(angle - yaw), std::sin(angle - yaw)};
    }
    return {position.x + travel.x*distance, position.z + travel.z*distance};
}
static Vec local(Vec p, const Box& b) {
    double x = p.x-b.x, z = p.z-b.z, c = std::cos(b.rotation), s = std::sin(b.rotation);
    return {x*c-z*s, x*s+z*c};
}
static double entry_fraction(Vec start, Vec end, double width, double depth) {
    double entry = -INFINITY, exit = INFINITY;
    for (auto axis : {std::array<double,3>{start.x, end.x-start.x, width/2},
                      std::array<double,3>{start.z, end.z-start.z, depth/2}}) {
        auto [a, delta, half] = axis;
        if (std::abs(delta) <= 1e-12) {
            if (a <= -half+1e-12 || a >= half-1e-12) return 1;
            continue;
        }
        double first = (-half-a)/delta, second = (half-a)/delta;
        entry = std::max(entry, std::min(first,second));
        exit = std::min(exit, std::max(first,second));
    }
    if (entry >= exit-1e-12 || exit <= 1e-12 || entry > 1) return 1;
    return std::max(0.0,entry);
}
Vec collide(Vec end, Vec start, double columns, double rows, double radius, const std::vector<Box>& boxes) {
    double fraction = 1;
    for (auto axis : {std::array<double,3>{start.x, end.x, columns/2-radius},
                      std::array<double,3>{start.z, end.z, rows/2-radius}}) {
        auto [a,b,half] = axis;
        if (b < -half && b < a) fraction = std::min(fraction,(-half-a)/(b-a));
        else if (b > half && b > a) fraction = std::min(fraction,(half-a)/(b-a));
    }
    for (const auto& b : boxes) {
        if (!b.boundary) fraction = std::min(fraction,entry_fraction(local(start,b),local(end,b),b.width+2*radius,b.depth+2*radius));
    }
    fraction = std::max(0.0,fraction);
    return {start.x+(end.x-start.x)*fraction, start.z+(end.z-start.z)*fraction};
}
void World::step() {
    if (!pending_config.is_null()) { config = std::move(pending_config); pending_config = nullptr; ++revision; }
    const auto& v = config["vehicle"];
    double sign = input.forward ? 1 : input.reverse ? -1 : 0;
    double distance = v["speedUnitsPerFrame"].get<double>() * input.throttle * sign;
    Vec next_direction;
    Vec intended = bicycle(position,direction,distance,input.steering,v["maxSteeringAngleRadians"],v["wheelbase"],next_direction);
    Vec next = collide(intended,position,config["map"]["columns"],config["map"]["rows"],v["radius"],boxes);
    double full = std::hypot(intended.x-position.x,intended.z-position.z);
    double traveled = std::hypot(next.x-position.x,next.z-position.z);
    double fraction = full > 0 ? std::min(1.0,traveled/full) : 0;
    if (fraction == 0) next_direction = direction;
    else if (fraction < 1) bicycle(position,direction,distance*fraction,input.steering,v["maxSteeringAngleRadians"],v["wheelbase"],next_direction);
    position = next; direction = next_direction; ++frame;
    applied_command_sequence = command_sequence;
    simulation_time += 1/config["fps"].get<double>();
}
Json World::configure(const Json& patch) {
    require_keys(patch,{"vehicle","camera","materials","fps"});
    Json next = pending_config.is_null() ? config : pending_config;
    next.merge_patch(patch); validate_config(next);
    validate_pose(position,next);
    if (playing) pending_config = std::move(next);
    else { config = std::move(next); pending_config = nullptr; ++revision; }
    return {{"configurationRevision",revision+(playing ? 1 : 0)}, {"effectiveFrameIndex",frame+(playing ? 1 : 0)}};
}
Json World::input_json() const {
    return {{"source","ws"},{"forward",input.forward},{"reverse",input.reverse},{"steering",input.steering},
            {"motion",input.forward ? "forward" : input.reverse ? "reverse" : "none"},{"throttle",input.throttle}};
}
Json World::identity() const { return {{"gameId","chase"},{"simulationEpoch",epoch},{"frameIndex",frame}}; }
Json World::fingerprint() const {
    return {{"gameId","chase"},{"scenarioId",config["scenarioId"]},{"simulationEpoch",epoch},
        {"playback",{{"frameIndex",frame},{"phase","running"},{"pendingAction",false}}},
        {"controlSource","ws"},{"controlInput",input_json()},{"actorId","chaser"},{"cameraId","front_camera"}};
}
Json World::state() const {
    return {{"gameId","chase"},{"backend","native"},{"playback",{{"isPlaying",playing},{"playbackRate",1}}},
        {"playSidebarSections",Json::array({{{"rows",Json::array({
            {{"id","scenario-select"},{"value",config["scenarioId"]}},
            {{"id","chaser-control-source"},{"value","ws"}}})}}})}};
}
Json World::debug() const {
    auto result = identity();
    result.update({{"scenarioId",config["scenarioId"]},{"phase","running"},{"actionApplicationPending",false},
        {"actions",{{"chaserInput",input_json()},{"chaserAction",input_json()}}},
        {"native",native_state()}});
    return result;
}
Json World::native_state() const {
    return {{"classification","non-sensor"},{"pose",{{"x",position.x},{"z",position.z},
            {"directionX",direction.x},{"directionZ",direction.z}}},
        {"settings",config},{"configurationRevision",revision},{"pendingConfiguration",pending_config},
        {"frameIdentity",identity()},{"simulationTimeSeconds",simulation_time},{"playing",playing},
        {"acceptedCommandSequence",command_sequence},{"appliedCommandSequence",applied_command_sequence}};
}
}

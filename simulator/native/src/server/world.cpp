#include "server/world.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <fstream>
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
static Configuration load_configuration(const Json& c, const AssetCatalog& assets) {
    require_keys(c, {"scenarioId", "map", "spawn", "vehicle", "camera", "materials", "fps"});
    if (!c.at("scenarioId").is_string() || c.at("scenarioId").get<std::string>().empty())
        throw std::invalid_argument("scenarioId must be nonempty");
    double fps = number(c,"fps",1,1000);
    auto map = load_map(c.at("map"),assets);

    const auto& spawn = c.at("spawn");
    require_keys(spawn,{"x","z","headingRadians"});
    number(spawn,"x",-1000,1000); number(spawn,"z",-1000,1000);
    number(spawn,"headingRadians",-2*pi,2*pi);

    const auto& v = c.at("vehicle");
    require_keys(v,{"speedUnitsPerFrame","wheelbase","radius","maxSteeringAngleRadians"});
    VehicleSettings vehicle{number(v,"speedUnitsPerFrame",0,10),number(v,"wheelbase",.001,10),
                            number(v,"radius",.001,10),number(v,"maxSteeringAngleRadians",0,70*pi/180)};
    if (2*vehicle.radius >= std::min(map.columns,map.rows))
        throw std::invalid_argument("Vehicle radius must fit inside the map");

    const auto& camera = c.at("camera");
    require_keys(camera,{"height","pitchDownRadians","verticalFovDegrees","near","far","barrelDistortion","vignette"});
    double height = number(camera,"height",.001,10);
    Projection projection{number(camera,"pitchDownRadians",-pi/2+.01,pi/2-.01),
                          number(camera,"verticalFovDegrees",10,150),number(camera,"near",.001,10),
                          number(camera,"far",camera.at("near").get<double>()+.001,2000)};
    CameraEffects effects{number(camera,"barrelDistortion",0,.5),number(camera,"vignette",0,1)};
    apply_legacy_materials(map,c.at("materials"));
    return {c,std::make_shared<const Map>(std::move(map)),vehicle,projection,effects,height,fps};
}

Json read_config(const std::string& path) {
    std::ifstream input(path);
    if (!input) throw std::invalid_argument("Cannot open config: " + path);
    Json result; input >> result; return result;
}

World::World(const Json& document, std::shared_ptr<const AssetCatalog> catalog)
    : epoch(new_epoch()), configuration(load_configuration(document,*catalog)), assets(std::move(catalog)) {
    const auto& spawn = document["spawn"];
    pose.position = {spawn["x"],spawn["z"]};
    validate_vehicle_pose(pose.position,configuration.vehicle.radius,*configuration.map);
    double angle = spawn["headingRadians"];
    pose.direction = {std::cos(angle),std::sin(angle)};
}

void World::step() {
    if (pending_) {
        configuration = std::move(*pending_);
        pending_.reset();
        ++revision;
    }
    pose = step_vehicle(pose,input,configuration.vehicle,*configuration.map);
    ++frame;
    applied_command_sequence = command_sequence;
    simulation_time += 1/configuration.fps;
}

Json World::configure(const Json& patch) {
    require_keys(patch,{"vehicle","camera","materials","fps"});
    Json next = pending_ ? pending_->document : configuration.document;
    next.merge_patch(patch);
    auto candidate = load_configuration(next,*assets);
    validate_vehicle_pose(pose.position,candidate.vehicle.radius,*candidate.map);
    if (playing) pending_ = std::move(candidate);
    else {
        configuration = std::move(candidate);
        pending_.reset();
        ++revision;
    }
    return {{"configurationRevision",revision+(playing ? 1 : 0)}, {"effectiveFrameIndex",frame+(playing ? 1 : 0)}};
}

CameraPose World::camera_pose() const {
    return {{pose.position.x,configuration.camera_height,pose.position.z},pose.direction,configuration.projection};
}

Json World::input_json() const {
    return {{"source","ws"},{"forward",input.forward},{"reverse",input.reverse},{"steering",input.steering},
            {"motion",input.forward ? "forward" : input.reverse ? "reverse" : "none"},{"throttle",input.throttle}};
}
Json World::identity() const { return {{"gameId","chase"},{"simulationEpoch",epoch},{"frameIndex",frame}}; }
Json World::fingerprint() const {
    return {{"gameId","chase"},{"scenarioId",configuration.document["scenarioId"]},{"simulationEpoch",epoch},
        {"playback",{{"frameIndex",frame},{"phase","running"},{"pendingAction",false}}},
        {"controlSource","ws"},{"controlInput",input_json()},{"actorId","chaser"},{"cameraId","front_camera"}};
}
Json World::state() const {
    return {{"gameId","chase"},{"backend","native"},{"playback",{{"isPlaying",playing},{"playbackRate",1}}},
        {"playSidebarSections",Json::array({{{"rows",Json::array({
            {{"id","scenario-select"},{"value",configuration.document["scenarioId"]}},
            {{"id","chaser-control-source"},{"value","ws"}}})}}})}};
}
Json World::debug() const {
    auto result = identity();
    result.update({{"scenarioId",configuration.document["scenarioId"]},{"phase","running"},{"actionApplicationPending",false},
        {"actions",{{"chaserInput",input_json()},{"chaserAction",input_json()}}},
        {"native",native_state()}});
    return result;
}
Json World::native_state() const {
    return {{"classification","non-sensor"},{"pose",{{"x",pose.position.x},{"z",pose.position.z},
            {"directionX",pose.direction.x},{"directionZ",pose.direction.z}}},
        {"settings",configuration.document},{"configurationRevision",revision},{"pendingConfiguration",pending_ ? pending_->document : Json(nullptr)},
        {"frameIdentity",identity()},{"simulationTimeSeconds",simulation_time},{"playing",playing},
        {"acceptedCommandSequence",command_sequence},{"appliedCommandSequence",applied_command_sequence}};
}
}

#include "protocol.hpp"
#include <stdexcept>

namespace sim {
static const Json preserved_fields = {"gameId","scenarioId","simulationEpoch","playback", "controlSource","controlInput","actorId","cameraId"};
Json message(const std::string& type, const Json& request, const Json& payload) {
    Json result = {{"type",type},{"payload",payload}};
    if (request.contains("request_id")) result["request_id"] = request["request_id"];
    return result;
}
Json capture(const World& world, const ImageRequest& request) {
    auto fingerprint = world.fingerprint();
    return {{"contractVersion",1},{"captureId",world.epoch+":"+std::to_string(world.frame)+":"+std::to_string(world.revision)},
        {"actorId","chaser"},{"frameIdentity",world.identity()},{"playback",{{"advanced",false}}},
        {"configurationRevision",world.revision},{"sensor",sensor_image(world,request)},
        {"evaluator",{{"classification","non-sensor"}}},
        {"passiveObservation",{{"supported",true},{"queryId","atomic-evaluation-capture"},
            {"actorId","chaser"},{"cameraId","front_camera"},{"preservedFields",preserved_fields},
            {"preservation",{{"preserved",true},{"before",fingerprint},{"after",fingerprint}}}}}};
}
Json stream_frame(const World& world, const ImageRequest& request, const std::string& subscription, uint64_t dropped) {
    auto source_time = monotonic_us();
    auto sensor = sensor_image(world,request);
    return {{"subscriptionId",subscription},{"actorId","chaser"},{"cameraId","front_camera"},
        {"frameIdentity",world.identity()},{"configurationRevision",world.revision},
        {"simulationTimeSeconds",world.simulation_time},{"appliedCommandSequence",world.applied_command_sequence},
        {"sourceTimestampUs",source_time},{"publishedAtUs",monotonic_us()},
        {"playback",{{"advanced",false}}},{"droppedFrameCount",dropped},{"sensor",std::move(sensor)}};
}
Json usage() {
    Json camera = {{"supported",true},
        {"actors",{"chaser"}},{"cameras",{"front_camera"}},{"imageFormat","image/jpeg"},
        {"dropPolicies",{"latest-frame"}},{"bounds",{{"width",{80,640}},{"height",{60,480}},{"maxRateHz",{1,30}}}}};
    return {{"gameId","chase"},{"backend","native"},{"protocol",{{"cameraStream",camera}}},
        {"commands",{"set-chaser-input","set-chaser-control-source","configure","load-map","reset","step"}},
        {"queries",{"atomic-evaluation-capture","native-state"}}};
}
Json command(World& world, const std::string& id, const Json& payload) {
    if (id == "set-chaser-input") {
        require_keys(payload,{"motion","forward","reverse","steering","throttle"});
        Input next;
        if (payload.contains("motion")) {
            auto motion = payload.at("motion").get<std::string>();
            if (motion != "none" && motion != "forward" && motion != "reverse") throw std::invalid_argument("Invalid motion");
            next.forward = motion == "forward"; next.reverse = motion == "reverse";
            if ((payload.contains("forward") && payload["forward"].get<bool>() != next.forward) ||
                (payload.contains("reverse") && payload["reverse"].get<bool>() != next.reverse))
                throw std::invalid_argument("motion and direction flags disagree");
        } else {
            next.forward = payload.value("forward",false); next.reverse = payload.value("reverse",false);
        }
        if (next.forward && next.reverse) throw std::invalid_argument("Conflicting directions");
        next.steering = payload.contains("steering") ? number(payload,"steering",-1,1) : 0;
        next.throttle = payload.contains("throttle") ? number(payload,"throttle",0,1) : 1;
        world.input = next;
        ++world.command_sequence;
        return {{"commandSequence",world.command_sequence},{"effectiveFrameIndex",world.frame+1}};
    }
    if (id == "set-chaser-control-source") {
        require_keys(payload,{"source"});
        if (payload.at("source") != "ws") throw std::invalid_argument("Native prototype supports only ws control");
        return {{"source","ws"}};
    }
    if (id == "configure") return world.configure(payload);
    if (id == "load-map") {
        if (world.playing) throw std::invalid_argument("Pause before replacing the map");
        World replacement(payload); replacement.playing = false; world = std::move(replacement);
        return world.identity();
    }
    if (id == "reset") {
        bool playing = world.playing;
        World replacement(world.config); replacement.playing = playing; world = std::move(replacement);
        return world.identity();
    }
    if (id == "step") {
        require_keys(payload,{"frames"});
        if (world.playing) throw std::invalid_argument("Pause before explicit stepping");
        if (!payload.at("frames").is_number_integer()) throw std::invalid_argument("frames must be an integer");
        auto count = int(number(payload,"frames",1,20000));
        for (int i=0;i<count;++i) world.step();
        return world.identity();
    }
    throw std::invalid_argument("Unsupported command: " + id);
}
}

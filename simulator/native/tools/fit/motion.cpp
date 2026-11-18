// Replay sparse camera intervals with the same steering and swept contacts as World::step.
#include "scene/asset_loader.hpp"
#include "server/world.hpp"
#include <cmath>
#include <iostream>
#include <stdexcept>

int main(int argc, char** argv) {
    try {
        if (argc != 2) throw std::invalid_argument("usage: native-fit-motion MAP < intervals.json");
        auto assets = std::make_shared<const sim::AssetCatalog>(sim::load_asset_catalog(SIM_ASSETS));
        sim::World world(sim::read_json(argv[1]), std::move(assets));
        auto intervals = sim::Json::parse(std::cin);
        for (const auto& interval : intervals) {
            const auto& start = interval.at("start");
            double heading = start.at(2);
            world.pose = {{start.at(0), start.at(1)}, {std::cos(heading), std::sin(heading)}};
            for (const auto& command : interval.at("commands")) {
                double throttle = command.at("throttle");
                // The recorded Donkey positive steering turns right. Chase positive turns left.
                world.input = {throttle > 0, throttle < 0, -command.at("steering").get<double>(), std::abs(throttle)};
                for (int i = 0; i < command.at("steps").get<int>(); ++i) world.step();
            }
            auto pose = world.pose;
            std::cout << sim::Json::array({pose.position.x, pose.position.z,
                std::atan2(pose.direction.z, pose.direction.x)}).dump() << '\n';
        }
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

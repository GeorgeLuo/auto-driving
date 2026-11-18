#include "stress.hpp"
#include "scene/asset_loader.hpp"
#include "sensor/jpeg.hpp"
#include "server/protocol.hpp"
#include "server/renderers.hpp"
#include <algorithm>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <stdexcept>

namespace {
using Clock = std::chrono::steady_clock;
constexpr int tour_frames = 140, capture_interval = 3;
constexpr std::array<std::string_view,4> maps{
    "chaser-depth-obstacles","showcase-boxes","showcase","stress"
};
struct Options {
    int frames = tour_frames, warmup = 12;
    sim::ImageRequest image;
    std::string_view map;
    bool full_only = false, hash = false;
};

int integer(std::string_view value, int low, int high) {
    int result;
    const auto parsed = std::from_chars(value.data(),value.data()+value.size(),result);
    if (parsed.ec != std::errc{} || parsed.ptr != value.data()+value.size() || result < low || result > high)
        throw std::invalid_argument("Integer out of range: " + std::string(value));
    return result;
}

Options parse(int argc, char** argv) {
    Options options;
    for (int i=1;i<argc;++i) {
        std::string_view flag = argv[i];
        auto value = [&]() -> std::string_view {
            if (++i == argc) throw std::invalid_argument("Missing value for " + std::string(flag));
            return argv[i];
        };
        if (flag == "--help") {
            std::cout << "usage: native-sim-bench [--map chaser-depth-obstacles|showcase-boxes|showcase|stress|FILE]\n"
                         "                        [--frames N] [--warmup N] [--width W] [--height H]\n"
                         "                        [--full-only] [--hash]\n";
            std::exit(0);
        }
        if (flag == "--map") options.map = value();
        else if (flag == "--frames") options.frames = integer(value(),1,100000);
        else if (flag == "--warmup") options.warmup = integer(value(),0,100000);
        else if (flag == "--width") options.image.width = integer(value(),1,640);
        else if (flag == "--height") options.image.height = integer(value(),1,480);
        else if (flag == "--full-only") options.full_only = true;
        else if (flag == "--hash") options.hash = true;
        else throw std::invalid_argument("Unknown argument: " + std::string(flag));
    }
    return options;
}

std::vector<sim::CameraPose> tour_poses(sim::World world, int count) {
    world.input = {true,false,.57,1};
    const int captures = std::max(count,tour_frames);
    std::vector<sim::CameraPose> poses;
    poses.reserve(count);
    for (int capture=0;capture<captures;++capture) {
        world.step();
        // Sample evenly across the complete tour when using fewer than 140 poses.
        if (capture == int(int64_t(poses.size())*captures/count)) poses.push_back(world.camera_pose());
        for (int i=1;i<capture_interval;++i) world.step();
    }
    return poses;
}

sim::Map without_textures(sim::Map scene) {
    for (auto& entry : scene.materials) entry.second.texture = {};
    std::unordered_map<const sim::Asset*,std::shared_ptr<const sim::Asset>> stripped;
    for (auto& object : scene.objects) {
        auto& asset = stripped[object.asset.get()];
        if (!asset) {
            auto copy = std::make_shared<sim::Asset>(*object.asset);
            for (auto& part : copy->visuals)
                if (part.embedded_material) part.embedded_material->texture = {};
            asset = std::move(copy);
        }
        object.asset = asset;
    }
    return scene;
}

double milliseconds(Clock::time_point start, Clock::time_point end) {
    return std::chrono::duration<double,std::milli>(end-start).count();
}

void summary(std::string_view map, const char* stage, std::vector<double> values) {
    std::sort(values.begin(),values.end());
    const size_t n = values.size();
    const double median = (values[(n-1)/2]+values[n/2])/2;
    const double p90 = values[(9*n+9)/10-1];
    if (map.find_first_of(",\"\n\r") == std::string_view::npos) {
        std::cout << map;
    } else {
        std::cout << '"';
        for (char c : map) {
            if (c == '"') std::cout << '"';
            std::cout << c;
        }
        std::cout << '"';
    }
    std::cout << ',' << stage << ',' << n << ',' << median << ',' << p90 << '\n';
}

void hash_bytes(uint64_t& hash, const std::vector<unsigned char>& bytes) {
    for (auto byte : bytes) hash = (hash ^ byte)*1099511628211ULL;
}

void run(std::string_view name, const std::filesystem::path& path, bool stress_scene, const Options& options,
         const std::shared_ptr<const sim::AssetCatalog>& assets) {
    sim::World world(sim::read_json(path),assets);
    if (stress_scene) {
        auto stress = std::make_shared<const sim::Map>(bench::stress_map(world));
        sim::validate_vehicle_pose(world.pose.position,world.configuration.vehicle.radius,*stress);
        auto original = world;
        original.input = world.input = {true,false,.57,1};
        world.configuration.map = stress;
        auto candidate = world;
        for (int step=0;step<std::max(tour_frames,options.frames)*capture_interval;++step) {
            original.step();
            candidate.step();
            if (original.pose.position.x != candidate.pose.position.x ||
                original.pose.position.z != candidate.pose.position.z ||
                original.pose.direction.x != candidate.pose.direction.x ||
                original.pose.direction.z != candidate.pose.direction.z)
                throw std::runtime_error("Stress geometry changed the showcase tour");
        }
    }
    const auto& scene = *world.configuration.map;
    size_t parts = 0, triangles = 0;
    for (const auto& object : scene.objects) {
        parts += object.asset->visuals.size();
        for (const auto& part : object.asset->visuals)
            if (part.mesh) triangles += part.mesh->triangles.size();
    }
    const auto size = sim::ImageSize{options.image.width,options.image.height};
    std::cerr << name << ' ' << size.width << 'x' << size.height << ", " << scene.objects.size()
              << " objects, " << parts << " parts, " << triangles << " instanced triangles, "
              << options.warmup << " warmup frames\n";
    const auto poses = tour_poses(world,options.frames);
    const auto renderer = sim::make_renderer("software");
    std::vector<double> render, camera, jpeg, total;
    uint64_t rgb_hash = 14695981039346656037ULL, jpeg_hash = rgb_hash;
    for (int i=-options.warmup;i<options.frames;++i) {
        const auto& pose = poses[i < 0 ? (i+options.warmup)%poses.size() : i];
        const auto start = Clock::now();
        auto linear = renderer->render(scene,pose,size);
        const auto rendered = Clock::now();
        auto rgb = sim::apply_camera(linear,world.configuration.camera_effects);
        const auto sampled = Clock::now();
        const auto encoded = sim::encode_jpeg(rgb,size,options.image.quality);
        const auto finish = Clock::now();
        if (i < 0) continue;
        render.push_back(milliseconds(start,rendered));
        camera.push_back(milliseconds(rendered,sampled));
        jpeg.push_back(milliseconds(sampled,finish));
        total.push_back(milliseconds(start,finish));
        if (options.hash) {
            hash_bytes(rgb_hash,rgb);
            hash_bytes(jpeg_hash,encoded);
        }
    }
    summary(name,"total",total);
    summary(name,"render",render);
    summary(name,"sensor",camera);
    summary(name,"jpeg",jpeg);
    if (options.hash)
        std::cerr << name << " tour FNV-1a RGB " << std::hex << rgb_hash
                  << ", JPEG " << jpeg_hash << std::dec << '\n';
    if (options.full_only) return;

    auto unshadowed = scene;
    for (auto& light : unshadowed.light.directionals) light.shadow_strength = 0;
    const auto flat = without_textures(unshadowed);
    auto render_pass = [&](const sim::Map& variant) {
        std::vector<double> times;
        for (int i=-options.warmup;i<options.frames;++i) {
            const auto& pose = poses[i < 0 ? (i+options.warmup)%poses.size() : i];
            const auto start = Clock::now();
            const auto image = renderer->render(variant,pose,size);
            if (i >= 0) times.push_back(milliseconds(start,Clock::now()));
        }
        return times;
    };
    const auto no_shadows = render_pass(unshadowed);
    const auto rays_and_shading = render_pass(flat);
    std::vector<double> shadows, textures;
    for (size_t i=0;i<poses.size();++i) {
        shadows.push_back(render[i]-no_shadows[i]);
        textures.push_back(no_shadows[i]-rays_and_shading[i]);
    }
    summary(name,"rays_and_flat_shading",rays_and_shading);
    summary(name,"shadows_delta",shadows);
    summary(name,"textures_delta",textures);
    std::cout << std::flush;
}

void run_bundled(std::string_view name, const Options& options,
                 const std::shared_ptr<const sim::AssetCatalog>& assets) {
    const bool stress = name == "stress";
    const auto file = std::string(stress ? "showcase" : name)+".json";
    run(name,std::filesystem::path(SIM_MAPS)/file,stress,options,assets);
}
}

int main(int argc, char** argv) {
    try {
        const auto options = parse(argc,argv);
        const auto assets = std::make_shared<const sim::AssetCatalog>(sim::load_asset_catalog(SIM_ASSETS));
        std::cout << std::fixed << std::setprecision(3) << "map,stage,frames,median_ms,p90_ms\n";
        const auto map_dir = std::filesystem::path(SIM_MAPS);
        if (options.map.empty()) {
            for (auto name : maps) run_bundled(name,options,assets);
            if (std::filesystem::exists(map_dir/"room-run6.json"))
                run_bundled("room-run6",options,assets);
        } else if (options.map == "room-run6" || std::find(maps.begin(),maps.end(),options.map) != maps.end()) {
            run_bundled(options.map,options,assets);
        } else {
            const auto path = std::filesystem::path(options.map);
            run(path.stem().string(),path,false,options,assets);
        }
    } catch (const std::exception& error) {
        std::cerr << "Native simulator benchmark: " << error.what() << '\n';
        return 1;
    }
}

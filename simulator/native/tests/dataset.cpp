#include "tools/dataset/runner.hpp"
#include "scene/asset_loader.hpp"
#include "server/renderers.hpp"
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void check(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}

void close(double actual, double expected, double tol, const std::string& label) {
    check(std::abs(actual - expected) <= tol,
          label + ": " + std::to_string(actual) + " != " + std::to_string(expected));
}

template <class F>
void rejects(F operation, const std::string& label) {
    try {
        operation();
    } catch (const std::exception&) {
        return;
    }
    throw std::runtime_error("Expected rejection: " + label);
}

std::string read_bytes(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    check(bool(input), "Cannot open " + path.string());
    std::ostringstream buffer;
    buffer << input.rdbuf();
    return buffer.str();
}

std::vector<sim::Json> read_jsonl(const std::filesystem::path& path) {
    std::ifstream input(path);
    check(bool(input), "Cannot open " + path.string());
    std::vector<sim::Json> lines;
    std::string line;
    while (std::getline(input, line)) {
        if (!line.empty()) lines.push_back(sim::Json::parse(line));
    }
    return lines;
}

void collect_keys(const sim::Json& node, std::set<std::string>& keys) {
    if (node.is_object()) {
        for (auto it = node.begin(); it != node.end(); ++it) {
            keys.insert(it.key());
            collect_keys(it.value(), keys);
        }
    } else if (node.is_array()) {
        for (const auto& item : node) collect_keys(item, keys);
    }
}

struct TempDir {
    std::filesystem::path path;
    explicit TempDir(const std::string& name)
        : path(std::filesystem::temp_directory_path() / ("native_sim_dataset_test_" + name)) {
        std::filesystem::remove_all(path);
        std::filesystem::create_directories(path);
    }
    ~TempDir() {
        std::error_code ec;
        std::filesystem::remove_all(path, ec);
    }
};

void check_no_evaluator_leakage(const sim::Json& config, const std::vector<sim::Json>& records) {
    static const std::set<std::string> forbidden = {
        "wheelbase", "maxSteeringAngleRadians", "max_steering_angle_radians",
        "speedUnitsPerFrame", "speed_units_per_frame", "speed_units_per_second",
        "v_over_wheelbase_per_s", "radius", "pitchDownRadians", "pitch_down_radians",
        "pose", "x", "z", "heading_rad", "headingRadians", "direction_x", "direction_z",
        "yaw_delta_rad", "yaw_rate_rad_s", "signed_displacement", "intended_distance",
        "travel_fraction", "contact_limited", "native-state", "native_state", "spawn",
    };

    std::set<std::string> sensor_config_keys;
    collect_keys(config.at("sensor_metadata"), sensor_config_keys);
    // Camera pixel height is allowed inside camera_intrinsics, but camera mount
    // height must live strictly under evaluator_metadata.camera_extrinsics.
    check(!config.at("sensor_metadata").contains("camera_extrinsics"),
          "sensor_metadata must not contain camera_extrinsics");
    check(!config.at("sensor_metadata").at("camera_intrinsics").contains("pitch_down_radians"),
          "camera_intrinsics must not contain pitch_down_radians");
    for (const auto& key : forbidden)
        check(!sensor_config_keys.count(key), "Leaked evaluator key in sensor_metadata: " + key);
    check(config.at("evaluator_metadata").at("classification") == "non-sensor",
          "evaluator_metadata must be marked non-sensor");

    for (const auto& record : records) {
        check(record.at("evaluator").at("classification") == "non-sensor",
              "trajectory evaluator record must be marked non-sensor");
        std::set<std::string> input_keys;
        collect_keys(record.at("identity"), input_keys);
        collect_keys(record.at("sensor"), input_keys);
        for (const auto& key : forbidden)
            check(!input_keys.count(key), "Leaked evaluator key in sensor/identity record: " + key);
    }
}

void test_determinism_and_label_correctness(const std::filesystem::path& default_map,
                                            const std::shared_ptr<const sim::AssetCatalog>& assets,
                                            const sim::Renderer& renderer) {
    const sim::Json spec_json = {
        {"schemaVersion", 1},
        {"episodeId", "det_multi_segment"},
        {"seed", 7},
        {"map", default_map.string()},
        {"overrides", {
            {"spawn", {{"x", 0.0}, {"z", 2.8}, {"headingRadians", -sim::pi / 2.0}}},
            {"vehicle", {{"wheelbase", 0.5}, {"maxSteeringAngleRadians", 0.6}, {"speedUnitsPerFrame", 0.04}}},
            {"fps", 50},
        }},
        {"capture", {{"width", 80}, {"height", 60}, {"quality", 0.8}, {"stride", 3}, {"includeFrameZero", true}}},
        {"segments", {
            {{"frames", 3}, {"motion", "none"}, {"throttle", 0.0}, {"steering", 0.5}},
            {{"frames", 3}, {"motion", "forward"}, {"throttle", 1.0}, {"steering", 0.0}},
            {{"frames", 6}, {"motion", "forward"}, {"throttle", 1.0}, {"steering", 0.5}},
            {{"frames", 6}, {"motion", "forward"}, {"throttle", 0.5}, {"steering", -0.5}},
            {{"frames", 6}, {"motion", "reverse"}, {"throttle", 1.0}, {"steering", 0.5}},
        }},
    };

    const auto spec = sim::dataset::load_episode_spec(spec_json, default_map.parent_path());
    TempDir run_a("det_a"), run_b("det_b");
    const auto res_a = sim::dataset::generate_episode(spec, run_a.path, assets, renderer);
    const auto res_b = sim::dataset::generate_episode(spec, run_b.path, assets, renderer);

    check(res_a.total_steps == 24, "Expected 24 total steps");
    check(res_a.captured_frames == 9, "Expected frame 0 plus 8 stride-3 images");
    check(res_a.valid_for_turn_id, "Expected valid_for_turn_id=true for unobstructed excited run");
    check(read_bytes(run_a.path / "config.json") == read_bytes(run_b.path / "config.json"),
          "config.json must be byte-identical across runs");
    check(read_bytes(run_a.path / "trajectory.jsonl") == read_bytes(run_b.path / "trajectory.jsonl"),
          "trajectory.jsonl must be byte-identical across runs");

    const auto records = read_jsonl(run_a.path / "trajectory.jsonl");
    check(records.size() == 25, "Expected 25 records (frames 0..24)");
    check_no_evaluator_leakage(res_a.config, records);

    // Check frame 0 alignment and passive image identity against an independent World.
    sim::World reference(spec.map_document, assets);
    reference.playing = false;
    check(records[0].at("identity").at("frame_index") == 0, "Frame 0 index");
    check(records[0].at("sensor").at("applied_control").is_null(), "Frame 0 has null applied_control");
    check(records[0].at("evaluator").at("transition").is_null(), "Frame 0 has null transition");
    check(records[0].at("sensor").at("image_path") == "images/000000.jpg", "Frame 0 captured");

    const auto expected_frame0_jpeg = sim::render_jpeg(reference, renderer, spec.capture.image);
    const auto actual_frame0_bytes = read_bytes(run_a.path / "images/000000.jpg");
    check(actual_frame0_bytes == std::string(expected_frame0_jpeg.begin(), expected_frame0_jpeg.end()),
          "Frame 0 JPEG must match passive render at exact spawn state");

    // Verify every captured JPEG matches between runs and against independent stepping.
    const double dt = 1.0 / 50.0;
    const double speed = 0.04;
    const double wheelbase = 0.5;
    const double max_angle = 0.6;
    size_t frame = 0;
    for (size_t seg = 0; seg < spec.segments.size(); ++seg) {
        reference.input = spec.segments[seg].input;
        for (int s = 0; s < spec.segments[seg].frames; ++s) {
            reference.step();
            ++frame;
            const auto& rec = records[frame];
            check(rec.at("identity").at("frame_index") == frame, "Frame index monotonic");
            check(rec.at("identity").at("segment_index") == seg, "Segment index alignment");
            close(rec.at("identity").at("simulation_time_s"), double(frame) * dt, 1e-12, "Sim time");

            // Check applied control matches transition INTO this frame.
            const auto& ctrl = rec.at("sensor").at("applied_control");
            close(ctrl.at("steering"), reference.input.steering, 1e-12, "Applied steering");
            close(ctrl.at("throttle"), reference.input.throttle, 1e-12, "Applied throttle");

            // Check exact motion equations for each segment type.
            const double sign = reference.input.forward ? 1.0 : reference.input.reverse ? -1.0 : 0.0;
            const double expected_dist = speed * reference.input.throttle * sign;
            const double expected_yaw = (std::abs(expected_dist) > 0.001 && reference.input.steering != 0.0)
                ? -(expected_dist / wheelbase * std::tan(reference.input.steering * max_angle))
                : 0.0;
            const auto& tr = rec.at("evaluator").at("transition");
            close(tr.at("signed_displacement"), expected_dist, 1e-12, "Signed displacement");
            close(tr.at("yaw_delta_rad"), expected_yaw, 1e-12, "Yaw delta equation");
            close(tr.at("yaw_rate_rad_s"), expected_yaw / dt, 1e-10, "Yaw rate equation");
            check(!tr.at("contact_limited").get<bool>(), "Expected unobstructed step");

            const auto& pose = rec.at("evaluator").at("pose");
            close(pose.at("x"), reference.pose.position.x, 1e-12, "Pose x");
            close(pose.at("z"), reference.pose.position.z, 1e-12, "Pose z");

            if (frame % 3 == 0) {
                const auto rel = rec.at("sensor").at("image_path").get<std::string>();
                check(read_bytes(run_a.path / rel) == read_bytes(run_b.path / rel),
                      "JPEG bytes must match across deterministic runs: " + rel);
                const auto ref_jpg = sim::render_jpeg(reference, renderer, spec.capture.image);
                check(read_bytes(run_a.path / rel) == std::string(ref_jpg.begin(), ref_jpg.end()),
                      "Captured JPEG must match exact frame state without hidden advance: " + rel);
            } else {
                check(rec.at("sensor").at("image_path").is_null(), "Unsampled frame has null image_path");
            }
        }
    }

    // Explicitly verify sign conventions:
    // Segment 2: forward + Chase positive steering (+0.5) turns LEFT -> negative yaw_delta_rad.
    check(records[7].at("evaluator").at("transition").at("yaw_delta_rad").get<double>() < 0.0,
          "Forward + positive Chase steering must produce negative yaw_delta_rad (left)");
    // Segment 3: forward + Chase negative steering (-0.5) turns RIGHT -> positive yaw_delta_rad.
    check(records[13].at("evaluator").at("transition").at("yaw_delta_rad").get<double>() > 0.0,
          "Forward + negative Chase steering must produce positive yaw_delta_rad (right)");
    // Segment 4: reverse + Chase positive steering (+0.5) swings nose RIGHT -> positive yaw_delta_rad.
    check(records[19].at("evaluator").at("transition").at("yaw_delta_rad").get<double>() > 0.0,
          "Reverse + positive Chase steering must produce positive yaw_delta_rad");
    // Segment 0: motion=none produces zero displacement and zero yaw even with steering=0.5.
    close(records[1].at("evaluator").at("transition").at("yaw_delta_rad"), 0.0, 1e-12, "Idle yaw");
    close(records[1].at("evaluator").at("transition").at("signed_displacement"), 0.0, 1e-12, "Idle disp");
}

void test_validity_flags(const std::filesystem::path& default_map,
                         const std::shared_ptr<const sim::AssetCatalog>& assets,
                         const sim::Renderer& renderer) {
    // 1. Contact-limited run: spawn close to south boundary and drive straight into it.
    {
        const sim::Json spec_json = {
            {"episodeId", "contact_wall"},
            {"map", default_map.string()},
            {"overrides", {
                {"spawn", {{"x", 0.0}, {"z", 3.6}, {"headingRadians", sim::pi / 2.0}}},
            }},
            {"capture", {{"width", 80}, {"height", 60}, {"stride", 5}, {"includeFrameZero", false}}},
            {"segments", {{{"frames", 15}, {"motion", "forward"}, {"throttle", 1.0}, {"steering", 0.4}}}},
        };
        TempDir out("contact");
        const auto res = sim::dataset::generate_episode(
            sim::dataset::load_episode_spec(spec_json, default_map.parent_path()), out.path, assets, renderer);
        const auto& summary = res.config.at("summary");
        check(summary.at("any_contact_limited").get<bool>(), "Wall contact must be flagged");
        check(summary.at("contact_limited_steps").get<int>() > 0, "Contact step count > 0");
        check(!res.valid_for_turn_id, "Contact-limited episode must not be valid_for_turn_id");
    }

    // 2. Excessive inter-capture yaw: huge stride at full steering exceeds 15 deg/capture.
    {
        const sim::Json spec_json = {
            {"episodeId", "excessive_yaw"},
            {"map", default_map.string()},
            {"overrides", {
                {"spawn", {{"x", 2.0}, {"z", 2.0}, {"headingRadians", -sim::pi / 2.0}}},
            }},
            {"capture", {{"width", 80}, {"height", 60}, {"stride", 12}, {"includeFrameZero", true}}},
            {"validation", {{"maxStepYawRadians", 0.25}}},
            {"segments", {{{"frames", 24}, {"motion", "forward"}, {"throttle", 1.0}, {"steering", 1.0}}}},
        };
        TempDir out("excessive_yaw");
        const auto res = sim::dataset::generate_episode(
            sim::dataset::load_episode_spec(spec_json, default_map.parent_path()), out.path, assets, renderer);
        const auto& summary = res.config.at("summary");
        check(!summary.at("any_contact_limited").get<bool>(), "No contact expected");
        check(summary.at("any_excessive_step_yaw").get<bool>(), "Large stride full-lock yaw must be flagged");
        check(summary.at("max_capture_step_yaw_rad").get<double>() > 0.7, "Expected ~0.72 rad per 12 frames");
        check(!res.valid_for_turn_id, "Excessive step yaw must invalidate episode for turn ID");
    }

    // 3. Unexcited straight run and zero-displacement idle run.
    {
        const sim::Json straight_json = {
            {"episodeId", "straight_only"},
            {"map", default_map.string()},
            {"capture", {{"width", 80}, {"height", 60}, {"stride", 5}, {"includeFrameZero", true}}},
            {"segments", {{{"frames", 10}, {"motion", "forward"}, {"throttle", 1.0}, {"steering", 0.0}}}},
        };
        TempDir out_s("straight");
        const auto res_s = sim::dataset::generate_episode(
            sim::dataset::load_episode_spec(straight_json, default_map.parent_path()), out_s.path, assets, renderer);
        check(res_s.config.at("summary").at("low_yaw_excitation").get<bool>(), "Straight run has low yaw excitation");
        check(!res_s.config.at("summary").at("zero_displacement").get<bool>(), "Straight run moved");
        check(!res_s.valid_for_turn_id, "Unexcited run is not valid_for_turn_id");

        const sim::Json idle_json = {
            {"episodeId", "idle_only"},
            {"map", default_map.string()},
            {"capture", {{"width", 80}, {"height", 60}, {"stride", 5}, {"includeFrameZero", true}}},
            {"segments", {{{"frames", 5}, {"motion", "none"}, {"throttle", 0.0}, {"steering", 0.0}}}},
        };
        TempDir out_i("idle");
        const auto res_i = sim::dataset::generate_episode(
            sim::dataset::load_episode_spec(idle_json, default_map.parent_path()), out_i.path, assets, renderer);
        check(res_i.config.at("summary").at("zero_displacement").get<bool>(), "Idle run has zero displacement");
        check(!res_i.valid_for_turn_id, "Idle run is not valid_for_turn_id");
    }
}

void test_invalid_specs_and_example(const std::filesystem::path& default_map,
                                    const std::filesystem::path& example_spec,
                                    const std::shared_ptr<const sim::AssetCatalog>& assets,
                                    const sim::Renderer& renderer) {
    const auto base = default_map.parent_path();
    const sim::Json valid = {
        {"episodeId", "valid_base"},
        {"map", default_map.string()},
        {"segments", {{{"frames", 3}, {"motion", "forward"}, {"throttle", 1.0}, {"steering", 0.2}}}},
    };

    rejects([&] { auto s = valid; s["episodeId"] = "../bad"; sim::dataset::load_episode_spec(s, base); }, "bad id");
    rejects([&] { auto s = valid; s["seed"] = -1; sim::dataset::load_episode_spec(s, base); }, "negative seed");
    rejects([&] { auto s = valid; s["schemaVersion"] = 2; sim::dataset::load_episode_spec(s, base); }, "bad schema");
    rejects([&] { auto s = valid; s["unknown"] = 1; sim::dataset::load_episode_spec(s, base); }, "unknown field");
    rejects([&] { auto s = valid; s["segments"] = sim::Json::array(); sim::dataset::load_episode_spec(s, base); }, "empty segments");
    rejects([&] { auto s = valid; s["segments"][0]["motion"] = "none"; s["segments"][0]["throttle"] = 0.5;
                  sim::dataset::load_episode_spec(s, base); }, "none motion with throttle");
    rejects([&] { auto s = valid; s["segments"][0]["steering"] = 1.5; sim::dataset::load_episode_spec(s, base); }, "bad steering");
    rejects([&] {
        auto s = valid;
        s["overrides"] = {{"spawn", {{"x", 100.0}, {"z", 0.0}, {"headingRadians", 0.0}}}};
        TempDir out("bad_spawn");
        sim::dataset::generate_episode(sim::dataset::load_episode_spec(s, base), out.path, assets, renderer);
    }, "out-of-bounds spawn");

    // Verify checked-in example spec runs cleanly and passes turn-ID validity.
    TempDir example_out("example");
    auto ex_spec = sim::dataset::load_episode_spec_file(example_spec);
    ex_spec.capture.image.width = 80;
    ex_spec.capture.image.height = 60;
    const auto ex_res = sim::dataset::generate_episode(ex_spec, example_out.path, assets, renderer);
    check(ex_res.valid_for_turn_id, "Checked-in example-spec.json must be valid_for_turn_id");
    check(ex_res.captured_frames == 36, "Example spec: frame 0 + 105/3 = 36 captured frames");
}

} // namespace

int main(int argc, char** argv) {
    try {
        check(argc == 4, "Expected default map, example spec, and asset directory paths");
        const auto assets = std::make_shared<const sim::AssetCatalog>(sim::load_asset_catalog(argv[3]));
        const auto renderer = sim::make_renderer("software");
        test_determinism_and_label_correctness(argv[1], assets, *renderer);
        test_validity_flags(argv[1], assets, *renderer);
        test_invalid_specs_and_example(argv[1], argv[2], assets, *renderer);
        std::cout << "PASS: native-sim-dataset determinism, alignment, label equations, validity flags, and boundary checks\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}

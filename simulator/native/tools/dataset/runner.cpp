#include "runner.hpp"
#include "core/math.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace sim::dataset {
namespace {

constexpr int kMaxSegments = 1000;
constexpr int kMaxSegmentFrames = 20000;
constexpr int kMaxTotalFrames = 200000;

bool valid_episode_id(std::string_view id) {
    if (id.empty() || id.size() > 128 || id == "." || id == "..") return false;
    for (unsigned char c : id) {
        bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                  (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.';
        if (!ok) return false;
    }
    return true;
}

int required_int(const Json& object, const std::string& key, int low, int high) {
    const auto& value = object.at(key);
    if (!value.is_number_integer())
        throw std::invalid_argument("Expected integer field: " + key);
    if (value.is_number_unsigned()) {
        const auto parsed = value.get<uint64_t>();
        if (high < 0 || parsed < static_cast<uint64_t>(std::max(0, low)) || parsed > static_cast<uint64_t>(high))
            throw std::invalid_argument("Integer field out of range: " + key);
        return static_cast<int>(parsed);
    }
    const auto parsed = value.get<int64_t>();
    if (parsed < low || parsed > high)
        throw std::invalid_argument("Integer field out of range: " + key);
    return static_cast<int>(parsed);
}

bool required_bool(const Json& object, const std::string& key) {
    const auto& value = object.at(key);
    if (!value.is_boolean())
        throw std::invalid_argument("Expected boolean field: " + key);
    return value.get<bool>();
}

Json resolve_map_document(const Json& map_field, const std::filesystem::path& base_dir) {
    if (map_field.is_object()) return map_field;
    if (map_field.is_string()) {
        const auto raw = map_field.get<std::string>();
        if (raw.empty()) throw std::invalid_argument("map path must be nonempty");
        std::filesystem::path path(raw);
        if (path.is_relative()) path = base_dir / path;
        return read_json(path);
    }
    throw std::invalid_argument("map must be a file path string or inline map object");
}

void apply_overrides(Json& document, const Json& overrides) {
    require_keys(overrides, {"spawn", "vehicle", "camera", "materials", "fps"});
    for (const auto* key : {"spawn", "vehicle", "camera", "materials"}) {
        if (overrides.contains(key) && !overrides.at(key).is_object())
            throw std::invalid_argument(std::string("overrides.") + key + " must be an object");
    }
    if (overrides.contains("fps") && !overrides.at("fps").is_number())
        throw std::invalid_argument("overrides.fps must be numeric");
    document.merge_patch(overrides);
}

CaptureSpec parse_capture(const Json& capture) {
    require_keys(capture, {"width", "height", "quality", "stride", "includeFrameZero"});
    CaptureSpec result;
    if (capture.contains("width")) result.image.width = required_int(capture, "width", 80, 640);
    if (capture.contains("height")) result.image.height = required_int(capture, "height", 60, 480);
    if (capture.contains("quality")) result.image.quality = number(capture, "quality", 0.4, 0.9);
    if (capture.contains("stride")) result.stride = required_int(capture, "stride", 1, kMaxSegmentFrames);
    if (capture.contains("includeFrameZero")) result.include_frame_zero = required_bool(capture, "includeFrameZero");
    return result;
}

ValidationSpec parse_validation(const Json& validation) {
    require_keys(validation, {"maxStepYawRadians", "minExcitationYawRadians"});
    ValidationSpec result;
    if (validation.contains("maxStepYawRadians"))
        result.max_step_yaw_radians = number(validation, "maxStepYawRadians", 1e-6, pi);
    if (validation.contains("minExcitationYawRadians"))
        result.min_excitation_yaw_radians = number(validation, "minExcitationYawRadians", 0.0, 1000.0);
    return result;
}

std::vector<ControlSegment> parse_segments(const Json& segments) {
    if (!segments.is_array() || segments.empty() || segments.size() > static_cast<size_t>(kMaxSegments))
        throw std::invalid_argument("segments must be a non-empty array of at most 1000 items");
    std::vector<ControlSegment> result;
    result.reserve(segments.size());
    int total_frames = 0;
    for (const auto& entry : segments) {
        require_keys(entry, {"frames", "motion", "throttle", "steering"});
        if (!entry.contains("frames") || !entry.contains("motion"))
            throw std::invalid_argument("Each segment requires frames and motion");
        int frames = required_int(entry, "frames", 1, kMaxSegmentFrames);
        total_frames += frames;
        if (total_frames > kMaxTotalFrames)
            throw std::invalid_argument("Total segment frames exceed maximum of 200000");

        if (!entry.at("motion").is_string())
            throw std::invalid_argument("segment motion must be a string");
        const auto motion = entry.at("motion").get<std::string>();
        if (motion != "none" && motion != "forward" && motion != "reverse")
            throw std::invalid_argument("Invalid segment motion: " + motion);

        Input input;
        input.forward = (motion == "forward");
        input.reverse = (motion == "reverse");
        if (entry.contains("throttle")) {
            input.throttle = number(entry, "throttle", 0.0, 1.0);
            if (motion == "none" && input.throttle > 0.0)
                throw std::invalid_argument("segment with motion=none cannot have nonzero throttle");
        } else {
            input.throttle = (motion == "none") ? 0.0 : 1.0;
        }
        input.steering = entry.contains("steering") ? number(entry, "steering", -1.0, 1.0) : 0.0;
        result.push_back({frames, input});
    }
    return result;
}

Json applied_control_json(const Input& input) {
    return {
        {"motion", input.forward ? "forward" : input.reverse ? "reverse" : "none"},
        {"forward", input.forward},
        {"reverse", input.reverse},
        {"throttle", input.throttle},
        {"steering", input.steering},
    };
}

Json pose_json(const VehiclePose& pose) {
    double heading = std::atan2(pose.direction.z, pose.direction.x);
    if (heading == 0.0) heading = 0.0;
    return {
        {"x", pose.position.x},
        {"z", pose.position.z},
        {"direction_x", pose.direction.x},
        {"direction_z", pose.direction.z},
        {"heading_rad", heading},
    };
}

Json transition_json(const StepOutcome& step, double dt_s) {
    double yaw_rate = dt_s > 0.0 ? step.yaw_delta / dt_s : 0.0;
    if (yaw_rate == 0.0) yaw_rate = 0.0;
    return {
        {"yaw_delta_rad", step.yaw_delta},
        {"yaw_rate_rad_s", yaw_rate},
        {"intended_distance", step.intended_distance},
        {"signed_displacement", step.signed_distance},
        {"travel_fraction", step.travel_fraction},
        {"contact_limited", step.contact_limited},
    };
}

void write_bytes(const std::filesystem::path& path, const std::vector<unsigned char>& bytes) {
    std::ofstream output(path, std::ios::binary);
    output.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
    output.close();
    if (!output) throw std::runtime_error("Cannot write " + path.string());
}

void write_text(const std::filesystem::path& path, const std::string& content) {
    std::ofstream output(path, std::ios::binary);
    output.write(content.data(), static_cast<std::streamsize>(content.size()));
    output.close();
    if (!output) throw std::runtime_error("Cannot write " + path.string());
}

} // namespace

std::string fnv1a_hex(std::string_view bytes) {
    uint64_t hash = 14695981039346656037ULL;
    for (unsigned char byte : bytes) hash = (hash ^ byte) * 1099511628211ULL;
    std::ostringstream out;
    out << std::hex << std::setw(16) << std::setfill('0') << hash;
    return out.str();
}

EpisodeSpec load_episode_spec(const Json& spec, const std::filesystem::path& base_dir) {
    require_keys(spec, {"schemaVersion", "episodeId", "seed", "map", "overrides", "capture", "validation", "segments"});
    if (spec.contains("schemaVersion")) {
        if (!spec.at("schemaVersion").is_number_integer() || spec.at("schemaVersion").get<int>() != 1)
            throw std::invalid_argument("Unsupported schemaVersion (expected 1)");
    }
    if (!spec.contains("episodeId") || !spec.at("episodeId").is_string())
        throw std::invalid_argument("episodeId must be a string");
    EpisodeSpec result;
    result.episode_id = spec.at("episodeId").get<std::string>();
    if (!valid_episode_id(result.episode_id))
        throw std::invalid_argument("Invalid episodeId: " + result.episode_id);

    if (spec.contains("seed")) {
        const auto& seed = spec.at("seed");
        if (seed.is_number_unsigned()) {
            result.seed = seed.get<uint64_t>();
        } else if (seed.is_number_integer() && seed.get<int64_t>() >= 0) {
            result.seed = static_cast<uint64_t>(seed.get<int64_t>());
        } else {
            throw std::invalid_argument("seed must be an unsigned integer");
        }
    }

    if (!spec.contains("map"))
        throw std::invalid_argument("Episode spec requires map");
    result.map_document = resolve_map_document(spec.at("map"), base_dir);
    if (!result.map_document.is_object())
        throw std::invalid_argument("Map document must be an object");
    // Dataset episodes run from spawn + segments, not tour; drop unused tour so
    // vehicle radius overrides only validate spawn clearance.
    result.map_document.erase("tour");
    if (spec.contains("overrides")) apply_overrides(result.map_document, spec.at("overrides"));

    if (spec.contains("capture")) result.capture = parse_capture(spec.at("capture"));
    if (spec.contains("validation")) result.validation = parse_validation(spec.at("validation"));
    if (!spec.contains("segments"))
        throw std::invalid_argument("Episode spec requires segments");
    result.segments = parse_segments(spec.at("segments"));
    return result;
}

EpisodeSpec load_episode_spec_file(const std::filesystem::path& spec_path) {
    const auto absolute = std::filesystem::absolute(spec_path);
    return load_episode_spec(read_json(absolute), absolute.parent_path());
}

EpisodeResult generate_episode(const EpisodeSpec& spec, const std::filesystem::path& out_dir,
                               const std::shared_ptr<const AssetCatalog>& assets,
                               const Renderer& renderer) {
    World world(spec.map_document, assets);
    world.playing = false;
    world.epoch = "dataset:" + spec.episode_id;

    const auto output_root = std::filesystem::absolute(out_dir);
    const auto images_dir = output_root / "images";
    std::filesystem::create_directories(images_dir);

    std::ofstream trajectory(output_root / "trajectory.jsonl", std::ios::binary);
    if (!trajectory) throw std::runtime_error("Cannot open " + (output_root / "trajectory.jsonl").string());

    int captured_frames = 0;
    int contact_limited_steps = 0;
    int excessive_step_yaw_steps = 0;
    double total_abs_yaw = 0.0;
    double net_yaw = 0.0;
    double total_abs_displacement = 0.0;
    double max_capture_step_yaw = 0.0;
    bool has_previous_capture = false;
    double yaw_since_capture = 0.0;

    auto capture_if_selected = [&](bool selected) -> Json {
        if (!selected) return nullptr;
        char filename[32];
        std::snprintf(filename, sizeof filename, "%06llu.jpg", static_cast<unsigned long long>(world.frame));
        const auto bytes = render_jpeg(world, renderer, spec.capture.image);
        write_bytes(images_dir / filename, bytes);
        ++captured_frames;
        return std::string("images/") + filename;
    };

    // Frame 0: initial spawn state before any transition.
    {
        const bool capture_zero = spec.capture.include_frame_zero;
        const Json image_path = capture_if_selected(capture_zero);
        if (capture_zero) {
            has_previous_capture = true;
            yaw_since_capture = 0.0;
        }
        const Json record = {
            {"identity", {
                {"episode_id", spec.episode_id},
                {"epoch", world.epoch},
                {"frame_index", world.frame},
                {"simulation_time_s", world.simulation_time},
                {"dt_s", 0.0},
                {"configuration_revision", world.revision},
                {"segment_index", nullptr},
            }},
            {"sensor", {
                {"image_path", image_path},
                {"applied_control", nullptr},
            }},
            {"evaluator", {
                {"classification", "non-sensor"},
                {"pose", pose_json(world.pose)},
                {"transition", nullptr},
            }},
        };
        trajectory << record.dump() << '\n';
    }

    Json segment_summaries = Json::array();
    int total_steps = 0;

    for (size_t seg_idx = 0; seg_idx < spec.segments.size(); ++seg_idx) {
        const auto& segment = spec.segments[seg_idx];
        world.input = segment.input;
        ++world.command_sequence;

        const uint64_t start_frame = world.frame + 1;
        double seg_total_yaw = 0.0;
        double seg_total_signed_disp = 0.0;
        double seg_duration = 0.0;
        double seg_max_capture_yaw = 0.0;
        bool seg_contact_limited = false;

        for (int step = 0; step < segment.frames; ++step) {
            const double dt_s = world.next_step_interval();
            world.step();
            ++total_steps;

            const auto& outcome = world.last_step;
            if (outcome.contact_limited) {
                ++contact_limited_steps;
                seg_contact_limited = true;
            }
            total_abs_yaw += std::abs(outcome.yaw_delta);
            net_yaw += outcome.yaw_delta;
            total_abs_displacement += std::abs(outcome.signed_distance);
            seg_total_yaw += outcome.yaw_delta;
            seg_total_signed_disp += outcome.signed_distance;
            seg_duration += dt_s;
            yaw_since_capture += outcome.yaw_delta;

            const bool should_capture = (world.frame % static_cast<uint64_t>(spec.capture.stride)) == 0;
            const Json image_path = capture_if_selected(should_capture);
            if (should_capture) {
                if (has_previous_capture) {
                    const double step_yaw = std::abs(yaw_since_capture);
                    max_capture_step_yaw = std::max(max_capture_step_yaw, step_yaw);
                    seg_max_capture_yaw = std::max(seg_max_capture_yaw, step_yaw);
                    if (step_yaw > spec.validation.max_step_yaw_radians) ++excessive_step_yaw_steps;
                }
                has_previous_capture = true;
                yaw_since_capture = 0.0;
            }

            const Json record = {
                {"identity", {
                    {"episode_id", spec.episode_id},
                    {"epoch", world.epoch},
                    {"frame_index", world.frame},
                    {"simulation_time_s", world.simulation_time},
                    {"dt_s", dt_s},
                    {"configuration_revision", world.revision},
                    {"segment_index", seg_idx},
                }},
                {"sensor", {
                    {"image_path", image_path},
                    {"applied_control", applied_control_json(world.input)},
                }},
                {"evaluator", {
                    {"classification", "non-sensor"},
                    {"pose", pose_json(world.pose)},
                    {"transition", transition_json(outcome, dt_s)},
                }},
            };
            trajectory << record.dump() << '\n';
        }

        double mean_yaw_rate = seg_duration > 0.0 ? seg_total_yaw / seg_duration : 0.0;
        if (mean_yaw_rate == 0.0) mean_yaw_rate = 0.0;
        segment_summaries.push_back({
            {"segment_index", seg_idx},
            {"start_frame", start_frame},
            {"end_frame", world.frame},
            {"frames", segment.frames},
            {"duration_s", seg_duration},
            {"applied_control", applied_control_json(segment.input)},
            {"evaluator", {
                {"classification", "non-sensor"},
                {"total_yaw_rad", seg_total_yaw},
                {"mean_yaw_rate_rad_s", mean_yaw_rate},
                {"total_signed_displacement", seg_total_signed_disp},
                {"any_contact_limited", seg_contact_limited},
                {"max_capture_step_yaw_rad", seg_max_capture_yaw},
            }},
        });
    }

    trajectory.close();
    if (!trajectory) throw std::runtime_error("Failed writing " + (output_root / "trajectory.jsonl").string());

    const auto& doc = world.configuration.document;
    const auto& cam_doc = doc.at("camera");
    const auto& veh = world.configuration.vehicle;
    const double aspect = double(spec.capture.image.width) / double(spec.capture.image.height);
    const double vfov_deg = world.configuration.projection.vertical_fov_degrees;
    const double hfov_deg = 2.0 * std::atan(aspect * std::tan(vfov_deg * pi / 360.0)) * 180.0 / pi;
    const std::string map_hash = fnv1a_hex(doc.at("map").dump());

    const bool any_contact = contact_limited_steps > 0;
    const bool any_excessive_yaw = excessive_step_yaw_steps > 0;
    const bool zero_displacement = total_abs_displacement <= 1e-9;
    const bool low_yaw_excitation = total_abs_yaw < spec.validation.min_excitation_yaw_radians;
    const bool valid_for_turn_id = !any_contact && !any_excessive_yaw && !zero_displacement && !low_yaw_excitation;

    const Json config = {
        {"schema_version", 1},
        {"episode_id", spec.episode_id},
        {"epoch", world.epoch},
        {"seed", spec.seed},
        {"conventions", {
            {"control_alignment", "applied_to_transition_into_frame"},
            {"steering_sign", "chase_positive_left"},
            {"yaw_sign", "positive_right_atan2_z_x"},
            {"target_regime", "scale_free_yaw_rate"},
        }},
        {"capture", {
            {"width", spec.capture.image.width},
            {"height", spec.capture.image.height},
            {"quality", spec.capture.image.quality},
            {"stride", spec.capture.stride},
            {"include_frame_zero", spec.capture.include_frame_zero},
            {"image_format", "image/jpeg"},
            {"renderer_id", renderer.id()},
        }},
        {"validation", {
            {"max_step_yaw_radians", spec.validation.max_step_yaw_radians},
            {"min_excitation_yaw_radians", spec.validation.min_excitation_yaw_radians},
        }},
        {"sensor_metadata", {
            {"fps", world.configuration.fps},
            {"camera_intrinsics", {
                {"width", spec.capture.image.width},
                {"height", spec.capture.image.height},
                {"vertical_fov_degrees", vfov_deg},
                {"horizontal_fov_degrees", hfov_deg},
                {"barrel_distortion", world.configuration.projection.barrel_distortion},
                {"vignette", cam_doc.at("vignette")},
            }},
        }},
        {"evaluator_metadata", {
            {"classification", "non-sensor"},
            {"scenario_id", doc.at("scenarioId")},
            {"map_fnv1a", map_hash},
            {"spawn", doc.at("spawn")},
            {"vehicle", {
                {"speed_units_per_frame", veh.speed},
                {"speed_units_per_second", veh.speed * world.configuration.fps},
                {"wheelbase", veh.wheelbase},
                {"radius", veh.radius},
                {"max_steering_angle_radians", veh.max_steering_angle},
                {"v_over_wheelbase_per_s", (veh.speed * world.configuration.fps) / veh.wheelbase},
            }},
            {"camera_extrinsics", {
                {"height", world.configuration.camera_height},
                {"pitch_down_radians", world.configuration.projection.pitch_down},
                {"near", world.configuration.projection.near},
                {"far", world.configuration.projection.far},
            }},
        }},
        {"summary", {
            {"total_steps", total_steps},
            {"captured_frames", captured_frames},
            {"duration_s", world.simulation_time},
            {"contact_limited_steps", contact_limited_steps},
            {"any_contact_limited", any_contact},
            {"total_abs_yaw_rad", total_abs_yaw},
            {"net_yaw_rad", net_yaw},
            {"total_abs_displacement", total_abs_displacement},
            {"max_capture_step_yaw_rad", max_capture_step_yaw},
            {"excessive_step_yaw_steps", excessive_step_yaw_steps},
            {"any_excessive_step_yaw", any_excessive_yaw},
            {"zero_displacement", zero_displacement},
            {"low_yaw_excitation", low_yaw_excitation},
            {"valid_for_turn_id", valid_for_turn_id},
            {"segments", segment_summaries},
        }},
    };

    write_text(output_root / "config.json", config.dump(2) + '\n');
    return {spec.episode_id, total_steps, captured_frames, valid_for_turn_id, config};
}

} // namespace sim::dataset

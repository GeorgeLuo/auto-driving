#include "render/software/renderer.hpp"
#include "core/noise.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <future>
#include <stdexcept>

namespace sim {
namespace {
struct RenderBox {
    const VisualBox& part;
    const Material* material;
    Vec3 position;
    double cosine, sine;
};
struct Hit { double distance; const Material* material; double light, u, v; };

static void intersect(Vec3 origin, Vec3 ray, const RenderBox& box, double near, Hit& hit) {
    const auto& b = box.part;
    double x = origin.x-box.position.x, z = origin.z-box.position.z;
    double y = origin.y-box.position.y;
    std::array<double,3> local_origin{x*box.cosine-z*box.sine, y, x*box.sine+z*box.cosine};
    std::array<double,3> local_ray{ray.x*box.cosine-ray.z*box.sine, ray.y, ray.x*box.sine+ray.z*box.cosine};
    std::array<double,3> mins{-b.width/2,0,-b.depth/2}, maxs{b.width/2,b.height,b.depth/2};
    double entry = -INFINITY, exit = INFINITY;
    int axis = 0;
    for (int i=0;i<3;++i) {
        if (std::abs(local_ray[i]) < 1e-12) {
            if (local_origin[i] < mins[i] || local_origin[i] > maxs[i]) return;
            continue;
        }
        double first = (mins[i]-local_origin[i])/local_ray[i];
        double second = (maxs[i]-local_origin[i])/local_ray[i];
        double n = std::min(first,second);
        if (n > entry) { entry = n; axis = i; }
        exit = std::min(exit,std::max(first,second));
        if (entry > exit) return;
    }
    if (entry < near || entry >= hit.distance) return;
    hit = {entry,box.material,axis == 1 ? 1.08 : axis == 0 ? .80 : .96,
        local_origin[0]+entry*local_ray[0]+local_origin[2]+entry*local_ray[2],
        y+entry*ray.y};
}
}

LinearRgbImage SoftwareRenderer::render(const Map& scene, const CameraPose& camera, ImageSize size) const {
    const int width = size.width, height = size.height;
    if (width < 1 || height < 1) throw std::invalid_argument("Invalid render dimensions");
    const auto& projection = camera.projection;
    double pitch = projection.pitch_down, cp = std::cos(pitch), sp = std::sin(pitch);
    double tan_fov = std::tan(projection.vertical_fov_degrees*pi/360);
    double aspect = double(width)/height, near = projection.near, far = projection.far;
    auto origin = camera.position;
    Vec d = camera.direction;
    double half_columns = scene.columns/2, half_rows = scene.rows/2;
    const auto& floor = scene.materials.at(scene.floor_material);
    const auto& sky = scene.materials.at("sky");
    std::vector<RenderBox> boxes;
    for (const auto& object : scene.objects) {
        const auto& pose = object.pose;
        double cosine = std::cos(pose.rotation), sine = std::sin(pose.rotation);
        for (const auto& part : object.asset->visuals) {
            Vec offset = rotate({part.offset.x,part.offset.z},cosine,sine);
            double rotation = pose.rotation+part.rotation;
            boxes.push_back({part,&scene.materials.at(part.material),
                {pose.position.x+offset.x,part.offset.y,pose.position.z+offset.z},
                std::cos(rotation),std::sin(rotation)});
        }
    }
    LinearRgbImage image{size,std::vector<double>(size_t(width)*height*3)};
    auto rows = [&](int begin, int end) {
        for (int y=begin;y<end;++y) for (int x=0;x<width;++x) {
            double nx = 2*(x+.5)/width-1, ny = 1-2*(y+.5)/height;
            double px = nx*aspect*tan_fov, py = ny*tan_fov;
            Vec3 ray{d.x*(cp+py*sp)-d.z*px, -sp+py*cp, d.z*(cp+py*sp)+d.x*px};
            Hit hit{far,nullptr,1,0,0};
            if (ray.y < -1e-12) {
                double distance = -origin.y/ray.y;
                double gx = origin.x+distance*ray.x, gz = origin.z+distance*ray.z;
                if (distance >= near && distance < far && std::abs(gx) <= half_columns && std::abs(gz) <= half_rows)
                    hit = {distance,&floor,1,gx,gz};
            }
            for (const auto& b : boxes) intersect(origin,ray,b,near,hit);
            double brightness = 1;
            const auto& material = hit.material ? *hit.material : sky;
            if (hit.material) {
                brightness *= hit.light;
                // Four nearby rays approximate the soft shadow-map footprint.
                Vec3 surface{origin.x+hit.distance*ray.x, origin.y+hit.distance*ray.y,
                            origin.z+hit.distance*ray.z};
                int blocked = 0;
                for (double dx : {-.002, .002}) for (double dz : {-.002, .002}) {
                    Vec3 light{scene.light.direction.x+dx,scene.light.direction.y,scene.light.direction.z+dz};
                    Vec3 start{surface.x+light.x*.018, surface.y+light.y*.018, surface.z+light.z*.018};
                    Hit shadow{30,nullptr,1,0,0};
                    for (const auto& box : boxes) intersect(start,light,box,0,shadow);
                    if (shadow.material) ++blocked;
                }
                brightness *= 1-scene.light.shadow_strength*blocked/4.0;
                if (material.texture.kind == TextureKind::grain) {
                    double scale = material.texture.scale;
                    uint32_t grain = noise(int(std::floor(hit.u*80/scale)),int(std::floor(hit.v*80/scale)),
                                           material.texture.seed);
                    brightness *= .92 + .16*(grain&255)/255.0;
                }
            }
            for (int channel=0;channel<3;++channel)
                image.channels[(size_t(y)*width+x)*3+channel] = material.base_color[channel]*brightness;
        }
    };
    // Independent scanlines; deterministic output regardless of task scheduling.
    std::array<std::future<void>,4> tasks;
    for (int i=0;i<4;++i) tasks[i] = std::async(std::launch::async,rows,height*i/4,height*(i+1)/4);
    for (auto& task : tasks) task.get();
    return image;
}
}

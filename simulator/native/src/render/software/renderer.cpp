#include "render/software/renderer.hpp"
#include "render/software/geometry.hpp"
#include "render/software/textures.hpp"
#include <future>
#include <stdexcept>

namespace sim {
using namespace software;

namespace {
struct ShadowRay {
    Vec3 direction, bias;
    std::array<double,3> inverse;
};

struct RenderLight {
    Vec3 direction;
    double intensity, shadow_strength;
    std::vector<ShadowRay> samples;

    explicit RenderLight(const DirectionalLight& light)
        : direction(unit(light.direction)), intensity(light.intensity), shadow_strength(light.shadow_strength) {
        if (shadow_strength == 0 || intensity == 0) return;
        auto add_sample = [&](Vec3 direction) {
            samples.push_back({direction,direction*.018,BoundsRay({},direction).inverse});
        };
        double spread = light.shadow_softness;
        if (spread == 0) {
            add_sample(light.direction);
        } else {
            for (double dx : {-spread,spread}) for (double dz : {-spread,spread})
                add_sample({light.direction.x+dx,light.direction.y,light.direction.z+dz});
        }
    }

    double transmission(Vec3 point, const Bvh& bvh, const std::vector<RenderPart>& parts) const {
        if (samples.empty()) return 1;
        static const double shadow_far = std::nextafter(30.,0.);
        int blocked = 0;
        for (const auto& sample : samples) {
            Ray ray{point+sample.bias,sample.direction};
            BoundsRay bounds_ray(ray.origin,sample.inverse);
            double far = shadow_far;
            if (bvh.intersect(bounds_ray,0,far,[&](size_t i) {
                return parts[i].intersect(ray,0,far,nullptr);
            })) ++blocked;
        }
        return 1-shadow_strength*blocked/double(samples.size());
    }
};

double lighting(const std::optional<double>& ambient, const std::vector<RenderLight>& lights,
                const Surface& surface, Vec3 point, const Bvh& bvh, const std::vector<RenderPart>& parts) {
    if (!ambient) return surface.legacy_shade*lights.front().transmission(point,bvh,parts);
    double brightness = *ambient;
    for (const auto& light : lights) {
        double incident = light.intensity*dot(surface.normal,light.direction);
        if (incident > 0) brightness += incident*light.transmission(point,bvh,parts);
    }
    return brightness;
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

    std::vector<RenderPart> parts;
    std::vector<Bounds> bounds;
    {
        // Compiled meshes are shared across frames and instances. The scene still owns the geometry.
        std::lock_guard<std::mutex> guard(mesh_mutex_);
        for (auto it = meshes_.begin();it != meshes_.end();) {
            if (it->first.expired()) it = meshes_.erase(it);
            else ++it;
        }
        for (const auto& object : scene.objects) {
            const auto& pose = object.pose;
            double cosine = std::cos(pose.rotation), sine = std::sin(pose.rotation);
            const auto* override = object.material ? &scene.materials.at(*object.material) : nullptr;
            for (const auto& part : object.asset->visuals) {
                Vec offset = rotate({part.offset.x,part.offset.z},cosine,sine);
                const auto& material = override ? *override :
                    part.embedded_material ? *part.embedded_material : scene.materials.at(part.material);
                std::shared_ptr<const MeshBvh> mesh;
                if (part.mesh) {
                    auto& compiled = meshes_[part.mesh];
                    if (!compiled) compiled = std::make_shared<const MeshBvh>(*part.mesh);
                    mesh = compiled;
                }
                parts.push_back({part,material,object.color ? *object.color : material.base_color,
                    {{pose.position.x+offset.x,part.offset.y,pose.position.z+offset.z},
                     pose.rotation+part.rotation,part.pitch,part.roll},std::move(mesh)});
                bounds.push_back(parts.back().bounds());
            }
        }
    }
    Bvh bvh(bounds);
    std::vector<RenderLight> lights;
    for (const auto& light : scene.light.directionals) lights.emplace_back(light);

    LinearRgbImage image{size,std::vector<double>(size_t(width)*height*3)};
    auto rows = [&](int begin, int end) {
        for (int y=begin;y<end;++y) for (int x=0;x<width;++x) {
            double nx = 2*(x+.5)/width-1, ny = 1-2*(y+.5)/height;
            double px = nx*aspect*tan_fov, py = ny*tan_fov;
            Ray ray{origin,{d.x*(cp+py*sp)-d.z*px,-sp+py*cp,d.z*(cp+py*sp)+d.x*px}};
            Surface surface{};
            double closest = far;
            const Material* material = nullptr;
            const std::array<int,3>* base = nullptr;
            size_t order = 0;
            if (ray.direction.y < -1e-12) {
                double distance = -origin.y/ray.direction.y;
                auto point = ray.at(distance);
                if (distance >= near && distance < far && std::abs(point.x) <= half_columns && std::abs(point.z) <= half_rows) {
                    closest = distance;
                    material = &floor;
                    base = &floor.base_color;
                    UV coords{point.x,point.z};
                    surface = {distance,{0,1,0},coords,
                               {point.x/scene.columns+.5,point.z/scene.rows+.5},coords,1,true};
                }
            }
            bvh.intersect(BoundsRay(origin,ray.direction),near,closest,[&](size_t i) {
                Surface candidate;
                const auto& part = parts[i];
                if (part.intersect(ray,near,closest,&candidate) &&
                    (candidate.distance < closest || i+1 < order)) {
                    closest = candidate.distance;
                    surface = candidate;
                    material = &part.material;
                    base = &part.color;
                    order = i+1;
                }
                return false;
            });
            size_t pixel = (size_t(y)*width+x)*3;
            if (material) {
                double brightness = lighting(scene.light.ambient,lights,surface,ray.at(closest),bvh,parts);
                auto color = texture_color(*material,*base,surface,ray.direction,brightness);
                std::copy(color.begin(),color.end(),image.channels.data()+pixel);
            } else {
                std::copy(sky.base_color.begin(),sky.base_color.end(),image.channels.data()+pixel);
            }
        }
    };
    // Independent scanlines; deterministic output regardless of task scheduling.
    std::array<std::future<void>,4> tasks;
    for (int i=0;i<4;++i) tasks[i] = std::async(std::launch::async,rows,height*i/4,height*(i+1)/4);
    for (auto& task : tasks) task.get();
    return image;
}
}

#include "render/software/renderer.hpp"
#include "core/overscan.hpp"
#include "render/software/geometry.hpp"
#include "render/software/shadow_grid.hpp"
#include "render/software/textures.hpp"
#include <stdexcept>

namespace sim {
using namespace software;

namespace {
struct ShadowRay {
    Vec3 direction;
    std::array<double,3> inverse;
    ShadowGrid grid;
};

struct RenderLight {
    Vec3 direction;
    double intensity, shadow_strength;
    std::vector<ShadowRay> samples;

    RenderLight(const DirectionalLight& light, const std::vector<Bounds>& bounds)
        : direction(unit(light.direction)), intensity(light.intensity), shadow_strength(light.shadow_strength) {
        if (shadow_strength == 0 || intensity == 0) return;
        auto add_sample = [&](Vec3 direction) {
            samples.push_back({direction,BoundsRay({},direction).inverse,ShadowGrid(bounds,direction)});
        };
        double spread = light.shadow_softness;
        if (spread == 0) {
            add_sample(light.direction);
        } else {
            for (double dx : {-spread,spread}) for (double dz : {-spread,spread})
                add_sample({light.direction.x+dx,light.direction.y,light.direction.z+dz});
        }
    }

    double transmission(Vec3 point, const Bvh& bvh, const std::vector<RenderPart>& parts,
                        const std::vector<Bounds>& bounds) const {
        if (samples.empty()) return 1;
        static const double shadow_far = std::nextafter(30.,0.);
        int blocked = 0;
        for (const auto& sample : samples) {
            const auto& light = sample.direction;
            Vec3 start{point.x+light.x*.018,point.y+light.y*.018,point.z+light.z*.018};
            const Ray ray{start,light};
            const BoundsRay bounds_ray(start,sample.inverse);
            if (sample.grid.indexed()) {
                const auto* candidates = sample.grid.candidates(start);
                if (candidates && std::any_of(candidates->begin(),candidates->end(),[&](size_t i) {
                    return bounds[i].intersects(bounds_ray,0,shadow_far) &&
                           parts[i].intersect(ray,0,shadow_far,nullptr);
                })) ++blocked;
            } else {
                double far = shadow_far;
                if (bvh.intersect(bounds_ray,0,far,[&](size_t i) {
                    return parts[i].intersect(ray,0,far,nullptr);
                })) ++blocked;
            }
        }
        return 1-shadow_strength*blocked/double(samples.size());
    }
};

double lighting(const std::optional<double>& ambient, const std::vector<RenderLight>& lights,
                const Surface& surface, Vec3 point, const Bvh& bvh, const std::vector<RenderPart>& parts,
                const std::vector<Bounds>& bounds) {
    if (!ambient) return surface.legacy_shade*lights.front().transmission(point,bvh,parts,bounds);
    double brightness = *ambient;
    for (const auto& light : lights) {
        double incident = light.intensity*dot(surface.normal,light.direction);
        if (incident > 0) brightness += incident*light.transmission(point,bvh,parts,bounds);
    }
    return brightness;
}
}

struct SoftwareRenderer::ShadowCache {
    std::weak_ptr<const Map> scene;
    std::vector<RenderLight> lights;

    ShadowCache(const std::shared_ptr<const Map>& scene, const std::vector<Bounds>& bounds) : scene(scene) {
        lights.reserve(scene->light.directionals.size());
        for (const auto& light : scene->light.directionals) lights.emplace_back(light,bounds);
    }
};

SoftwareRenderer::SoftwareRenderer(unsigned workers) : tiles_(workers) {}
SoftwareRenderer::~SoftwareRenderer() = default;

LinearRgbImage SoftwareRenderer::render(const std::shared_ptr<const Map>& map, const CameraPose& camera,
                                        ImageSize size) const {
    const int width = size.width, height = size.height;
    if (width < 1 || height < 1) throw std::invalid_argument("Invalid render dimensions");
    const std::lock_guard<std::mutex> guard(render_mutex_);
    const auto& scene = *map;
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
    // Compiled meshes are shared across frames and instances. The scene owns the geometry.
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
    const Bvh bvh(bounds);
    for (auto& box : bounds) box.pad();
    // Every committed configuration owns a new map, even when only the camera
    // changes. The weak key also rules out reuse after an address is recycled.
    if (!shadows_ || shadows_->scene.lock() != map)
        shadows_ = std::make_unique<const ShadowCache>(map,bounds);
    const auto& lights = shadows_->lights;

    const OverscanAxis horizontal(width,height,projection.barrel_distortion);
    const OverscanAxis vertical(height,width,projection.barrel_distortion);
    std::vector<double> column_x(width), row_y(height);
    for (int x=0;x<width;++x) {
        const double nx = 2*(horizontal.ray_position(x)+.5)/width-1;
        column_x[x] = nx*aspect*tan_fov;
    }
    for (int y=0;y<height;++y) {
        const double ny = 1-2*(vertical.ray_position(y)+.5)/height;
        row_y[y] = ny*tan_fov;
    }
    LinearRgbImage image{size,std::vector<double>(size_t(width)*height*3)};
    auto tile = [&](int x0, int y0, int x1, int y1) {
        for (int y=y0;y<y1;++y) for (int x=x0;x<x1;++x) {
            double px = column_x[x], py = row_y[y];
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
            const BoundsRay bounds_ray(origin,ray.direction);
            bvh.intersect(bounds_ray,near,closest,[&](size_t i) {
                if (!bounds[i].intersects(bounds_ray,near,closest)) return false;
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
                double brightness = lighting(scene.light.ambient,lights,surface,ray.at(closest),bvh,parts,bounds);
                auto color = texture_color(*material,*base,surface,ray.direction,brightness);
                std::copy(color.begin(),color.end(),image.channels.data()+pixel);
            } else {
                std::copy(sky.base_color.begin(),sky.base_color.end(),image.channels.data()+pixel);
            }
        }
    };
    constexpr int tile_width = 64, tile_height = 16;
    const int columns = (width-1)/tile_width+1, rows = (height-1)/tile_height+1;
    tiles_.run(size_t(columns)*rows,[&](size_t index) {
        const int x = int(index%columns)*tile_width, y = int(index/columns)*tile_height;
        tile(x,y,x+std::min(tile_width,width-x),y+std::min(tile_height,height-y));
    });
    return image;
}
}

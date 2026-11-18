#include "scene/asset_loader.hpp"
#include "scene/asset_json.hpp"
#include "scene/material.hpp"
#define CGLTF_IMPLEMENTATION
#include <cgltf.h>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <stdexcept>

namespace sim {
namespace {
void check(cgltf_result result, const char* operation) {
    if (result != cgltf_result_success)
        throw std::invalid_argument(std::string(operation) + " failed (cgltf error " + std::to_string(result) + ")");
}

std::shared_ptr<const TextureImage> read_gltf_image(const cgltf_image& image,
                                                    const std::filesystem::path& directory) {
    if (image.buffer_view) {
        const auto* data = cgltf_buffer_view_data(image.buffer_view);
        if (!data || !image.buffer_view->size) throw std::invalid_argument("Empty glTF image buffer");
        return load_texture_image(data,image.buffer_view->size);
    }
    if (!image.uri) throw std::invalid_argument("Missing glTF image data");
    std::string uri(image.uri);
    if (uri.compare(0,5,"data:") == 0) {
        auto comma = uri.find(',');
        if (comma == std::string::npos || comma < 7 || uri.compare(comma-7,7,";base64") != 0)
            throw std::invalid_argument("Expected base64 glTF image data");
        auto last = uri.find_last_not_of('=');
        if (last <= comma) throw std::invalid_argument("Empty glTF image data");
        size_t size = (last-comma)*6/8;
        cgltf_options options{};
        void* bytes = nullptr;
        check(cgltf_load_buffer_base64(&options,size,uri.c_str()+comma+1,&bytes),"Loading glTF image data");
        std::unique_ptr<void,decltype(&std::free)> owned(bytes,std::free);
        return load_texture_image(static_cast<const uint8_t*>(owned.get()),size);
    }
    uri.resize(cgltf_decode_uri(uri.data()));
    return load_texture_image(directory/uri);
}

std::vector<float> read_attribute(const cgltf_accessor* accessor, cgltf_type type, const char* name) {
    if (!accessor || accessor->type != type || !accessor->count || accessor->is_sparse)
        throw std::invalid_argument(std::string("Expected a non-sparse glTF ") + name + " accessor");
    std::vector<float> values(accessor->count*cgltf_num_components(type));
    if (cgltf_accessor_unpack_floats(accessor,values.data(),values.size()) != values.size() ||
        std::any_of(values.begin(),values.end(),[](float value) {
            return !std::isfinite(value) || std::abs(value) > 1e6;
        }))
        throw std::invalid_argument(std::string("Invalid glTF ") + name + " data");
    return values;
}

const cgltf_accessor* find_attribute(const cgltf_primitive& primitive, cgltf_attribute_type type) {
    for (size_t i=0;i<primitive.attributes_count;++i) {
        const auto& attribute = primitive.attributes[i];
        if (attribute.type == type && attribute.index == 0) return attribute.data;
    }
    return nullptr;
}

TextureWrap wrap_mode(cgltf_int mode) {
    switch (mode) {
    case 10497: return TextureWrap::repeat;
    case 33071: return TextureWrap::clamp;
    case 33648: return TextureWrap::mirror;
    default: throw std::invalid_argument("Unknown glTF texture wrap mode");
    }
}

struct Import {
    std::filesystem::path directory;
    std::unordered_map<const cgltf_image*,std::shared_ptr<const TextureImage>> images;

    void read_material(VisualPart& part, const cgltf_material* material) {
        part.embedded_material = Material{{255,255,255},{}};
        if (!material) return;
        if (material->extras.data) {
            auto extras = Json::parse(material->extras.data);
            if (extras.contains("nativeMaterial")) {
                part.material = extras.at("nativeMaterial").get<std::string>();
                part.embedded_material.reset();
                return;
            }
        }

        auto& result = *part.embedded_material;
        const auto& pbr = material->pbr_metallic_roughness;
        for (size_t channel=0;channel<3;++channel) {
            auto factor = pbr.base_color_factor[channel];
            if (!std::isfinite(factor) || factor < 0 || factor > 1)
                throw std::invalid_argument("Invalid glTF base color factor");
            result.base_color[channel] = std::lround(255*factor);
        }
        const auto& view = pbr.base_color_texture;
        if (!view.texture) return;
        if (!view.texture->image || view.texcoord != 0 || view.has_transform)
            throw std::invalid_argument("Expected a glTF base color image using TEXCOORD_0");
        auto& image = images[view.texture->image];
        if (!image) image = read_gltf_image(*view.texture->image,directory);
        result.texture.kind = TextureKind::image;
        result.texture.image = image;
        if (const auto* sampler = view.texture->sampler) {
            result.texture.wrap_u = wrap_mode(sampler->wrap_s);
            result.texture.wrap_v = wrap_mode(sampler->wrap_t);
        }
    }

    VisualPart read_primitive(const cgltf_primitive& primitive, const float* transform) {
        auto* indices = primitive.indices;
        if (primitive.type != cgltf_primitive_type_triangles || primitive.has_draco_mesh_compression ||
            primitive.targets_count || (indices && (indices->is_sparse || !indices->buffer_view ||
                                                    !cgltf_buffer_view_data(indices->buffer_view))))
            throw std::invalid_argument("Expected an uncompressed, rigid glTF triangle list");

        const auto* accessor = find_attribute(primitive,cgltf_attribute_type_position);
        auto vertices = read_attribute(accessor,cgltf_type_vec3,"POSITION");
        auto mesh = std::make_shared<TriangleMesh>();
        mesh->positions.reserve(accessor->count);
        for (size_t i=0;i<vertices.size();i+=3) {
            double x = vertices[i], y = vertices[i+1], z = vertices[i+2];
            Vec3 position{
                transform[0]*x+transform[4]*y+transform[8]*z+transform[12],
                transform[1]*x+transform[5]*y+transform[9]*z+transform[13],
                transform[2]*x+transform[6]*y+transform[10]*z+transform[14],
            };
            if (!(std::abs(position.x) <= 1e6 && std::abs(position.y) <= 1e6 && std::abs(position.z) <= 1e6))
                throw std::invalid_argument("Invalid transformed glTF vertex");
            mesh->positions.push_back(position);
        }

        if (const auto* uv = find_attribute(primitive,cgltf_attribute_type_texcoord)) {
            auto values = read_attribute(uv,cgltf_type_vec2,"TEXCOORD_0");
            mesh->uv.reserve(uv->count);
            for (size_t i=0;i<values.size();i+=2) mesh->uv.push_back({values[i],values[i+1]});
        }

        size_t count = primitive.indices ? primitive.indices->count : mesh->positions.size();
        if (!count || count % 3) throw std::invalid_argument("Incomplete glTF triangle");
        mesh->triangles.reserve(count/3);
        for (size_t i=0;i<count;i+=3) {
            std::array<uint32_t,3> triangle;
            for (size_t corner=0;corner<3;++corner) {
                size_t index = primitive.indices ? cgltf_accessor_read_index(primitive.indices,i+corner) : i+corner;
                if (index >= mesh->positions.size() || index > UINT32_MAX)
                    throw std::invalid_argument("Invalid glTF triangle index");
                triangle[corner] = static_cast<uint32_t>(index);
            }
            mesh->triangles.push_back(triangle);
        }

        VisualPart part;
        part.shape = Shape::mesh;
        read_material(part,primitive.material);
        if (part.embedded_material && part.embedded_material->texture.image && mesh->uv.empty())
            throw std::invalid_argument("Textured glTF primitive is missing TEXCOORD_0");
        part.mesh = std::move(mesh);
        return part;
    }
};
}

bool GltfAssetLoader::supports(const std::filesystem::path& source) const {
    return source.extension() == ".gltf" || source.extension() == ".glb";
}

Asset GltfAssetLoader::load(const std::filesystem::path& source) const {
    cgltf_options options{};
    cgltf_data* parsed = nullptr;
    check(cgltf_parse_file(&options,source.string().c_str(),&parsed),"Parsing glTF");
    std::unique_ptr<cgltf_data,decltype(&cgltf_free)> data(parsed,cgltf_free);
    if (data->extensions_required_count) throw std::invalid_argument("Required glTF extensions are unsupported");
    check(cgltf_load_buffers(&options,data.get(),source.string().c_str()),"Loading glTF buffers");
    check(cgltf_validate(data.get()),"Validating glTF");
    for (size_t i=0;i<data->buffer_views_count;++i)
        if (data->buffer_views[i].has_meshopt_compression)
            throw std::invalid_argument("Compressed glTF buffer views are unsupported");

    Asset asset;
    if (data->asset.extras.data) {
        auto extras = Json::parse(data->asset.extras.data);
        if (extras.contains("collision")) asset.collision = read_collision(extras.at("collision"));
    }

    Import importer{source.parent_path(),{}};
    std::vector<const cgltf_node*> nodes;
    const auto* scene = data->scene;
    if (!scene && data->scenes_count) scene = &data->scenes[0];
    if (scene) {
        for (size_t i=0;i<scene->nodes_count;++i) nodes.push_back(scene->nodes[i]);
    } else {
        for (size_t i=0;i<data->nodes_count;++i)
            if (!data->nodes[i].parent) nodes.push_back(&data->nodes[i]);
    }
    for (size_t i=0;i<nodes.size();++i) {
        const auto* node = nodes[i];
        for (size_t child=0;child<node->children_count;++child) nodes.push_back(node->children[child]);
        if (!node->mesh) continue;
        if (node->skin || node->has_mesh_gpu_instancing)
            throw std::invalid_argument("Expected a rigid glTF mesh node");
        float transform[16];
        cgltf_node_transform_world(node,transform);
        for (size_t j=0;j<node->mesh->primitives_count;++j)
            asset.visuals.push_back(importer.read_primitive(node->mesh->primitives[j],transform));
    }
    if (asset.visuals.empty()) throw std::invalid_argument("glTF asset has no triangles in its scene");
    return asset;
}
}

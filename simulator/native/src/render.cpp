#include "render.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <future>
#include <jpeglib.h>
#include <setjmp.h>
#include <stdexcept>

namespace sim {
ImageRequest image_request(const Json& options) {
    if (!options.is_object()) throw std::invalid_argument("Image options must be an object");
    if (options.value("actorId",std::string("chaser")) != "chaser" ||
        options.value("cameraId",std::string("front_camera")) != "front_camera")
        throw std::invalid_argument("Only chaser/front_camera is supported");
    if (options.value("imageFormat",std::string("image/jpeg")) != "image/jpeg")
        throw std::invalid_argument("Only image/jpeg is supported");
    ImageRequest request;
    if (options.contains("width")) request.width = static_cast<int>(std::round(number(options,"width",80,640)));
    if (options.contains("height")) request.height = static_cast<int>(std::round(number(options,"height",60,480)));
    if (options.contains("quality")) request.quality = number(options,"quality",.4,.9);
    return request;
}
struct Ray { double x,y,z; };
struct RenderBox { Box box; double cosine, sine; };
struct Hit { double distance; int material; double light, u, v; };
static uint32_t noise(int x, int z, uint32_t seed) {
    uint32_t n = uint32_t(x)*374761393u + uint32_t(z)*668265263u + seed*2246822519u;
    n = (n^(n>>13))*1274126177u;
    return n^(n>>16);
}
static void intersect(Ray origin, Ray ray, const RenderBox& box, double near, Hit& hit) {
    const auto& b = box.box;
    double x = origin.x-b.x, z = origin.z-b.z;
    std::array<double,3> local_origin{x*box.cosine-z*box.sine, origin.y, x*box.sine+z*box.cosine};
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
    hit = {entry,b.boundary ? 2 : 1,axis == 1 ? 1.08 : axis == 0 ? .80 : .96,
        local_origin[0]+entry*local_ray[0]+local_origin[2]+entry*local_ray[2],
        origin.y+entry*ray.y};
}
std::vector<unsigned char> render_rgb(const World& world, int width, int height) {
    if (width < 1 || height < 1 || width > 640 || height > 480) throw std::invalid_argument("Invalid render dimensions");
    const auto& c = world.config["camera"];
    double pitch = c["pitchDownRadians"], cp = std::cos(pitch), sp = std::sin(pitch);
    double tan_fov = std::tan(c["verticalFovDegrees"].get<double>()*pi/360);
    double aspect = double(width)/height, near = c["near"], far = c["far"];
    double distortion = c["barrelDistortion"], vignette = c["vignette"];
    Ray origin{world.position.x,c["height"],world.position.z};
    Vec d = world.direction;
    const auto& material = world.config["materials"];
    uint32_t seed = material["seed"];
    double scale = material["textureScale"];
    double half_columns = world.config["map"]["columns"].get<double>()/2;
    double half_rows = world.config["map"]["rows"].get<double>()/2;
    std::array<std::array<int,3>,3> colors{material["floor"].get<std::array<int,3>>(),
        material["obstacle"].get<std::array<int,3>>(),material["wall"].get<std::array<int,3>>()};
    std::vector<RenderBox> boxes;
    for (const auto& b : world.boxes) boxes.push_back({b,std::cos(b.rotation),std::sin(b.rotation)});
    std::vector<unsigned char> rgb(size_t(width)*height*3);
    auto rows = [&](int begin, int end) {
        for (int y=begin;y<end;++y) for (int x=0;x<width;++x) {
            double nx = 2*(x+.5)/width-1, ny = 1-2*(y+.5)/height;
            double r2 = nx*nx+ny*ny;
            double distortion_scale = 1+distortion*r2;
            double px = nx*aspect*tan_fov*distortion_scale, py = ny*tan_fov*distortion_scale;
            Ray ray{d.x*(cp+py*sp)-d.z*px, -sp+py*cp, d.z*(cp+py*sp)+d.x*px};
            Hit hit{far,-1,1,0,0};
            if (ray.y < -1e-12) {
                double distance = -origin.y/ray.y;
                double gx = origin.x+distance*ray.x, gz = origin.z+distance*ray.z;
                if (distance >= near && distance < far && std::abs(gx) <= half_columns && std::abs(gz) <= half_rows)
                    hit = {distance,0,1,gx,gz};
            }
            for (const auto& b : boxes) intersect(origin,ray,b,near,hit);
            double brightness = 1-vignette*std::min(1.0,r2/2);
            auto color = hit.material < 0 ? std::array<int,3>{216,214,207} : colors[hit.material];
            if (hit.material >= 0) {
                brightness *= hit.light;
                if (hit.material < 2) {
                    uint32_t grain = noise(int(std::floor(hit.u*80/scale)),int(std::floor(hit.v*80/scale)),seed);
                    brightness *= .92 + .16*(grain&255)/255.0;
                }
            }
            for (int channel=0;channel<3;++channel)
                rgb[(size_t(y)*width+x)*3+channel] = static_cast<unsigned char>(std::clamp(color[channel]*brightness,0.0,255.0));
        }
    };
    // Independent scanlines; deterministic output regardless of task scheduling.
    std::array<std::future<void>,4> tasks;
    for (int i=0;i<4;++i) tasks[i] = std::async(std::launch::async,rows,height*i/4,height*(i+1)/4);
    for (auto& task : tasks) task.get();
    return rgb;
}
struct JpegError { jpeg_error_mgr manager; jmp_buf jump; };
static void jpeg_failure(j_common_ptr pointer) {
    auto* error = reinterpret_cast<JpegError*>(pointer->err);
    longjmp(error->jump,1);
}
std::vector<unsigned char> encode_jpeg(const std::vector<unsigned char>& rgb, const ImageRequest& request) {
    jpeg_compress_struct encoder{};
    JpegError error{};
    encoder.err = jpeg_std_error(&error.manager);
    error.manager.error_exit = jpeg_failure;
    unsigned char* output = nullptr;
    unsigned long size = 0;
    if (setjmp(error.jump)) {
        jpeg_destroy_compress(&encoder); std::free(output);
        throw std::runtime_error("JPEG encoding failed");
    }
    jpeg_create_compress(&encoder); jpeg_mem_dest(&encoder,&output,&size);
    encoder.image_width = request.width; encoder.image_height = request.height;
    encoder.input_components = 3; encoder.in_color_space = JCS_RGB;
    jpeg_set_defaults(&encoder); jpeg_set_quality(&encoder,int(std::round(request.quality*100)),TRUE);
    jpeg_start_compress(&encoder,TRUE);
    while (encoder.next_scanline < encoder.image_height) {
        auto* row = const_cast<unsigned char*>(rgb.data()+encoder.next_scanline*request.width*3);
        jpeg_write_scanlines(&encoder,&row,1);
    }
    jpeg_finish_compress(&encoder);
    std::vector<unsigned char> result(output,output+size);
    jpeg_destroy_compress(&encoder); std::free(output); return result;
}
std::string base64(const std::vector<unsigned char>& bytes) {
    static constexpr char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string output; output.reserve((bytes.size()+2)/3*4);
    for (size_t i=0;i<bytes.size();i+=3) {
        uint32_t value = uint32_t(bytes[i])<<16;
        if (i+1<bytes.size()) value |= uint32_t(bytes[i+1])<<8;
        if (i+2<bytes.size()) value |= bytes[i+2];
        output += alphabet[value>>18]; output += alphabet[(value>>12)&63];
        output += i+1<bytes.size() ? alphabet[(value>>6)&63] : '=';
        output += i+2<bytes.size() ? alphabet[value&63] : '=';
    }
    return output;
}
Json sensor_image(const World& world, const ImageRequest& request) {
    auto bytes = encode_jpeg(render_rgb(world,request.width,request.height),request);
    return {{"image",{{"contentType","image/jpeg"},{"rendererId","native-software-v1"},
        {"width",request.width},{"height",request.height},{"dataUrl","data:image/jpeg;base64,"+base64(bytes)}}}};
}
}

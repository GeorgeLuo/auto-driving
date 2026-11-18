#include "sensor/camera.hpp"
#include "core/overscan.hpp"
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace sim {
struct CameraSampler::SamplingMap {
    struct Sample {
        size_t top_left, bottom_left;
        double fx, fy, gain;
    };
    ImageSize size;
    std::vector<Sample> pixels;

    SamplingMap(ImageSize size, const CameraEffects& effects)
        : size(size), pixels(size_t(size.width)*size.height) {
        const int width = size.width, height = size.height;
        const OverscanAxis horizontal(width,height,effects.barrel_distortion);
        const OverscanAxis vertical(height,width,effects.barrel_distortion);
        for (int y=0;y<height;++y) for (int x=0;x<width;++x) {
            double nx=2*(x+.5)/width-1, ny=2*(y+.5)/height-1;
            double r2=nx*nx+ny*ny, factor=1+effects.barrel_distortion*r2;
            // The overscan covers these rays. Clamp only corner roundoff.
            double sx=std::clamp(horizontal.sample_position((.5+.5*nx*factor)*width-.5),0.0,double(width-1));
            double sy=std::clamp(vertical.sample_position((.5+.5*ny*factor)*height-.5),0.0,double(height-1));
            int x0=int(sx), y0=int(sy), y1=std::min(y0+1,height-1);
            double fx=sx-x0, fy=sy-y0;
            double t=std::clamp((r2-.18)/.82,0.0,1.0);
            double gain=1-effects.vignette*t*t*(3-2*t);
            pixels[size_t(y)*width+x] = {(size_t(y0)*width+x0)*3,(size_t(y1)*width+x0)*3,fx,fy,gain};
        }
    }
};

std::shared_ptr<const CameraSampler::SamplingMap> CameraSampler::sampling_map(ImageSize size) const {
    const std::lock_guard<std::mutex> guard(mutex_);
    if (!samples_ || samples_->size.width != size.width || samples_->size.height != size.height)
        samples_ = std::make_shared<const SamplingMap>(size,effects_);
    return samples_;
}

std::vector<unsigned char> CameraSampler::apply(const LinearRgbImage& image) const {
    const auto [width,height] = image.size;
    if (width < 1 || height < 1 || image.channels.size() != size_t(width)*height*3)
        throw std::invalid_argument("Invalid linear RGB image");
    const auto map = sampling_map(image.size);
    // Preserve the native camera's code-value quantization before lens sampling.
    std::vector<unsigned char> source(image.channels.size()), rgb(image.channels.size());
    std::transform(image.channels.begin(),image.channels.end(),source.begin(),[](double channel) {
        return static_cast<unsigned char>(std::clamp(channel,0.0,255.0));
    });
    for (size_t i=0;i<map->pixels.size();++i) {
        const auto& sample = map->pixels[i];
        const double fx = sample.fx, fy = sample.fy;
        // The right sample has zero weight at integer positions, including
        // the clamped right edge where there is no next column.
        const size_t right = fx == 0 ? 0 : 3;
        const auto* top = source.data()+sample.top_left;
        const auto* bottom = source.data()+sample.bottom_left;
        for (int c=0;c<3;++c) {
            double value=(1-fy)*((1-fx)*top[c]+fx*top[right+c])
                         +fy*((1-fx)*bottom[c]+fx*bottom[right+c]);
            rgb[i*3+c]=static_cast<unsigned char>(std::round(value*sample.gain));
        }
    }
    return rgb;
}
}

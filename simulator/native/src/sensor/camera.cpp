#include "sensor/camera.hpp"
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace sim {
std::vector<unsigned char> apply_camera(const LinearRgbImage& image, const CameraEffects& effects) {
    const int width = image.size.width, height = image.size.height;
    if (width < 1 || height < 1 || image.channels.size() != size_t(width)*height*3)
        throw std::invalid_argument("Invalid linear RGB image");
    // Preserve the native camera's code-value quantization before lens sampling.
    std::vector<unsigned char> source(image.channels.size()), rgb(image.channels.size());
    std::transform(image.channels.begin(),image.channels.end(),source.begin(),[](double channel) {
        return static_cast<unsigned char>(std::clamp(channel,0.0,255.0));
    });
    // Metrics UI rendering/sensor.ts: radial resampling with clamp-to-edge
    // linear filtering, followed by smoothstep(0.18, 1.0, radiusSquared).
    for (int y=0;y<height;++y) for (int x=0;x<width;++x) {
        double nx=2*(x+.5)/width-1, ny=2*(y+.5)/height-1;
        double r2=nx*nx+ny*ny, factor=1+effects.barrel_distortion*r2;
        double sx=std::clamp((.5+.5*nx*factor)*width-.5,0.0,double(width-1));
        double sy=std::clamp((.5+.5*ny*factor)*height-.5,0.0,double(height-1));
        int x0=int(sx), y0=int(sy), x1=std::min(x0+1,width-1), y1=std::min(y0+1,height-1);
        double fx=sx-x0, fy=sy-y0;
        double t=std::clamp((r2-.18)/.82,0.0,1.0);
        double gain=1-effects.vignette*t*t*(3-2*t);
        for (int c=0;c<3;++c) {
            auto at=[&](int xx,int yy){return source[(size_t(yy)*width+xx)*3+c];};
            double value=(1-fy)*((1-fx)*at(x0,y0)+fx*at(x1,y0))
                         +fy*((1-fx)*at(x0,y1)+fx*at(x1,y1));
            rgb[(size_t(y)*width+x)*3+c]=static_cast<unsigned char>(std::round(value*gain));
        }
    }
    return rgb;
}
}

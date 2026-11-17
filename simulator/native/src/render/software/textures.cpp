#include "render/software/textures.hpp"
#include "core/noise.hpp"

namespace sim::software {
namespace {
double fraction(double x) { return x-std::floor(x); }
double wave(double x) { return 1-std::abs(2*fraction(x)-1); }
double mix(double a, double b, double t) { return a+(b-a)*t; }
double smooth(double t) { return t*t*(3-2*t); }

double random_cell(double x, double y, uint32_t seed) {
    auto i = static_cast<uint32_t>(static_cast<int64_t>(std::floor(x)));
    auto j = static_cast<uint32_t>(static_cast<int64_t>(std::floor(y)));
    return (noise(i,j,seed)&255)/255.0;
}

double mottling(double x, double y, uint32_t seed) {
    double u = smooth(fraction(x)), v = smooth(fraction(y));
    return mix(mix(random_cell(x,y,seed),random_cell(x+1,y,seed),u),
               mix(random_cell(x,y+1,seed),random_cell(x+1,y+1,seed),u),v);
}

double cardboard_ink(UV uv, uint32_t seed) {
    if (uv.u < .17 || uv.u > .82 || uv.v < .23 || uv.v > .66) return 0;
    if (uv.v < .29) return .65;
    if (uv.u < .43 && uv.v < .45) return fraction(uv.v*44) < .28 ? .58 : 0;
    if (uv.u > .52 && uv.v > .40 && uv.v < .61)
        return random_cell(uv.u*110,0,seed) > .34 ? .86 : 0;
    return 0;
}

int texel_index(double coordinate, int size, TextureWrap wrap) {
    switch (wrap) {
    case TextureWrap::repeat: coordinate = fraction(coordinate); break;
    case TextureWrap::clamp: coordinate = std::clamp(coordinate,0.,1.); break;
    case TextureWrap::mirror: coordinate = wave(coordinate*.5); break;
    }
    return std::min(static_cast<int>(coordinate*size),size-1);
}

std::array<double,3> sample_image(const Texture& texture, UV uv) {
    const auto& image = *texture.image;
    int x = texel_index(uv.u/texture.scale,image.width,texture.wrap_u);
    int y = texel_index(uv.v/texture.scale,image.height,texture.wrap_v);
    size_t index = (size_t(y)*image.width+x)*3;
    return {double(image.rgb[index]),double(image.rgb[index+1]),double(image.rgb[index+2])};
}
}

std::array<double,3> texture_color(const Material& material, const std::array<int,3>& base,
                                  const Surface& surface, Vec3 view_ray, double brightness) {
    const auto& texture = material.texture;
    std::array<double,3> color{double(base[0]),double(base[1]),double(base[2])};
    if (texture.kind == TextureKind::none)
        return {base[0]*brightness,base[1]*brightness,base[2]*brightness};

    if (texture.kind == TextureKind::grain) {
        auto grain = surface.grain_uv;
        brightness *= .92 + .16*random_cell(grain.u*80/texture.scale,grain.v*80/texture.scale,texture.seed);
        return {base[0]*brightness,base[1]*brightness,base[2]*brightness};
    }
    if (texture.kind == TextureKind::image) {
        const auto texel = sample_image(texture,surface.uv);
        for (size_t i=0;i<3;++i) color[i] *= texel[i]/255;
    } else {
        double u = surface.surface_uv.u/texture.scale, v = surface.surface_uv.v/texture.scale;
        double phase = random_cell(0,0,texture.seed);
        double variation = 1;
        switch (texture.kind) {
        case TextureKind::carpet:
            variation = .67+.40*mottling(u*3.5,v*3.5,texture.seed)+.25*random_cell(u*145,v*145,texture.seed+1);
            break;
        case TextureKind::cardboard: {
            variation = .87+.14*wave(u*63+phase)+.09*random_cell(u*165,v*110,texture.seed);
            double decal = texture.print && !surface.top ? cardboard_ink(surface.uv,texture.seed) : 0;
            for (double& channel : color) channel = mix(channel,27.,decal*texture.strength);
            if (texture.tape && std::abs(surface.uv.u-.46-.08*phase) < .075) {
                constexpr std::array<double,3> tape{213,174,105};
                for (size_t i=0;i<3;++i) color[i] = mix(color[i],tape[i],.9*texture.strength);
                variation = .94+.10*std::abs(dot(surface.normal,unit(view_ray)));
            }
            break;
        }
        case TextureKind::leather: {
            double bend = mottling(u*5,v*5,texture.seed);
            double crease = std::max(0.,wave(v*23+.9*bend)-.82)*3.5;
            double grazing = 1-std::abs(dot(surface.normal,unit(view_ray)));
            double sheen = .35*grazing*grazing;
            variation = .93-.30*crease+.13*random_cell(u*120,v*120,texture.seed)+sheen;
            break;
        }
        case TextureKind::wood: {
            double growth = wave(u*25+2.1*mottling(u*5,v*1.9,texture.seed)+phase);
            variation = .70+.33*growth+.16*mottling(u*35,v*3,texture.seed+1);
            break;
        }
        case TextureKind::fabric: {
            double warp = u*104+phase, weft = v*110+phase;
            bool over = fraction((std::floor(warp)+std::floor(weft))*.5) == 0;
            variation = .76+.32*wave(over ? warp : weft)+.13*random_cell(warp,weft,texture.seed);
            break;
        }
        case TextureKind::patterned_rug: {
            UV uv = surface.uv;
            double edge = std::min({uv.u,1-uv.u,uv.v,1-uv.v});
            double diamond = std::abs(fraction(u*1.7+phase)-.5)+std::abs(fraction(v*1.7+phase)-.5);
            double band = edge < .035 ? .88 : edge < .075 ? .16 : edge < .10 ? .92 :
                diamond < .23 ? .85 : diamond < .34 ? .30 : diamond < .46 ? .65 : 0;
            constexpr std::array<double,3> pattern{237,213,165};
            for (size_t i=0;i<3;++i) color[i] = mix(color[i],pattern[i],band*texture.strength);
            variation = .86+.25*random_cell(u*120,v*120,texture.seed);
            break;
        }
        default: break;
        }
        brightness *= mix(1,variation,texture.strength);
    }
    return {color[0]*brightness,color[1]*brightness,color[2]*brightness};
}
}

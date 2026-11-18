#pragma once
#include <algorithm>

namespace sim {
// Coordinates are in undistorted pixel units. Keep the middle two thirds
// of each axis on its original lattice. Spread the outer sixth on either
// side until the last pixel centre reaches the most distorted corner.
class OverscanAxis {
public:
    OverscanAxis(int length, int other_length, double distortion)
        : first_((length-1)/6.), last_(length-1-first_) {
        const double edge = 1-1./length, other_edge = 1-1./other_length;
        // The half-axis is three times the margin, so the extra distance
        // at the corner adds three times its radial expansion to the pitch.
        pitch_ = 1+3*distortion*(edge*edge+other_edge*other_edge);
    }

    double ray_position(double pixel) const {
        return pixel + (pixel-std::clamp(pixel,first_,last_))*(pitch_-1);
    }

    double sample_position(double ray) const {
        const double edge = std::clamp(ray,first_,last_);
        return edge + (ray-edge)/pitch_;
    }

private:
    double first_, last_, pitch_;
};
}

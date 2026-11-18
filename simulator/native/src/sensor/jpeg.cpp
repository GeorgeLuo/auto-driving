#include "sensor/jpeg.hpp"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <jpeglib.h>
#include <memory>
#include <setjmp.h>
#include <stdexcept>

namespace sim {
struct JpegError { jpeg_error_mgr manager; jmp_buf jump; };
struct JpegEncoder {
    jpeg_compress_struct encoder{};
    JpegError error{};
    unsigned char* output = nullptr;
    unsigned long byte_count = 0;

    ~JpegEncoder() {
        jpeg_destroy_compress(&encoder);
        std::free(output);
    }
};

static void jpeg_failure(j_common_ptr pointer) {
    auto* error = reinterpret_cast<JpegError*>(pointer->err);
    longjmp(error->jump,1);
}
std::vector<unsigned char> encode_jpeg(const std::vector<unsigned char>& rgb, ImageSize size, double quality) {
    // libjpeg changes the destination and compressor after setjmp. Keep them off
    // the stack so both remain valid if its error handler jumps back here.
    auto state = std::make_unique<JpegEncoder>();
    auto& encoder = state->encoder;
    auto& error = state->error;
    encoder.err = jpeg_std_error(&error.manager);
    error.manager.error_exit = jpeg_failure;
    if (setjmp(error.jump)) throw std::runtime_error("JPEG encoding failed");
    jpeg_create_compress(&encoder);
    jpeg_mem_dest(&encoder,&state->output,&state->byte_count);
    encoder.image_width = size.width; encoder.image_height = size.height;
    encoder.input_components = 3; encoder.in_color_space = JCS_RGB;
    jpeg_set_defaults(&encoder); jpeg_set_quality(&encoder,int(std::round(quality*100)),TRUE);
    jpeg_start_compress(&encoder,TRUE);
    while (encoder.next_scanline < encoder.image_height) {
        auto* row = const_cast<unsigned char*>(rgb.data()+encoder.next_scanline*size.width*3);
        jpeg_write_scanlines(&encoder,&row,1);
    }
    jpeg_finish_compress(&encoder);
    return {state->output,state->output+state->byte_count};
}
}

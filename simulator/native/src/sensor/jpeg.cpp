#include "sensor/jpeg.hpp"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <jpeglib.h>
#include <setjmp.h>
#include <stdexcept>

namespace sim {
struct JpegError { jpeg_error_mgr manager; jmp_buf jump; };
static void jpeg_failure(j_common_ptr pointer) {
    auto* error = reinterpret_cast<JpegError*>(pointer->err);
    longjmp(error->jump,1);
}
std::vector<unsigned char> encode_jpeg(const std::vector<unsigned char>& rgb, ImageSize size, double quality) {
    jpeg_compress_struct encoder{};
    JpegError error{};
    encoder.err = jpeg_std_error(&error.manager);
    error.manager.error_exit = jpeg_failure;
    unsigned char* output = nullptr;
    unsigned long byte_count = 0;
    if (setjmp(error.jump)) {
        jpeg_destroy_compress(&encoder); std::free(output);
        throw std::runtime_error("JPEG encoding failed");
    }
    jpeg_create_compress(&encoder); jpeg_mem_dest(&encoder,&output,&byte_count);
    encoder.image_width = size.width; encoder.image_height = size.height;
    encoder.input_components = 3; encoder.in_color_space = JCS_RGB;
    jpeg_set_defaults(&encoder); jpeg_set_quality(&encoder,int(std::round(quality*100)),TRUE);
    jpeg_start_compress(&encoder,TRUE);
    while (encoder.next_scanline < encoder.image_height) {
        auto* row = const_cast<unsigned char*>(rgb.data()+encoder.next_scanline*size.width*3);
        jpeg_write_scanlines(&encoder,&row,1);
    }
    jpeg_finish_compress(&encoder);
    std::vector<unsigned char> result(output,output+byte_count);
    jpeg_destroy_compress(&encoder); std::free(output); return result;
}
}

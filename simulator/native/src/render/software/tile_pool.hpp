#pragma once
#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <exception>
#include <functional>
#include <mutex>
#include <thread>
#include <vector>

namespace sim::software {
// The renderer submits one frame at a time. Each tile has one writer, and
// run() waits for all workers before returning or propagating a failure.
class TilePool {
public:
    explicit TilePool(unsigned count);
    ~TilePool();
    void run(std::size_t count, std::function<void(std::size_t)> tile);

private:
    void work();
    void stop();

    std::vector<std::thread> workers_;
    std::mutex mutex_;
    std::condition_variable ready_, finished_;
    std::function<void(std::size_t)> tile_;
    std::exception_ptr failure_;
    std::atomic<std::size_t> next_{0};
    std::size_t generation_ = 0, count_ = 0, pending_ = 0;
    bool stopping_ = false;
};
}

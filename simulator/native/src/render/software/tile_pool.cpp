#include "render/software/tile_pool.hpp"
#include <utility>

namespace sim::software {
TilePool::TilePool(unsigned count) {
    if (count == 1) return;
    workers_.reserve(count);
    try {
        for (unsigned i=0;i<count;++i) workers_.emplace_back([this] { work(); });
    } catch (...) {
        stop();
        throw;
    }
}

TilePool::~TilePool() { stop(); }

void TilePool::run(std::size_t count, std::function<void(std::size_t)> tile) {
    if (workers_.empty()) {
        for (std::size_t i=0;i<count;++i) tile(i);
        return;
    }
    std::unique_lock<std::mutex> lock(mutex_);
    tile_ = std::move(tile);
    failure_ = nullptr;
    next_.store(0,std::memory_order_relaxed);
    count_ = count;
    pending_ = workers_.size();
    ++generation_;
    ready_.notify_all();
    finished_.wait(lock,[&] { return pending_ == 0; });
    tile_ = {};
    if (failure_) std::rethrow_exception(failure_);
}

void TilePool::work() {
    std::unique_lock<std::mutex> lock(mutex_);
    std::size_t seen = 0;
    for (;;) {
        ready_.wait(lock,[&] { return stopping_ || seen != generation_; });
        if (stopping_) return;
        seen = generation_;
        lock.unlock();
        std::exception_ptr failure;
        try {
            for (;;) {
                const auto index = next_.fetch_add(1,std::memory_order_relaxed);
                if (index >= count_) break;
                tile_(index);
            }
        } catch (...) {
            failure = std::current_exception();
        }
        lock.lock();
        if (failure && !failure_) failure_ = failure;
        if (--pending_ == 0) finished_.notify_one();
    }
}

void TilePool::stop() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        stopping_ = true;
    }
    ready_.notify_all();
    for (auto& worker : workers_) worker.join();
}
}

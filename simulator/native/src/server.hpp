#pragma once
#include "protocol.hpp"
#include <atomic>
#include <memory>
#include <mutex>
#include <thread>
#include <unordered_map>
#include <ixwebsocket/IXWebSocketServer.h>

namespace sim {
class Server {
public:
    Server(int port, const Json& config);
    ~Server();
    void start();
private:
    struct Client {
        std::weak_ptr<ix::WebSocket> socket;
        std::mutex mutex;
        bool registered = false;
        Json metadata;
        std::string subscription, epoch;
        ImageRequest image;
        double rate = 30;
        uint64_t last_frame = 0, next_capture_us = 0, dropped = 0;
    };
    ix::WebSocketServer server_;
    World world_;
    std::mutex world_mutex_, clients_mutex_;
    std::unordered_map<ix::WebSocket*,std::shared_ptr<Client>> clients_;
    std::atomic<bool> stopped_{false};
    std::thread simulation_, camera_;
    World copy_world();
    void handle(const std::shared_ptr<Client>& client, ix::WebSocket& socket, const Json& request);
    void subscribe(Client& client, ix::WebSocket& socket, const Json& request);
    Json query(const Json& request);
    Json mutate(const Json& request);
    void simulate();
    void stream();
};
}

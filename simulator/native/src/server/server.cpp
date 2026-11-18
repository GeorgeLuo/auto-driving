#include "server/server.hpp"
#include <chrono>
#include <ctime>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace sim {
static std::string connected_at() {
    auto now = std::time(nullptr);
    std::tm utc{}; gmtime_r(&now,&utc);
    std::ostringstream text;
    text << std::put_time(&utc,"%Y-%m-%dT%H:%M:%S.000Z");
    return text.str();
}
static void send(ix::WebSocket& socket, const Json& response) {
    auto encoded = response.dump();
    if (encoded.size() > 1024*1024 || socket.bufferedAmount() > 1024*1024) {
        socket.close(1008,"backpressure_overflow"); return;
    }
    socket.sendText(encoded);
}
Server::Server(int port, World world, std::unique_ptr<Renderer> renderer)
    : server_(port,"127.0.0.1",128,32,5), world_(std::move(world)), renderer_(std::move(renderer)) {
    server_.disablePerMessageDeflate();
    server_.setOnConnectionCallback([this](std::weak_ptr<ix::WebSocket> weak, std::shared_ptr<ix::ConnectionState>) {
        auto socket = weak.lock();
        if (!socket) return;
        auto client = std::make_shared<Client>(); client->socket = weak;
        {
            std::lock_guard<std::mutex> guard(clients_mutex_);
            clients_[socket.get()] = client;
        }
        socket->setOnMessageCallback([this,client](const ix::WebSocketMessagePtr& event) {
            auto socket = client->socket.lock();
            if (!socket) return;
            if (event->type == ix::WebSocketMessageType::Close || event->type == ix::WebSocketMessageType::Error) {
                std::lock_guard<std::mutex> guard(clients_mutex_); clients_.erase(socket.get()); return;
            }
            if (event->type == ix::WebSocketMessageType::Open) {
                if (event->openInfo.uri != "/ws/control") socket->close(1008,"Use /ws/control");
                return;
            }
            if (event->type != ix::WebSocketMessageType::Message) return;
            std::lock_guard<std::mutex> guard(client->mutex);
            Json request = Json::object();
            try {
                if (event->binary || event->str.size() > 1024*1024) throw std::invalid_argument("Expected bounded JSON text");
                request = Json::parse(event->str);
                if (!request.is_object()) throw std::invalid_argument("Expected JSON object");
                handle(client,*socket,request);
            } catch (const std::exception& error) {
                auto response = message("error",request,{{"code","invalid_request"}});
                response["error"] = error.what(); send(*socket,response);
            }
        });
    });
}
Server::~Server() {
    stopped_ = true;
    if (simulation_.joinable()) simulation_.join();
    if (camera_.joinable()) camera_.join();
    server_.stop();
}
void Server::start() {
    auto result = server_.listen();
    if (!result.first) throw std::runtime_error(result.second);
    server_.start();
    simulation_ = std::thread(&Server::simulate,this);
    camera_ = std::thread(&Server::stream,this);
}
World Server::copy_world() {
    std::lock_guard<std::mutex> guard(world_mutex_); return world_;
}
Json Server::mutate(const Json& request) {
    std::lock_guard<std::mutex> guard(world_mutex_);
    const auto type = request.at("type").get<std::string>();
    Json result = Json::object();
    if (type == "play") world_.playing = true;
    else if (type == "pause") world_.playing = false;
    else if (type == "set_sidebar_app") {
        if (request.at("app") != "play") throw std::invalid_argument("Native simulator only exposes play");
    } else result = command(world_,request.at("commandId"),request.value("payload",Json::object()));
    return {{"command",type},{"result",result}};
}
Json Server::query(const Json& request) {
    auto world = copy_world();
    const auto id = request.at("queryId").get<std::string>();
    if (id == "atomic-evaluation-capture") {
        auto options = request.value("payload",Json::object());
        if (options.is_null()) options = Json::object();
        return {{"queryId",id},{"result",capture(world,*renderer_,image_request(options))}};
    }
    if (id == "native-state") return {{"queryId",id},{"result",world.native_state()}};
    throw std::invalid_argument("Unsupported query: " + id);
}
void Server::subscribe(Client& client, ix::WebSocket& socket, const Json& request) {
    if (!client.subscription.empty()) throw std::invalid_argument("Unsubscribe before subscribing again");
    if (request.value("dropPolicy",std::string("latest-frame")) != "latest-frame")
        throw std::invalid_argument("Native prototype supports latest-frame delivery only");
    auto image = image_request(request);
    double rate = request.contains("maxRateHz") ? number(request,"maxRateHz",1,30) : 30;
    auto world = copy_world();
    auto subscription = new_epoch();
    auto first = stream_frame(world,*renderer_,image,subscription,0);
    auto fingerprint = world.fingerprint();
    send(socket,message("play_camera_stream_result",request,{
        {"event","subscribed"},{"subscriptionId",subscription},{"cameraStream",{{"supported",true}}},
        {"playback",{{"advanced",false}}},{"preservation",{{"preserved",true},{"before",fingerprint},{"after",fingerprint}}},
        {"frame",std::move(first)}}));
    client.subscription = subscription; client.image = image; client.rate = rate;
    client.epoch = world.epoch; client.last_frame = world.frame;
    client.next_capture_us = monotonic_us()+uint64_t(1000000/rate); client.dropped = 0;
}
void Server::handle(const std::shared_ptr<Client>& client, ix::WebSocket& socket, const Json& request) {
    auto type = request.at("type").get<std::string>();
    if (type == "register") {
        if (request.at("role") != "agent") throw std::invalid_argument("Only agent registrations are supported");
        {
            std::lock_guard<std::mutex> guard(clients_mutex_);
            client->registered = true;
            client->metadata = {{"client",request.value("client",Json(nullptr))},
                                {"connectedAt",connected_at()}};
        }
        send(socket,message("ack",request,"registered as agent")); return;
    }
    if (!client->registered) throw std::invalid_argument("Register as agent first");
    if (type == "get_state") send(socket,message("state_update",request,copy_world().state()));
    else if (type == "get_play_debug") send(socket,message("play_debug",request,copy_world().debug()));
    else if (type == "get_play_game_usage") send(socket,message("play_game_usage",request,usage()));
    else if (type == "play_game_query") send(socket,message("play_game_query_result",request,query(request)));
    else if (type == "play" || type == "pause" || type == "set_sidebar_app" || type == "play_game_command")
        send(socket,message("ack",request,mutate(request)));
    else if (type == "play_camera_stream_subscribe") subscribe(*client,socket,request);
    else if (type == "play_camera_stream_unsubscribe") {
        if (client->subscription.empty() || request.at("subscriptionId") != client->subscription)
            throw std::invalid_argument("Unknown subscription");
        auto id = client->subscription; client->subscription.clear();
        send(socket,message("play_camera_stream_result",request,{{"event","ended"},{"subscriptionId",id},
            {"reason",{{"code","unsubscribed"},{"message","Unsubscribed"}}}}));
    } else if (type == "list_agents") {
        Json agents = Json::array();
        {
            std::lock_guard<std::mutex> guard(clients_mutex_);
            for (const auto& [pointer,other] : clients_)
                if (pointer != &socket && other->registered) agents.push_back(other->metadata);
        }
        send(socket,message("agent_list",request,{{"agents",agents}}));
    } else throw std::invalid_argument("Unsupported message type: " + type);
}
void Server::simulate() {
    auto previous = monotonic_us();
    double accumulated = 0;
    while (!stopped_) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
        auto now = monotonic_us();
        accumulated += double(now-previous)/1000000; previous = now;
        std::lock_guard<std::mutex> guard(world_mutex_);
        if (!world_.playing) { accumulated = 0; continue; }
        double dt = world_.next_step_interval();
        // Bounded catchup matches the browser loop's eight-step limit.
        accumulated = std::min(accumulated,8*dt);
        for (int i=0;i<8 && accumulated >= dt;++i) {
            world_.step(); accumulated -= dt;
            dt = world_.next_step_interval();
        }
    }
}
void Server::stream() {
    while (!stopped_) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
        std::vector<std::shared_ptr<Client>> clients;
        {
            std::lock_guard<std::mutex> guard(clients_mutex_);
            for (const auto& [pointer,client] : clients_) { (void)pointer; clients.push_back(client); }
        }
        for (auto& client : clients) {
            std::unique_lock<std::mutex> guard(client->mutex,std::try_to_lock);
            if (!guard || client->subscription.empty() || monotonic_us() < client->next_capture_us) continue;
            auto socket = client->socket.lock();
            if (!socket || socket->getReadyState() != ix::ReadyState::Open) continue;
            auto world = copy_world();
            if (world.epoch != client->epoch) {
                send(*socket,message("play_camera_stream_result",Json::object(),{{"event","ended"},
                    {"subscriptionId",client->subscription},{"reason",{{"code","session_changed"},{"message","Simulation epoch changed"}}}}));
                client->subscription.clear(); continue;
            }
            if (world.frame <= client->last_frame) continue;
            client->next_capture_us = monotonic_us()+uint64_t(1000000/client->rate);
            if (socket->bufferedAmount() > 0) { ++client->dropped; continue; }
            try {
                auto frame = stream_frame(world,*renderer_,client->image,client->subscription,client->dropped);
                send(*socket,message("play_camera_stream_frame",Json::object(),frame));
                client->last_frame = world.frame;
            } catch (const std::exception& error) {
                socket->close(1011,error.what()); client->subscription.clear();
            }
        }
    }
}
}

#pragma once
#include "scene/asset.hpp"
#include <filesystem>

namespace sim {
class AssetLoader {
public:
    virtual ~AssetLoader() = default;
    virtual bool supports(const std::filesystem::path& source) const = 0;
    virtual Asset load(const std::filesystem::path& source) const = 0;
};

class JsonAssetLoader final : public AssetLoader {
public:
    bool supports(const std::filesystem::path& source) const override;
    Asset load(const std::filesystem::path& source) const override;
};

class GltfAssetLoader final : public AssetLoader {
public:
    bool supports(const std::filesystem::path& source) const override;
    Asset load(const std::filesystem::path& source) const override;
};

AssetCatalog load_asset_catalog(const std::filesystem::path& directory);
}

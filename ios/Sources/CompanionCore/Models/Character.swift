// 角色详情 — GET /v1/ips/{id} 返回
// character 子对象对应项目约定的 Schema(见 docs/ips-api-requirements.md §3.1)
import Foundation

public struct Character: Codable, Identifiable, Hashable, Sendable {
    public let id: String
    public let name: String
    public let personalityTraits: [String]
    public let backstory: String
    public let speakingStyle: SpeakingStyle
    public let boundaries: [String]
    public let memorySeed: String
    public let voiceId: String?
    public let metadata: [String: String]

    public struct SpeakingStyle: Codable, Hashable, Sendable {
        public let tone: String?
        public let catchphrases: [String]?
        public let sentenceStyle: String?
    }

    enum CodingKeys: String, CodingKey {
        case id, name, backstory, boundaries, metadata
        case personalityTraits = "personality_traits"
        case speakingStyle = "speaking_style"
        case memorySeed = "memory_seed"
        case voiceId = "voice_id"
    }
}

public struct CharacterDetail: Codable, Identifiable, Hashable, Sendable {
    public let id: String
    public let name: String
    public let avatarURL: URL
    public let previewURL: URL
    public let tags: [String]
    public let character: Character
    public let license: License
    public let assets: Assets

    public struct License: Codable, Hashable, Sendable {
        public let type: String
        public let scope: String
        public let allowedPlatforms: [String]
        public let downloadQuota: Int
        public let downloadUsed: Int
        public let expiresAt: Date?
        public let canOfflineUse: Bool

        enum CodingKeys: String, CodingKey {
            case type, scope
            case allowedPlatforms = "allowed_platforms"
            case downloadQuota = "download_quota"
            case downloadUsed = "download_used"
            case expiresAt = "expires_at"
            case canOfflineUse = "can_offline_use"
        }
    }

    public struct Assets: Codable, Hashable, Sendable {
        public let preview2kURL: URL
        public let preview4kURL: URL?
        public let voiceSampleURL: URL?
        public let expressionSetURL: URL?

        enum CodingKeys: String, CodingKey {
            case preview2kURL = "preview_2k_url"
            case preview4kURL = "preview_4k_url"
            case voiceSampleURL = "voice_sample_url"
            case expressionSetURL = "expression_set_url"
        }
    }

    enum CodingKeys: String, CodingKey {
        case id, name, tags, character, license, assets
        case avatarURL = "avatar_url"
        case previewURL = "preview_url"
    }
}
